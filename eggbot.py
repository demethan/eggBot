#!/usr/bin/env python

import discord
import random
import json
import re
import aiohttp
import requests
import asyncio
from commands_cog import CommandsCog
from discord.ext import commands
from config import CONFIG, DATA
from config import DATA, save_data
from crontab import CronTab
from loguru import logger



class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_cog(CommandsCog(self))
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None:
            channel = await member.create_dm()
            await channel.send(DATA["joinMessage"].format(member=member.mention,applyUrl=DATA["applyUrl"]))
        channel = self.get_channel(DATA["adminChannelID"])
        await channel.send("Fresh meat to process!")


    #startup connection to discord
    async def on_ready(self):
        logger.info('Logged on as {0}!'.format(self.user))

    def schedule(self):
        my_cron =  CronTab(user=True)
        schedule = [(job.command.split()[3], job.description()) for job in my_cron if "restart" in job.command ]
        return schedule

    #check if command comes from admin channel
    def is_allowed(self,ctx):
        if ctx.message.channel.id != DATA["adminChannelID"]:
            logger.warning("ignored command, not from admin")
            return False
        else:
            return True

    async def get_token(self,data):
        async with aiohttp.ClientSession() as session:
            async with session.post(f'{data["endpoint"]}/v1/token/',data={"id": data["id"], "password": data["password"]}) as resp:
                if resp.status >= 400:
                    return None
                result = await resp.json()
                return result["data"]["token"]

    #get fry meta data
    async def get_fry_meta(self,name,serverObj):
        headers = {'Authorization': 'Bearer '+serverObj['token']}
        async with aiohttp.ClientSession() as session:
            async with session.get(serverObj["endpoint"]+'/v1/meta', headers=headers) as resp:
                if resp.status >= 400:
                    logger.info('getting new token for '+name)
                    serverObj['token'] = DATA["server_list"][name]["token"] = await self.get_token(serverObj)
                    save_data()
                    headers = {'Authorization': 'Bearer '+serverObj['token']}
                    async with aiohttp.ClientSession() as session:
                        async with session.get(serverObj["endpoint"]+'/v1/meta', headers=headers) as resp:
                            result =  await resp.json()
                            return result["data"]
                else:
                    result =  await resp.json()
                    return result["data"]

    
    #error handling
    async def on_command_error(self, ctx, error):
        logger.error(error)
        if error.param._name == "arg":
            await ctx.send("!"+ctx.command.name+" requires an argument. Type !help to get a list")




bot = eggBot("!")
bot.run(CONFIG["token"])


