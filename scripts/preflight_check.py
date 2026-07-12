#!/usr/bin/env python3
"""Check that required config and secrets are present before going live."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / "council.env"

PLACEHOLDER_MARKERS = ("YOUR_", "CHANGE_ME", "example.com")
DISCORD_WEBHOOK_RE = re.compile(
    r"^https://discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$"
)


def is_placeholder(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


def is_valid_webhook(url: str) -> bool:
    return bool(DISCORD_WEBHOOK_RE.match(url.strip()))


def check() -> list[str]:
    issues: list[str] = []

    if not CONFIG_PATH.exists():
        issues.append("config.json missing — run scripts/vps_setup.sh first")
        return issues

    with CONFIG_PATH.open(encoding="utf-8") as handle:
        config = json.load(handle)

    llm_key = os.environ.get("LLM_API_KEY", "") or config.get("llm", {}).get("api_key", "")
    if not llm_key or is_placeholder(str(llm_key)):
        issues.append("LLM_API_KEY / llm.api_key not set")

    discord = config.get("discord", {})
    token = os.environ.get("DISCORD_BOT_TOKEN", "") or discord.get("bot_token", "")
    read_channel = (
        os.environ.get("DISCORD_READ_CHANNEL_ID", "")
        or os.environ.get("DISCORD_CHANNEL_ID", "")
        or discord.get("read_channel_id", "")
        or discord.get("channel_id", "")
    )
    status_channel = (
        os.environ.get("DISCORD_STATUS_CHANNEL_ID", "")
        or discord.get("status_channel_id", "")
    )
    admins = os.environ.get("DISCORD_ADMIN_USER_IDS", "") or ",".join(
        str(x) for x in discord.get("admin_user_ids", [])
    )

    if not token or is_placeholder(str(token)):
        issues.append("DISCORD_BOT_TOKEN not set")
    if not read_channel or is_placeholder(str(read_channel)):
        issues.append("DISCORD_READ_CHANNEL_ID not set")
    if not status_channel or is_placeholder(str(status_channel)):
        issues.append("DISCORD_STATUS_CHANNEL_ID not set (required for scheduled runs)")
    if not admins.strip() or is_placeholder(admins):
        issues.append("DISCORD_ADMIN_USER_IDS not set")

    webhooks = config.get("webhooks", {})
    webhook_env = {
        "chair-cat": "WEBHOOK_CHAIR_CAT",
        "barnaby": "WEBHOOK_BARNABY",
        "cleo": "WEBHOOK_CLEO",
        "kiwi": "WEBHOOK_KIWI",
    }
    for slug, env_name in webhook_env.items():
        url = os.environ.get(env_name, "") or webhooks.get(slug, "")
        if not url or not is_valid_webhook(str(url)):
            issues.append(f"{env_name} / webhooks.{slug} not set")

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        issues.append(".venv missing — run scripts/vps_setup.sh")

    return issues


def main() -> None:
    # Load council.env if present (for preflight before systemd)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

    issues = check()

    if not issues:
        print("Preflight OK — all required secrets appear to be configured.")
        sys.exit(0)

    print("Preflight: not ready yet (expected during initial setup):\n")
    for item in issues:
        print(f"  - {item}")
    print(f"\nFill in secrets in {ENV_PATH} then re-run:")
    print("  python scripts/preflight_check.py")
    sys.exit(1)


if __name__ == "__main__":
    main()
