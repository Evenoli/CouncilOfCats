"""VPS Discord gateway: fetch logs and forward them to the local orchestrator."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
import requests

from council_common import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TRIGGER_COMMAND = "!council run"
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


def post_to_orchestrator(raw_logs: str, config: dict) -> dict:
    payload = {
        "secret": config["shared_secret"],
        "raw_logs": raw_logs,
    }
    response = requests.post(
        config["local_orchestrator_url"],
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


class CouncilGateway(discord.Client):
    def __init__(self, config: dict) -> None:
        super().__init__(intents=intents)
        self.config = config

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        content = message.content.strip()
        if content != TRIGGER_COMMAND:
            return

        admin_ids = {str(user_id) for user_id in self.config["discord"]["admin_user_ids"]}
        if str(message.author.id) not in admin_ids:
            await message.channel.send("You are not authorised to run the council.")
            return

        target_channel_id = int(self.config["discord"]["channel_id"])
        channel = self.get_channel(target_channel_id)
        if channel is None:
            channel = await self.fetch_channel(target_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await message.channel.send("Configured council channel was not found.")
            return

        await message.channel.send("Summoning the High Council… fetching logs.")
        days = int(self.config["orchestrator"].get("log_days", 7))
        raw_logs = await fetch_channel_logs(channel, days)

        try:
            result = await asyncio.to_thread(post_to_orchestrator, raw_logs, self.config)
            await message.channel.send(
                "Council run accepted by the local orchestrator.\n"
                f"Brief source: `{result.get('brief_source', 'unknown')}`\n"
                f"Transcript: `{result.get('transcript_path', 'pending')}`"
            )
        except requests.RequestException as exc:
            logger.exception("Failed to reach local orchestrator.")
            await message.channel.send(
                "Could not reach the local orchestrator. Check the tunnel and local service.\n"
                f"Error: {exc}"
            )


def main() -> None:
    config = load_config()
    client = CouncilGateway(config)
    client.run(config["discord"]["bot_token"])


if __name__ == "__main__":
    main()
