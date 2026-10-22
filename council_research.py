"""Pre-council analysis (chronicler) and post-council review (self-reviews + curator)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from council_common import (
    PATCHES_DIR,
    REVIEWS_DIR,
    CouncilBrief,
    LLMClient,
    PERSONAS_DIR,
    extract_cat_lines,
    extract_json_object,
    format_prompt,
    get_all_cats,
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
    memory = load_council_memory()
    user_prompt = format_prompt(
        read_prompt("chronicler_user.md"),
        memory=memory,
        raw_logs=raw_logs,
    )

    try:
        response = llm.chat(read_prompt("chronicler_system.md"), user_prompt)
        payload = extract_json_object(response)
        brief = CouncilBrief(
            summary=str(payload.get("summary", "")).strip(),
            memory_callbacks=str(payload.get("memory_callbacks", "")).strip(),
            memory_updates=str(payload.get("memory_updates", "")).strip(),
            source="llm:chronicler",
        )
    except Exception as exc:
        logger.exception("Chronicler failed; falling back to plain summary: %s", exc)
        return fallback_brief(llm, raw_logs)

    if brief.memory_updates:
        updated_memory = f"{memory}\n\n## {week_stamp()}\n{brief.memory_updates}".strip()
        save_council_memory(updated_memory)

    return brief


def build_cat_extractions(config: dict[str, Any], transcript: str) -> str:
    sections: list[str] = []
    for slug in get_all_cats(config):
        display_name = get_display_name(config, slug)
        lines = extract_cat_lines(transcript, display_name)
        sections.append(f"### {display_name}\n{lines}")
    return "\n\n".join(sections)


def run_cat_self_reviews(
    config: dict[str, Any],
    llm: LLMClient,
    summary: str,
    transcript: str,
) -> dict[str, Any]:
    reviews: dict[str, Any] = {}
    for slug in get_all_cats(config):
        display_name = get_display_name(config, slug)
        system_prompt = load_cat_system_prompt(slug)
        user_prompt = format_prompt(
            read_prompt("cat_self_review.md"),
            soul=system_prompt.split("\n\n", 1)[0],
            council_voice=read_text(PERSONAS_DIR / slug / "council-voice.md"),
            summary=summary,
            cat_lines=extract_cat_lines(transcript, display_name),
        )
        try:
            response = llm.chat(system_prompt, user_prompt)
            reviews[slug] = extract_json_object(response)
        except Exception as exc:
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
    cat_self_reviews = run_cat_self_reviews(config, llm, summary, transcript)

    user_prompt = format_prompt(
        read_prompt("curator_user.md"),
        summary=summary,
        transcript=transcript,
        cat_extractions=cat_extractions,
        cat_self_reviews=json.dumps(cat_self_reviews, indent=2),
    )
    try:
        response = llm.chat(read_prompt("curator_system.md"), user_prompt)
        review_payload = extract_json_object(response)
        return save_review_artifacts(review_payload, cat_self_reviews)
    except Exception as exc:
        logger.exception("Curator failed: %s", exc)

    fallback_payload: dict[str, Any] = {
        "overall_notes": "Curator unavailable; saved per-cat self reviews only.",
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
