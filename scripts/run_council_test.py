#!/usr/bin/env python3
"""Run the council pipeline against sample or file logs (no Discord required)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from council_common import load_config
from council_orchestrator import run_council_pipeline

SAMPLE_LOGS = """[Alice]: We argued about pizza toppings for three hours
[Bob]: someone brought a laser pointer into voice chat
[Carol]: Barnaby the cat knocked over my mug again somehow"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a council session from log text.")
    parser.add_argument(
        "--logs-file",
        type=Path,
        help="Path to a text file of sanitized [User]: message lines.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.json (default: project config.json).",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.logs_file:
        raw_logs = args.logs_file.read_text(encoding="utf-8").strip()
    else:
        raw_logs = SAMPLE_LOGS

    result = run_council_pipeline(raw_logs, config)
    print("Council run complete.")
    print(f"  brief_source: {result['brief_source']}")
    print(f"  transcript:   {result['transcript_path']}")


if __name__ == "__main__":
    main()
