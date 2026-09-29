"""VPS Discord service: scrape logs and run the council pipeline in-process."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from council_common import (
    add_submission,
    evaluate_manual_trigger,
    format_submissions_status,
    get_read_channel_id,
    get_status_channel_id,
    load_config,
)
from council_orchestrator import run_council_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TRIGGER_COMMAND = "!council run"
SUBMIT_PREFIX = "!council submit"
LIST_COMMAND = "!council submissions"
intents = discord.Intents.default()
intents.message_content = True


def sanitize_message(message: discord.Message) -> str | None:
    if message.author.bot or message.type != discord.MessageType.default:
        return None
    if not message.content or not message.content.strip():
        return None
    author = message.author.display_name
    content = " ".join(message.content.split())
    return f"[{author}]: {content}"


async def fetch_channel_logs(
    channel: discord.TextChannel,
    days: int,
) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    lines: list[str] = []

    async for message in channel.history(limit=None, oldest_first=True, after=cutoff):
        sanitized = sanitize_message(message)
        if sanitized:
            lines.append(sanitized)

    if not lines:
        return "No user messages were found in the configured window."
    return "\n".join(lines)


async def fetch_logs_from_config(config: dict) -> str:
    """Connect briefly to Discord, scrape logs, and disconnect."""
    target_id = get_read_channel_id(config)
    days = int(config.get("council", {}).get("log_days", 7))
    token = config["discord"]["bot_token"]

    scrape_intents = discord.Intents.default()
    raw_logs: dict[str, str] = {}

    class LogFetcher(discord.Client):
        async def on_ready(self) -> None:
            channel = self.get_channel(target_id)
            if channel is None:
                channel = await self.fetch_channel(target_id)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"Channel {target_id} is not a text channel.")
            raw_logs["text"] = await fetch_channel_logs(channel, days)
            await self.close()

    client = LogFetcher(intents=scrape_intents)
    await client.start(token)
    return raw_logs.get("text", "No user messages were found in the configured window.")


def format_council_success(result: dict) -> str:
    discussed = result.get("submissions_discussed", 0)
    remaining = result.get("submissions_remaining", 0)
    return (
        "The High Council has convened.\n"
        f"Brief source: `{result.get('brief_source', 'unknown')}`\n"
        f"Transcript: `{result.get('transcript_path', 'pending')}`\n"
        f"Agenda items discussed: {discussed} (remaining queued: {remaining})"
    )


def format_council_failure(exc: Exception) -> str:
    return (
        "The council could not complete its session. Check the service logs.\n"
        f"Error: {exc}"
    )


async def post_bot_message(config: dict, channel_id: int, content: str) -> None:
    """Connect briefly to Discord, post one message, and disconnect."""
    token = config["discord"]["bot_token"]

    class Poster(discord.Client):
        async def on_ready(self) -> None:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"Channel {channel_id} is not a text channel.")
            await channel.send(content)
            await self.close()

    client = Poster(intents=discord.Intents.default())
    await client.start(token)


async def notify_council_status(
    config: dict,
    *,
    success: bool,
    result: dict | None = None,
    error: Exception | None = None,
    fallback_status_channel_id: int | None = None,
) -> None:
    """Post a completion message to the status channel, or a manual-run fallback."""
    channel_id = get_status_channel_id(config) or fallback_status_channel_id
    if channel_id is None:
        return

    if success:
        content = format_council_success(result or {})
    else:
        content = format_council_failure(error or RuntimeError("unknown error"))

    await post_bot_message(config, channel_id, content)


async def run_council_from_config(
    config: dict,
    *,
    fallback_status_channel_id: int | None = None,
) -> dict:
    """Fetch Discord logs and run the full council pipeline."""
    read_id = get_read_channel_id(config)
    logger.info("Fetching logs from channel %s", read_id)
    raw_logs = await fetch_logs_from_config(config)
    try:
        result = await asyncio.to_thread(run_council_pipeline, raw_logs, config)
        await notify_council_status(
            config,
            success=True,
            result=result,
            fallback_status_channel_id=fallback_status_channel_id,
        )
        return result
    except Exception as exc:
        await notify_council_status(
            config,
            success=False,
            error=exc,
            fallback_status_channel_id=fallback_status_channel_id,
        )
        raise


class CouncilService(discord.Client):
    def __init__(self, config: dict) -> None:
        super().__init__(intents=intents)
        self.config = config

    async def on_ready(self) -> None:
        logger.info("Council service logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        content = message.content.strip()
        lowered = content.lower()

        if lowered == LIST_COMMAND or lowered == "!council queue":
            await message.channel.send(format_submissions_status(self.config))
            return

        if lowered == TRIGGER_COMMAND:
            allowed, denial = evaluate_manual_trigger(self.config, message.author.id)
            if not allowed:
                await message.channel.send(denial)
                return

            await message.channel.send("Summoning the High Council… fetching logs.")
            try:
                await run_council_from_config(
                    self.config,
                    fallback_status_channel_id=message.channel.id,
                )
            except Exception:
                logger.exception("Council pipeline failed.")
            return

        if lowered == SUBMIT_PREFIX or lowered.startswith(SUBMIT_PREFIX + " "):
            topic = content[len(SUBMIT_PREFIX) :].strip()
            _ok, reply, _item = add_submission(
                topic,
                submitted_by=message.author.display_name,
                submitted_by_id=message.author.id,
                config=self.config,
            )
            await message.channel.send(reply)
            return


def main() -> None:
    config = load_config()
    client = CouncilService(config)
    client.run(config["discord"]["bot_token"])


if __name__ == "__main__":
    main()
