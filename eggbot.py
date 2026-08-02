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
        async with aiohttp.ClientSession() as session:
            async with session.post(f'{host}/v1/token/',data={"id": user, "password": password}) as resp:
                if resp.status >= 400:
                    logger.debug(await resp.text())
                    return None
                return await resp.json()

    async def get_token(self,name, force=False):
        if DATA["server_list"].get(name) is None:
            return None

        if force is True or DATA["server_list"][name].get('token') is None:
            result = await self.get_token_by_auth(DATA["sever_list"][name]["host"], DATA["server_list"][name]["id"], DATA["server_list"][name]["password"])
            if not result:
                return None

            DATA["server_list"][name]["token"] = result["data"]["token"]
            save_data()
            logger.debug(DATA["server_list"][name]["token"])
        return DATA["server_list"][name]["token"]


    #get fry meta data
    async def get_fry_meta(self,name,serverObj):
        logger.debug(f"geting meta for {name}")
        token = await self.get_token(name)
        headers = {}
        if token is not None:
            headers['Authorization'] = 'Bearer '+ token
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(DATA["server_list"][name]["endpoint"]+'/v1/meta', headers=headers) as resp:
                    if resp.status >= 400:
                        logger.debug(resp.status)
                        logger.debug(await resp.text())
                        logger.info('getting new token for '+name)
                        headers = {'Authorization': 'Bearer ' + await self.get_token(name, False) }

                        async with aiohttp.ClientSession() as session:
                            async with session.get(DATA["server_list"][name]["endpoint"]+'/v1/meta', headers=headers) as resp:
                                result =  await resp.json()
                                logger.debug(f"1 {result['data']}")
                                return result["data"]
                    else:
                        result =  await resp.json()
                        logger.debug(result["data"])
                        return result["data"]
        except:
            return False
    
    #gets current player online meta data from fry and store it to json for !ls command use.
    async def store_online_users(self):
        methods = []
        for name, server in DATA["server_list"].items():
            methods.append(self.get_fry_meta(name, server))
            
        data = await asyncio.gather(*methods)
        data.sort(key=lambda s: "" if not s else s.get("name"))
        
        for info in data:
            if info:
                server = info["name"].strip().lower()
                self.tracking_service.reconcile_api_snapshot(
                    server_name=server,
                    players_online=info.get("players_online", {}),
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

    #gets started in class __init__
    async def recuring_task(self):
        while True:
            await asyncio.sleep(300) #every 5 min get the user online meta data.
            await self.store_online_users()
    
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
