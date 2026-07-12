#!/usr/bin/env python3
"""Run the council on a schedule — no Discord trigger message required.

Fetches logs from the configured read channel, runs the pipeline, posts cat
lines via webhooks, and posts a completion message to the status channel.
Safe to call from cron or systemd timer.

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

from council_common import get_status_channel_id, load_config
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
        "--config",
        type=Path,
        help="Path to config.json (default: project config.json).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if get_status_channel_id(config) is None:
        logger.error(
            "DISCORD_STATUS_CHANNEL_ID / discord.status_channel_id is required "
            "for scheduled runs (completion messages)."
        )
        sys.exit(1)

    result = await run_council_from_config(config)
    logger.info("Council run complete: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
