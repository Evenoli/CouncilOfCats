"""Shared utilities for Council of Cats."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent
PERSONAS_DIR = PROJECT_ROOT / "personas"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
RESEARCH_DIR = PROJECT_ROOT / "research"
TRANSCRIPTS_DIR = RESEARCH_DIR / "transcripts"
REVIEWS_DIR = RESEARCH_DIR / "reviews"
PATCHES_DIR = RESEARCH_DIR / "persona-patches"
MEMORY_FILE = RESEARCH_DIR / "memory" / "council_memory.md"
LAST_RUN_FILE = RESEARCH_DIR / "last_run.json"
SUBMISSIONS_FILE = RESEARCH_DIR / "submissions.json"
TRANSCRIPT_STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)\.md$")
CONTINUE_MARKER_RE = re.compile(
    r"^\s*CONTINUE:\s*(yes|no)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
DEFAULT_MANUAL_COOLDOWN_DAYS = 6
DEFAULT_SUBMISSIONS_PER_RUN = 4
DEFAULT_MAX_EXTRA_ROUNDS = 2
DEFAULT_MAX_SUBMISSION_CHARS = 500

DEFAULT_DEBATE_CATS = ["barnaby", "cleo", "kiwi"]
DEFAULT_DISPLAY_NAMES = {
    "chair-cat": "Chair Cat",
    "barnaby": "Barnaby",
    "cleo": "Cleo",
    "kiwi": "Kiwi",
}


@dataclass
class CouncilBrief:
    summary: str
    memory_callbacks: str
    memory_updates: str
    source: str


def apply_env_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """Overlay secrets from environment variables (e.g. council.env via systemd)."""
    llm = config.setdefault("llm", {})
    if os.environ.get("LLM_API_KEY"):
        llm["api_key"] = os.environ["LLM_API_KEY"]

    discord = config.setdefault("discord", {})
    if os.environ.get("DISCORD_BOT_TOKEN"):
        discord["bot_token"] = os.environ["DISCORD_BOT_TOKEN"]
    if os.environ.get("DISCORD_READ_CHANNEL_ID"):
        discord["read_channel_id"] = os.environ["DISCORD_READ_CHANNEL_ID"]
    elif os.environ.get("DISCORD_CHANNEL_ID"):
        discord["read_channel_id"] = os.environ["DISCORD_CHANNEL_ID"]
    if os.environ.get("DISCORD_STATUS_CHANNEL_ID"):
        discord["status_channel_id"] = os.environ["DISCORD_STATUS_CHANNEL_ID"]
    admin_ids = os.environ.get("DISCORD_ADMIN_USER_IDS", "").strip()
    if admin_ids:
        discord["admin_user_ids"] = [
            item.strip() for item in admin_ids.split(",") if item.strip()
        ]

    webhooks = config.setdefault("webhooks", {})
    for slug, env_name in (
        ("chair-cat", "WEBHOOK_CHAIR_CAT"),
        ("barnaby", "WEBHOOK_BARNABY"),
        ("cleo", "WEBHOOK_CLEO"),
        ("kiwi", "WEBHOOK_KIWI"),
    ):
        if os.environ.get(env_name):
            webhooks[slug] = os.environ[env_name]

    return config


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or PROJECT_ROOT / "config.json"
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    return apply_env_secrets(config)


def _discord_channel_int(
    config: dict[str, Any],
    key: str,
    *legacy_keys: str,
) -> int | None:
    discord = config.get("discord", {})
    raw = discord.get(key)
    if raw is None:
        for legacy_key in legacy_keys:
            raw = discord.get(legacy_key)
            if raw is not None:
                break
    if raw is None:
        return None
    return int(raw)


def get_read_channel_id(config: dict[str, Any]) -> int:
    """Channel to scrape user messages from (7-day window)."""
    channel_id = _discord_channel_int(config, "read_channel_id", "channel_id")
    if channel_id is None:
        raise KeyError("discord.read_channel_id is not configured")
    return channel_id


def get_status_channel_id(config: dict[str, Any]) -> int | None:
    """Channel for bot completion messages (scheduled runs; optional for manual)."""
    return _discord_channel_int(config, "status_channel_id")


def get_debate_cats(config: dict[str, Any]) -> list[str]:
    cats = config.get("cats", {})
    order = cats.get("debate_order")
    if isinstance(order, list) and order:
        return [str(slug) for slug in order]
    return list(DEFAULT_DEBATE_CATS)


def get_display_name(config: dict[str, Any], cat_slug: str) -> str:
    cats = config.get("cats", {})
    names = cats.get("display_names", {})
    if isinstance(names, dict) and cat_slug in names:
        return str(names[cat_slug])
    return DEFAULT_DISPLAY_NAMES.get(
        cat_slug,
        cat_slug.replace("-", " ").title(),
    )


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8").strip()


def read_prompt(name: str) -> str:
    return read_text(PROMPTS_DIR / name)


def load_cat_system_prompt(cat_slug: str) -> str:
    persona_dir = PERSONAS_DIR / cat_slug
    soul = read_text(persona_dir / "SOUL.md")
    voice = read_text(persona_dir / "council-voice.md")
    if voice:
        return f"{soul}\n\n{voice}".strip()
    return soul


def format_prompt(template: str, **kwargs: str) -> str:
    return template.format(**kwargs)


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    return json.loads(text)


class LLMClient:
    """OpenAI-compatible chat client (Gemini, Ollama, OpenRouter, etc.)."""

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> LLMClient:
        llm = config.get("llm")
        if llm is None:
            # Legacy config support for local Ollama deployments.
            ollama = config["ollama"]
            llm = {
                "base_url": ollama["base_url"],
                "model": ollama["model"],
                "api_key": ollama.get("api_key", "ollama"),
            }

        api_key = llm.get("api_key") or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise ValueError(
                "LLM API key missing. Set llm.api_key in config.json or LLM_API_KEY in the environment."
            )
        return cls(
            base_url=llm["base_url"],
            model=llm["model"],
            api_key=api_key,
        )

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("LLM returned an empty response.")
        return content.strip()


# Backwards-compatible alias for any external imports.
OllamaClient = LLMClient


def send_webhook(url: str, content: str, username: str | None = None) -> None:
    payload: dict[str, str] = {"content": content}
    if username:
        payload["username"] = username
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def dispatch_cat_message(
    config: dict[str, Any],
    cat_slug: str,
    content: str,
    delay_seconds: float,
) -> None:
    webhooks = config["webhooks"]
    url = webhooks[cat_slug]
    display_name = get_display_name(config, cat_slug)
    send_webhook(url, content, username=display_name)
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def append_transcript_line(transcript: str, speaker: str, content: str) -> str:
    block = f"**{speaker}:** {content.strip()}"
    if transcript:
        return f"{transcript}\n\n{block}"
    return block


def week_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-W%W")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_research_dirs() -> None:
    for path in (
        TRANSCRIPTS_DIR,
        REVIEWS_DIR,
        PATCHES_DIR,
        MEMORY_FILE.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)


def get_manual_cooldown_days(config: dict[str, Any]) -> float:
    council = config.get("council", {})
    raw = council.get("manual_cooldown_days", DEFAULT_MANUAL_COOLDOWN_DAYS)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_MANUAL_COOLDOWN_DAYS)


def _parse_last_run_payload(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        stamp = payload.get("completed_at")
        if isinstance(stamp, str) and stamp.strip():
            return datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_run_from_transcripts() -> datetime | None:
    if not TRANSCRIPTS_DIR.exists():
        return None
    latest: datetime | None = None
    for path in TRANSCRIPTS_DIR.glob("*.md"):
        match = TRANSCRIPT_STAMP_RE.match(path.name)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if latest is None or stamp > latest:
            latest = stamp
    return latest


def get_last_run_at() -> datetime | None:
    """Return the timestamp of the most recent successful council run."""
    if LAST_RUN_FILE.exists():
        parsed = _parse_last_run_payload(LAST_RUN_FILE.read_text(encoding="utf-8"))
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    return _last_run_from_transcripts()


def record_council_run(completed_at: datetime | None = None) -> None:
    """Persist completion time so manual cooldown can be enforced."""
    ensure_research_dirs()
    stamp = completed_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    stamp = stamp.astimezone(timezone.utc)
    payload = {"completed_at": stamp.isoformat().replace("+00:00", "Z")}
    LAST_RUN_FILE.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def evaluate_manual_trigger(
    config: dict[str, Any],
    user_id: str | int,
) -> tuple[bool, str]:
    """Decide whether a Discord user may start a manual council run.

    Admins may always trigger. Everyone else may trigger only when no successful
    run has completed within ``council.manual_cooldown_days`` (default 6).
    """
    admin_ids = {
        str(item) for item in config.get("discord", {}).get("admin_user_ids", [])
    }
    if str(user_id) in admin_ids:
        return True, ""

    cooldown_days = get_manual_cooldown_days(config)
    last_run = get_last_run_at()
    if last_run is None:
        return True, ""

    now = datetime.now(timezone.utc)
    elapsed = now - last_run
    cooldown = timedelta(days=cooldown_days)
    if elapsed >= cooldown:
        return True, ""

    remaining = cooldown - elapsed
    remaining_hours = max(1, int(remaining.total_seconds() // 3600) + 1)
    if remaining_hours >= 48:
        remaining_text = f"about {remaining_hours // 24} days"
    else:
        remaining_text = f"about {remaining_hours} hours"
    return (
        False,
        (
            "The High Council needs rest between sessions. "
            f"Anyone may summon them again in {remaining_text} "
            f"(at least {cooldown_days:g} days since the last run). "
            "Authorised keepers can still summon them at any time."
        ),
    )


def get_submissions_per_run(config: dict[str, Any]) -> int:
    council = config.get("council", {})
    raw = council.get("submissions_per_run", DEFAULT_SUBMISSIONS_PER_RUN)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SUBMISSIONS_PER_RUN


def get_max_extra_rounds(config: dict[str, Any]) -> int:
    """Total cat rounds allowed per extra topic (1 required + optional continuations)."""
    council = config.get("council", {})
    raw = council.get("max_extra_rounds", DEFAULT_MAX_EXTRA_ROUNDS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_EXTRA_ROUNDS


def get_max_submission_chars(config: dict[str, Any]) -> int:
    council = config.get("council", {})
    raw = council.get("max_submission_chars", DEFAULT_MAX_SUBMISSION_CHARS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SUBMISSION_CHARS


def load_submissions() -> list[dict[str, Any]]:
    ensure_research_dirs()
    if not SUBMISSIONS_FILE.exists():
        return []
    try:
        payload = json.loads(SUBMISSIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = payload.get("submissions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def save_submissions(items: list[dict[str, Any]]) -> None:
    ensure_research_dirs()
    payload = {"submissions": items}
    SUBMISSIONS_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def peek_submissions_for_run(limit: int) -> list[dict[str, Any]]:
    """Return the oldest pending submissions without removing them."""
    if limit <= 0:
        return []
    return load_submissions()[:limit]


def clear_submissions_by_ids(submission_ids: list[str]) -> int:
    """Remove consumed submissions from the FIFO queue. Returns how many were removed."""
    if not submission_ids:
        return 0
    remove = {str(item) for item in submission_ids}
    remaining: list[dict[str, Any]] = []
    removed = 0
    for item in load_submissions():
        item_id = str(item.get("id", ""))
        if item_id and item_id in remove:
            removed += 1
            continue
        remaining.append(item)
    save_submissions(remaining)
    return removed


def add_submission(
    text: str,
    *,
    submitted_by: str,
    submitted_by_id: str | int,
    config: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Append a community agenda item. Returns (ok, message, item)."""
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return False, "Submission cannot be empty. Try `!council submit your topic here`.", None

    max_chars = get_max_submission_chars(config or {})
    if len(cleaned) > max_chars:
        return (
            False,
            f"Submission is too long ({len(cleaned)} chars). Keep it under {max_chars}.",
            None,
        )

    item = {
        "id": timestamp_slug(),
        "text": cleaned,
        "submitted_by": submitted_by.strip() or "unknown",
        "submitted_by_id": str(submitted_by_id),
        "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    items = load_submissions()
    # Avoid colliding ids if two submits land in the same second.
    existing_ids = {str(existing.get("id", "")) for existing in items}
    if item["id"] in existing_ids:
        item["id"] = f"{item['id']}-{len(items) + 1}"

    items.append(item)
    save_submissions(items)
    position = len(items)
    return (
        True,
        (
            f"Agenda item queued in position {position}: {cleaned}"
        ),
        item,
    )


def parse_chair_continue(raw_text: str) -> tuple[bool, str]:
    """Extract CONTINUE: yes|no from the chair gavel turn.

    Returns (should_continue, discord_facing_text). Missing/invalid markers default
    to closing the topic so cost stays bounded.
    """
    text = raw_text.strip()
    match = CONTINUE_MARKER_RE.search(text)
    if not match:
        cleaned = CONTINUE_MARKER_RE.sub("", text).strip()
        return False, cleaned or text

    should_continue = match.group(1).lower() == "yes"
    cleaned = CONTINUE_MARKER_RE.sub("", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return should_continue, cleaned or text


def format_submissions_status(
    config: dict[str, Any] | None = None,
    *,
    preview_limit: int = 8,
) -> str:
    items = load_submissions()
    per_run = get_submissions_per_run(config or {})
    if not items:
        return (
            "No agenda submissions pending. "
            "Add one with `!council submit your topic here`."
        )

    lines = [
        f"{len(items)} pending submission(s); "
        f"next council will discuss up to {per_run} (oldest first)."
    ]
    for index, item in enumerate(items[:preview_limit], start=1):
        submitter = item.get("submitted_by") or "unknown"
        text = item.get("text") or ""
        lines.append(f"{index}. [{submitter}] {text}")
    remaining = len(items) - preview_limit
    if remaining > 0:
        lines.append(f"…and {remaining} more.")
    return "\n".join(lines)


def load_council_memory() -> str:
    ensure_research_dirs()
    return read_text(MEMORY_FILE, default="No prior council memory recorded yet.")


def save_council_memory(content: str) -> None:
    ensure_research_dirs()
    MEMORY_FILE.write_text(content.strip() + "\n", encoding="utf-8")


def save_transcript(
    transcript: str,
    summary: str,
    brief_source: str,
) -> Path:
    ensure_research_dirs()
    path = TRANSCRIPTS_DIR / f"{timestamp_slug()}.md"
    body = (
        f"# Council Transcript ({week_stamp()})\n\n"
        f"**Brief source:** {brief_source}\n\n"
        f"## Weekly Summary\n\n{summary.strip()}\n\n"
        f"## Debate Transcript\n\n{transcript.strip()}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def extract_cat_lines(transcript: str, display_name: str) -> str:
    pattern = re.compile(
        rf"\*\*{re.escape(display_name)}:\*\*\s*(.*?)(?=\n\n\*\*|\Z)",
        re.DOTALL,
    )
    matches = pattern.findall(transcript)
    if not matches:
        return f"{display_name} spoke, but no lines were extracted."
    return "\n".join(line.strip() for line in matches)


def get_cat_profiles(config: dict[str, Any]) -> list[str]:
    hermes = config.get("hermes", {})
    profiles = hermes.get("cat_profiles")
    if isinstance(profiles, list) and profiles:
        return [str(slug) for slug in profiles]
    debate = get_debate_cats(config)
    return debate + ["chair-cat"]
