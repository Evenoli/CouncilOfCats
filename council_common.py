"""Shared utilities for Council of Cats."""

from __future__ import annotations

import json
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

DEBATE_CATS = ["barnaby", "cleo", "pip"]

CAT_DISPLAY_NAMES = {
    "chair-cat": "Chair Cat",
    "barnaby": "Barnaby",
    "cleo": "Cleo",
    "pip": "Pip",
}


@dataclass
class CouncilBrief:
    summary: str
    memory_callbacks: str
    memory_updates: str
    source: str


@dataclass
class DebateTurnResult:
    cat_slug: str
    display_name: str
    content: str


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or PROJECT_ROOT / "config.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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


class OllamaClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key="ollama")

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
            raise RuntimeError("Ollama returned an empty response.")
        return content.strip()


def send_webhook(url: str, content: str, username: str | None = None) -> None:
    payload: dict[str, str] = {"content": content}
    if username:
        payload["username"] = username
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def dispatch_cat_message(
    webhooks: dict[str, str],
    cat_slug: str,
    content: str,
    delay_seconds: float,
) -> None:
    url = webhooks[cat_slug]
    display_name = CAT_DISPLAY_NAMES[cat_slug]
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
