"""Shared utilities for Council of Cats."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
