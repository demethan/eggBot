#!/usr/bin/env python

import discord
import random
import json
import re
import aiohttp
import asyncio
import datetime
import os
from pathlib import Path
from commands_cog import CommandsCog
from admin_commands_cog import AdminCommandsCog
from support_commands_cog import SupportCommandsCog
from application_service import ApplicationService
from eggbot_db.database import Database
from eggbot_db.repositories import ServerRepository
from eggbot_db.secrets import SecretBox
from fry_api import FryApiClient
from fry_health import FryHealthService
from command_errors import reaction_for_command_error
from player_tracking import PlayerTrackingService
from tracking_cog import TrackingCog
from discord.ext import commands
from config import CONFIG, DATA
from config import DATA, save_data
from loguru import logger


class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.database_connection = None
        self.fry_api = None
        self.fry_health = None
        self._fry_poll_errors = {}
        self._recurring_task = None

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
        await self.add_cog(CommandsCog(self))
        await self.add_cog(AdminCommandsCog(self, applications, tracking))
        await self.add_cog(SupportCommandsCog(self, applications))
        await self.add_cog(TrackingCog(self, tracking))
        self._recurring_task = asyncio.create_task(self.recuring_task())
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None:
            channel = await member.create_dm()
            await channel.send(DATA["joinMessage"].format(member=member.mention,applyUrl=DATA["applyUrl"]))
        
    
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
        if self.fry_api is not None:
            await self.fry_api.close()
        if self.database_connection is not None:
            self.database_connection.close()
        await super().close()

    async def get_token_by_auth(self, host, user, password):
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f'{host}/v1/token/',data={"id": user, "password": password}) as resp:
                if resp.status >= 400:
                    logger.debug(await resp.text())
                    return None
                return await resp.json()

    async def get_token(self,name, force=False):
        server = DATA["server_list"].get(name)
        if server is None:
            return None

        if force is True or server.get('token') is None:
            result = await self.get_token_by_auth(
                server["endpoint"], server["id"], server["password"]
            )
            if not result:
                return None

            server["token"] = result["data"]["token"]
            save_data()
        return server["token"]


    #get fry meta data
    async def get_fry_meta(self,name,serverObj):
        logger.debug(f"geting meta for {name}")
        self._fry_poll_errors.pop(name, None)
        try:
            token = await self.get_token(name)
            headers = {}
            if token is not None:
                headers['Authorization'] = 'Bearer '+ token
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(DATA["server_list"][name]["endpoint"]+'/v1/meta', headers=headers) as resp:
                    if resp.status >= 400:
                        if resp.status in (401, 403):
                            refreshed = await self.get_token(name, True)
                            if refreshed:
                                headers = {'Authorization': 'Bearer ' + refreshed}
                                async with session.get(
                                    DATA["server_list"][name]["endpoint"]+'/v1/meta',
                                    headers=headers,
                                ) as retry:
                                    if retry.status < 400:
                                        result = await retry.json()
                                        logger.debug(result["data"])
                                        return result["data"]
                                    status = retry.status
                            else:
                                status = resp.status
                        else:
                            status = resp.status
                        self._fry_poll_errors[name] = f"HTTP {status}"
                        logger.warning("Fry metadata request for {} returned HTTP {}", name, status)
                        return False
                    result = await resp.json()
                    logger.debug(result["data"])
                    return result["data"]
        except Exception as error:
            error_name = type(error).__name__
            detail = str(error).strip()
            self._fry_poll_errors[name] = (
                f"{error_name}: {detail[:200]}" if detail else error_name
            )
            logger.warning("Fry metadata request for {} failed: {}", name, error_name)
            return False
    
    #gets current player online meta data from fry and store it to json for !ls command use.
    async def store_online_users(self):
        server_names = list(DATA["server_list"])
        methods = [
            self.get_fry_meta(name, DATA["server_list"][name])
            for name in server_names
        ]
            
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
                if info["players_online"].__len__() > 0:
                    DATA["server_list"][server]["players"] = info["players_online"]
                    players_online = info["players_online"]
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # Update each player's information and last seen timestamp
                    for player in players_online:
                        player_key = player.strip().lower()
                        player_data = {"last_seen": current_time}
                        DATA["players"][player_key] = player_data
                        
                    
        logger.info("Storing online players...")
        save_data()
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
        channel_id = transition.alert_channel_id or int(DATA["adminChannelID"])
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
            role_id = DATA.get("applicationReviewerRoleID")
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

    # Define a function to validate DATA and set bot status
    async def validate_data(self):
        admin_channel_id = DATA.get("adminChannelID")  # Get admin channel ID from DATA

        if admin_channel_id is None:
            print("Admin channel ID is not set in DATA. Please provide the correct channel ID.")
            await bot.change_presence(status=discord.Status.dnd, activity=discord.Game(name="Admin Channel Not found or possible DATA corruption."))
            return

        admin_channel = bot.get_channel(admin_channel_id)

        if not admin_channel:
            print("Admin channel not found. Please check the channel ID in DATA.")
            await bot.change_presence(status=discord.Status.offline)
            return

        if "server_list" not in DATA or "players" not in DATA:
            await admin_channel.send("WARNING: DATA structure is missing required keys. Check your data.")
            if "players" not in DATA:
                DATA["players"] = {}  # Initialize with an empty dictionary
        else:
            await admin_channel.send("DATA validation passed. Bot is ready to run.")
            await bot.change_presence(status=discord.Status.online)



intents = discord.Intents.all()
intents.message_content = True
intents.members = True
bot = eggBot(command_prefix='!',intents=intents)
bot.run(CONFIG["token"])
