"""Hermes Agent integration for pre-council analysis and post-council review."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from council_common import (
    PATCHES_DIR,
    REVIEWS_DIR,
    CouncilBrief,
    LLMClient,
    PERSONAS_DIR,
    PROJECT_ROOT,
    extract_cat_lines,
    extract_json_object,
    format_prompt,
    get_cat_profiles,
    get_display_name,
    load_council_memory,
    load_cat_system_prompt,
    read_prompt,
    read_text,
    save_council_memory,
    timestamp_slug,
    week_stamp,
)

logger = logging.getLogger(__name__)


def hermes_home(config: dict[str, Any]) -> Path:
    return Path(config["hermes"]["home"]).expanduser()


def hermes_profile_dir(config: dict[str, Any], profile: str) -> Path:
    return hermes_home(config) / "profiles" / profile


def is_hermes_available(config: dict[str, Any]) -> bool:
    if not config.get("hermes", {}).get("enabled", False):
        return False
    cli = config["hermes"]["cli_command"]
    return shutil.which(cli) is not None


def invoke_hermes_profile(config: dict[str, Any], profile: str, prompt: str) -> str:
    hermes_cfg = config["hermes"]
    command = [hermes_cfg["cli_command"], "-p", profile, "-z", prompt]
    toolsets = hermes_cfg.get("toolsets", "").strip()
    if toolsets:
        command.extend(["-t", toolsets])

    logger.info("Invoking Hermes profile %s", profile)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=hermes_cfg.get("timeout_seconds", 900),
        cwd=PROJECT_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Hermes profile '{profile}' failed: {stderr}")
    return completed.stdout.strip()


def fallback_brief(llm: LLMClient, raw_logs: str) -> CouncilBrief:
    system_prompt = read_prompt("summarize_fallback_system.md")
    user_prompt = format_prompt(
        read_prompt("summarize_fallback_user.md"),
        raw_logs=raw_logs,
    )
    summary = llm.chat(system_prompt, user_prompt)
    return CouncilBrief(
        summary=summary,
        memory_callbacks="",
        memory_updates="",
        source="llm-fallback",
    )


def pre_council_analysis(
    config: dict[str, Any],
    llm: LLMClient,
    raw_logs: str,
) -> CouncilBrief:
    if not is_hermes_available(config):
        logger.warning("Hermes unavailable; using LLM summarisation fallback.")
        return fallback_brief(llm, raw_logs)

    memory = load_council_memory()
    prompt = (
        f"{read_prompt('chronicler_system.md')}\n\n"
        f"{format_prompt(read_prompt('chronicler_user.md'), memory=memory, raw_logs=raw_logs)}"
    )
    profile = config["hermes"]["chronicler_profile"]

    try:
        response = invoke_hermes_profile(config, profile, prompt)
        payload = extract_json_object(response)
        brief = CouncilBrief(
            summary=str(payload.get("summary", "")).strip(),
            memory_callbacks=str(payload.get("memory_callbacks", "")).strip(),
            memory_updates=str(payload.get("memory_updates", "")).strip(),
            source=f"hermes:{profile}",
        )
    except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
        logger.exception("Hermes chronicler failed; falling back to LLM: %s", exc)
        return fallback_brief(llm, raw_logs)

    if brief.memory_updates:
        updated_memory = f"{memory}\n\n## {week_stamp()}\n{brief.memory_updates}".strip()
        save_council_memory(updated_memory)

    return brief


def build_cat_extractions(config: dict[str, Any], transcript: str) -> str:
    sections: list[str] = []
    for slug in get_cat_profiles(config):
        display_name = get_display_name(config, slug)
        lines = extract_cat_lines(transcript, display_name)
        sections.append(f"### {display_name}\n{lines}")
    return "\n\n".join(sections)


def run_cat_self_reviews(
    config: dict[str, Any],
    summary: str,
    transcript: str,
) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    if not is_hermes_available(config):
        return reviews

    for slug in get_cat_profiles(config):
        display_name = get_display_name(config, slug)
        prompt = format_prompt(
            read_prompt("cat_self_review.md"),
            soul=load_cat_system_prompt(slug).split("\n\n", 1)[0],
            council_voice=read_text(PERSONAS_DIR / slug / "council-voice.md"),
            summary=summary,
            cat_lines=extract_cat_lines(transcript, display_name),
        )
        try:
            response = invoke_hermes_profile(config, slug, prompt)
            reviews[slug] = extract_json_object(response)
        except (RuntimeError, json.JSONDecodeError) as exc:
            logger.warning("Cat self-review failed for %s: %s", slug, exc)
            reviews[slug] = {"error": str(exc)}
    return reviews


def apply_council_voice_patch(cat_slug: str, patch_markdown: str) -> None:
    if not patch_markdown.strip():
        return
    voice_path = PERSONAS_DIR / cat_slug / "council-voice.md"
    current = read_text(voice_path)
    merged = f"{current}\n\n## Review update ({week_stamp()})\n{patch_markdown.strip()}".strip()
    voice_path.write_text(merged + "\n", encoding="utf-8")


def save_review_artifacts(
    review_payload: dict[str, Any],
    cat_self_reviews: dict[str, Any],
) -> Path:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)

    review_path = REVIEWS_DIR / f"{timestamp_slug()}.json"
    review_path.write_text(
        json.dumps(
            {
                "week": week_stamp(),
                "curator_review": review_payload,
                "cat_self_reviews": cat_self_reviews,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    soul_candidates = review_payload.get("soul_patch_candidates", {})
    if isinstance(soul_candidates, dict):
        for slug, patch in soul_candidates.items():
            if patch and str(patch).strip():
                patch_path = PATCHES_DIR / f"{slug}-{timestamp_slug()}.md"
                patch_path.write_text(str(patch).strip() + "\n", encoding="utf-8")

    cats = review_payload.get("cats", {})
    if isinstance(cats, dict):
        for slug, cat_review in cats.items():
            if not isinstance(cat_review, dict):
                continue
            patch = str(cat_review.get("council_voice_patch", "")).strip()
            apply_council_voice_patch(slug, patch)

    return review_path


def post_council_review(
    config: dict[str, Any],
    llm: LLMClient,
    summary: str,
    transcript: str,
) -> Path | None:
    cat_extractions = build_cat_extractions(config, transcript)
    cat_self_reviews = run_cat_self_reviews(config, summary, transcript)

    if is_hermes_available(config):
        prompt = (
            f"{read_prompt('curator_system.md')}\n\n"
            f"{format_prompt(read_prompt('curator_user.md'), summary=summary, transcript=transcript, cat_extractions=cat_extractions)}"
        )
        profile = config["hermes"]["curator_profile"]
        try:
            response = invoke_hermes_profile(config, profile, prompt)
            review_payload = extract_json_object(response)
            review_path = save_review_artifacts(review_payload, cat_self_reviews)
            sync_personas_from_hermes(config)
            return review_path
        except (RuntimeError, json.JSONDecodeError) as exc:
            logger.exception("Hermes curator failed: %s", exc)

    fallback_payload: dict[str, Any] = {
        "overall_notes": "Hermes curator unavailable; saved per-cat self reviews only.",
        "cats": {},
        "soul_patch_candidates": {},
    }
    for slug, review in cat_self_reviews.items():
        if "error" in review:
            continue
        fallback_payload["cats"][slug] = review
    if fallback_payload["cats"]:
        return save_review_artifacts(fallback_payload, cat_self_reviews)

    logger.warning("No post-council review artifacts were produced.")
    return None


def _read_hermes_skill_markdown(skill_dir: Path) -> str:
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8").strip()
    return ""


def sync_personas_from_hermes(config: dict[str, Any]) -> None:
    """Copy persona files from Hermes profiles into the project personas/ directory."""
    for slug in get_cat_profiles(config):
        profile_dir = hermes_profile_dir(config, slug)
        if not profile_dir.exists():
            logger.debug("Hermes profile missing, skipping sync: %s", slug)
            continue

        target_dir = PERSONAS_DIR / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        soul_src = profile_dir / "SOUL.md"
        if soul_src.exists():
            shutil.copy2(soul_src, target_dir / "SOUL.md")

        skill_dir = profile_dir / "skills" / "council-voice"
        skill_markdown = _read_hermes_skill_markdown(skill_dir)
        if skill_markdown:
            (target_dir / "council-voice.md").write_text(skill_markdown + "\n", encoding="utf-8")


def seed_hermes_profiles(config: dict[str, Any]) -> None:
    """Copy project persona seeds into Hermes profile directories if they exist."""
    for slug in get_cat_profiles(config):
        profile_dir = hermes_profile_dir(config, slug)
        profile_dir.mkdir(parents=True, exist_ok=True)

        soul_src = PERSONAS_DIR / slug / "SOUL.md"
        if soul_src.exists():
            shutil.copy2(soul_src, profile_dir / "SOUL.md")

        voice_src = PERSONAS_DIR / slug / "council-voice.md"
        if voice_src.exists():
            skill_dir = profile_dir / "skills" / "council-voice"
            skill_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(voice_src, skill_dir / "SKILL.md")

    chronicler_dir = hermes_profile_dir(config, config["hermes"]["chronicler_profile"])
    chronicler_dir.mkdir(parents=True, exist_ok=True)
    chronicler_soul = chronicler_dir / "SOUL.md"
    if not chronicler_soul.exists():
        chronicler_soul.write_text(read_prompt("chronicler_system.md") + "\n", encoding="utf-8")

    curator_dir = hermes_profile_dir(config, config["hermes"]["curator_profile"])
    curator_dir.mkdir(parents=True, exist_ok=True)
    curator_soul = curator_dir / "SOUL.md"
    if not curator_soul.exists():
        curator_soul.write_text(read_prompt("curator_system.md") + "\n", encoding="utf-8")
