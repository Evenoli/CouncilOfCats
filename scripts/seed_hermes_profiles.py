#!/usr/bin/env python3
"""Create Hermes profiles and seed SOUL.md / council-voice skills from personas/."""

from __future__ import annotations

import shutil
import subprocess
import sys

from council_common import load_config
from hermes_research import is_hermes_available, seed_hermes_profiles

PROFILES = [
    "council-chronicler",
    "council-curator",
    "barnaby",
    "cleo",
    "pip",
    "chair-cat",
]


def create_profiles(config: dict) -> None:
    cli = config["hermes"]["cli_command"]
    if not shutil.which(cli):
        print(f"Hermes CLI not found: {cli}")
        print("Install Hermes first, then re-run this script.")
        sys.exit(1)

    for profile in PROFILES:
        completed = subprocess.run(
            [cli, "profile", "create", profile],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            print(f"Created profile: {profile}")
        else:
            combined = (completed.stderr or completed.stdout).strip()
            if "already exists" in combined.lower():
                print(f"Profile already exists: {profile}")
            else:
                print(f"Profile create note for {profile}: {combined}")


def main() -> None:
    config = load_config()
    if not config.get("hermes", {}).get("enabled", True):
        print("Hermes is disabled in config.json.")
        sys.exit(1)

    create_profiles(config)
    seed_hermes_profiles(config)
    print("Seeded persona files into Hermes profiles.")
    if is_hermes_available(config):
        print("Hermes CLI is available for council runs.")
    else:
        print("Warning: Hermes CLI still not detected on PATH.")


if __name__ == "__main__":
    main()
