import discord
from discord.ext import commands
from loguru import logger

from player_tracking import PlayerTrackingService


class TrackingCog(commands.Cog, name="Tracking"):
    def __init__(self, client, tracking_service: PlayerTrackingService):
        self.client = client
        self.tracking_service = tracking_service

    @commands.Cog.listener()
    async def on_message(self, message):
        if (
            message.channel.id != self.client.runtime_config.require_int("generalChannelID")
            or not message.author.bot
        ):
            return
        try:
            source_id = message.webhook_id or message.author.id
            source_name = self.client.runtime_config.notification_source(source_id)
            if source_name is None:
                logger.warning(
                    "Ignored unconfigured bot/webhook {} in the general channel",
                    source_id,
                )
                return
            result = self.tracking_service.ingest_discord_message(
                discord_message_id=message.id,
                discord_channel_id=message.channel.id,
                source_discord_id=source_id,
                source_name=source_name,
                occurred_at=message.created_at,
                raw_content=message.content,
            )
            if result.status == "recorded":
                logger.debug(
                    "Recorded {} for {} on {}",
                    result.event_type,
                    result.player_name,
                    result.server_name,
                )
        except Exception:
            logger.exception("Unable to ingest Discord player notification")
