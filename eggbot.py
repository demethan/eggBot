#!/usr/bin/env python

import discord
import re
import asyncio
import os
from pathlib import Path
from commands_cog import CommandsCog
from admin_commands_cog import AdminCommandsCog
from support_commands_cog import SupportCommandsCog
from application_service import ApplicationService
from eggbot_db.database import Database
from eggbot_db.repositories import Server, ServerRepository
from eggbot_db.secrets import SecretBox
from fry_api import FryApiClient
from fry_health import FryHealthService
from command_errors import reaction_for_command_error
from player_tracking import PlayerTrackingService
from tracking_cog import TrackingCog
from runtime_config import RuntimeConfig
from discord.ext import commands
from config import CONFIG
from loguru import logger


class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.database_connection = None
        self.fry_api = None
        self.fry_health = None
        self._fry_poll_errors = {}
        self._recurring_task = None
        self.servers = None
        self.runtime_config = None

    async def setup_hook(self):
        database = Database(Path(os.environ.get("EGGBOT_DB_PATH", "eggbot.sqlite3")))
        database.migrate()
        key_file = os.environ.get("EGGBOT_SECRET_KEY_FILE")
        secrets = (
            SecretBox.from_file(Path(key_file))
            if key_file
            else SecretBox.from_environment()
        )
        self.database_connection = database.connect()
        servers = ServerRepository(self.database_connection, secrets)
        self.servers = servers
        self.runtime_config = RuntimeConfig(self.database_connection, servers)

        def persist_token(server, token):
            servers.update_token(server.id, token)
            self.database_connection.commit()

        self.fry_api = FryApiClient(on_token_refreshed=persist_token)
        await self.fry_api.start()
        applications = ApplicationService(
            self.database_connection, servers, self.fry_api
        )
        tracking = PlayerTrackingService(self.database_connection)
        self.fry_health = FryHealthService(self.database_connection)
        self.tracking_service = tracking
        await self.add_cog(CommandsCog(self, applications))
        await self.add_cog(
            AdminCommandsCog(
                self, applications, tracking,
                servers=servers, settings=self.runtime_config.settings,
                schedules=self.runtime_config.schedules,
            )
        )
        await self.add_cog(SupportCommandsCog(self, applications))
        await self.add_cog(TrackingCog(self, tracking))
        if self._recurring_task is None or self._recurring_task.done():
            self._recurring_task = asyncio.create_task(self.recuring_task())
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None:
            channel = await member.create_dm()
            await channel.send(self.runtime_config.get("joinMessage").format(
                member=member.mention, applyUrl=self.runtime_config.get("applyUrl")
            ))
        
    
    async def timer(self,time):
        await asyncio.sleep(time)
        self.flag = True



    #startup connection to discord
    async def on_ready(self):
        await self.validate_data()
        support_cog = self.get_cog("SupportCommands")
        if support_cog is not None:
            await support_cog.reconcile_pending_reviews()
            await support_cog.reconcile_followup_notifications()
        logger.info('Logged on as {0}!'.format(self.user))

    async def close(self):
        if self._recurring_task is not None:
            self._recurring_task.cancel()
            try:
                await self._recurring_task
            except asyncio.CancelledError:
                pass
            self._recurring_task = None
        if self.fry_api is not None:
            await self.fry_api.close()
        if self.database_connection is not None:
            self.database_connection.close()
        await super().close()

    async def probe_fry_server(self, endpoint, api_user, api_password):
        candidate = Server(-1, "candidate", endpoint, api_user, api_password, None, True)
        token = await self.fry_api.authenticate(candidate, force=True, persist=False)
        if not token.ok:
            return None, None
        metadata = await self.fry_api.get_metadata(candidate)
        return token.value, metadata.value if metadata.ok else None

    async def get_fry_meta(self, name):
        logger.debug("Getting metadata for {}", name)
        self._fry_poll_errors.pop(name, None)
        server = self.servers.get_by_name(name)
        if server is None or not server.enabled:
            self._fry_poll_errors[name] = "Server is not configured"
            return False
        result = await self.fry_api.get_metadata(server)
        if result.ok:
            return result.value
        self._fry_poll_errors[name] = result.error.code.value
        logger.warning("Fry metadata request for {} failed: {}", name, result.error.code.value)
        return False
    
    #gets current player online meta data from fry and store it to json for !ls command use.
    async def store_online_users(self):
        server_names = [server.name for server in self.servers.list_enabled()]
        methods = [self.get_fry_meta(name) for name in server_names]
            
        data = await asyncio.gather(*methods)
        results = dict(zip(server_names, data))

        for info in results.values():
            if info:
                server = info["name"].strip().lower()
                self.tracking_service.reconcile_api_snapshot(
                    server_name=server,
                    players_online=info.get("players_online", {}),
                    pack_name=info.get("pack_name"),
                    pack_version=info.get("pack_version"),
                    pack_metadata={
                        key: value
                        for key, value in info.items()
                        if key != "players_online"
                    },
                )
        logger.info("Stored Fry snapshots in SQLite")
        if self.fry_health is not None:
            transition = self.fry_health.record_cycle(
                {
                    name: (
                        None
                        if info
                        else self._fry_poll_errors.get(name, "Unknown Fry response")
                    )
                    for name, info in results.items()
                }
            )
            await self.publish_fry_health_transition(transition)

    async def publish_fry_health_transition(self, transition):
        if transition.action == "none":
            return
        await self.wait_until_ready()
        channel_id = transition.alert_channel_id or self.runtime_config.require_int("adminChannelID")
        channel = self.get_channel(channel_id)
        if channel is None:
            logger.error("Unable to find admin channel {} for Fry health alert", channel_id)
            return

        if transition.action == "recovery":
            duration_seconds = max(
                0,
                int((transition.recovered_at - transition.started_at).total_seconds()),
            )
            duration_minutes = max(1, round(duration_seconds / 60))
            embed = discord.Embed(
                title="🟢 Fry connectivity restored",
                description="All monitored Fry APIs are reachable again.",
                color=0x2ECC71,
            )
            embed.add_field(
                name="Recovered",
                value=(
                    f"<t:{int(transition.recovered_at.timestamp())}:F> "
                    f"(<t:{int(transition.recovered_at.timestamp())}:R>)"
                ),
                inline=False,
            )
            embed.add_field(
                name="Outage duration",
                value=f"Approximately {duration_minutes} minutes",
                inline=False,
            )
            embed.set_footer(
                text="Times are displayed in your Discord local timezone."
            )
            await channel.send(embed=embed)
            self.fry_health.mark_recovery_sent(transition.incident_id)
            return

        title = (
            "🔴 Possible WireGuard connection outage"
            if transition.all_servers_unreachable
            else "🔴 Fry API connectivity problem"
        )
        affected = ", ".join(transition.affected_servers)
        embed = discord.Embed(
            title=title,
            description=f"EggBot cannot reach Fry on: **{affected}**.",
            color=0xE74C3C,
        )
        embed.add_field(
            name="First failure",
            value=(
                f"<t:{int(transition.started_at.timestamp())}:F> "
                f"(<t:{int(transition.started_at.timestamp())}:R>)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Failed polls",
            value="At least 2 consecutive polls",
            inline=False,
        )
        embed.add_field(
            name="Last error",
            value=transition.last_error or "Unknown connection error",
            inline=False,
        )
        embed.set_footer(
            text=(
                "EggBot will retry every five minutes. Times are displayed in your "
                "Discord local timezone."
            )
        )

        message = None
        if transition.action == "update" and transition.alert_message_id:
            try:
                message = await channel.fetch_message(transition.alert_message_id)
                await message.edit(embed=embed)
            except discord.HTTPException:
                logger.exception("Unable to update Fry connectivity alert")
        if message is None:
            role_id = self.runtime_config.get("applicationReviewerRoleID")
            mention = f"<@&{int(role_id)}>" if role_id else None
            message = await channel.send(
                content=mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            self.fry_health.mark_alert_sent(
                transition.incident_id,
                channel_id=channel.id,
                message_id=message.id,
            )

    #gets started in class __init__
    async def recuring_task(self):
        while True:
            try:
                await self.store_online_users()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fry metadata polling cycle failed")
            await asyncio.sleep(300) #every 5 min get the user online meta data.
    
    """ async def on_message(self):
        print(message.content)
        await bot.process_commands(message) """


    #error handling
    async def on_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        logger.error(original)
        try:
            await ctx.message.add_reaction(reaction_for_command_error(error))
        except discord.HTTPException:
            logger.exception("Unable to react to invalid command")

        if isinstance(error, commands.CommandNotFound):
            await ctx.send("Unknown command. Use `!help` to see available commands.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"Missing `{error.param.name}`. Use `!help {ctx.command.qualified_name}` "
                "for usage."
            )
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Try again in {error.retry_after:.1f} seconds.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("That command is not available to you here.")
        elif isinstance(error, commands.UserInputError):
            await ctx.send(
                f"Invalid command input. Use `!help {ctx.command.qualified_name}` for usage."
            )
        else:
            logger.opt(exception=original).error("Unexpected command failure")
            await ctx.send("The command could not be completed.")

    async def validate_data(self):
        admin_channel_id = self.runtime_config.get("adminChannelID")

        if admin_channel_id is None:
            logger.error("Admin channel ID is not configured")
            await bot.change_presence(status=discord.Status.dnd, activity=discord.Game(name="Admin channel not configured"))
            return

        admin_channel = bot.get_channel(admin_channel_id)

        if not admin_channel:
            logger.error("Configured admin channel {} was not found", admin_channel_id)
            await bot.change_presence(status=discord.Status.offline)
            return

        if not self.servers.list_enabled():
            await admin_channel.send("WARNING: No enabled Fry servers are configured.")
            return
        await bot.change_presence(status=discord.Status.online)



intents = discord.Intents.all()
intents.message_content = True
intents.members = True
bot = eggBot(command_prefix='!',intents=intents)
bot.run(CONFIG["token"])
