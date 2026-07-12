#!/usr/bin/env python3
"""Run the council on a schedule — no Discord trigger message required.

Fetches logs from the configured channel (or --channel-id), runs the pipeline,
and posts cat lines via webhooks. Safe to call from cron or systemd timer.

Example crontab (Sundays at 18:00 UTC):
  0 18 * * 0 cd /root/CouncilOfCats && set -a && source council.env && set +a && .venv/bin/python scripts/run_council_scheduled.py >> research/cron.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from council_common import load_config
from council_service import run_council_from_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Council of Cats without a Discord trigger message.",
    )
    parser.add_argument(
        "--channel-id",
        type=int,
        help="Override discord.channel_id for this run (source logs channel).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.json (default: project config.json).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    result = await run_council_from_config(config, channel_id=args.channel_id)
    logger.info("Council run complete: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
