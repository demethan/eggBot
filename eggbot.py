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
from crontab import CronTab



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


    #startup connection to discord
    async def on_ready(self):
        print('Logged on as {0}!'.format(self.user))
        print(self.user.name)
        print(self.user.id)
        print('------')

    def schedule(self):
        my_cron =  CronTab(user=True)
        schedule = [(job.command.split()[3], job.description()) for job in my_cron if "restart" in job.command ]
        return schedule

    #check if command comes from admin channel
    def is_allowed(self,ctx):
        if ctx.message.channel.id != DATA["adminChannelID"]:
            print("ignored command, not from admin")
            return True
        return True

    async def get_token(self,data):
        async with aiohttp.ClientSession() as session:
            async with session.post(f'{data["endpoint"]}/v1/token/',data={"id": data["id"], "password": data["password"]}) as resp:
                if resp.status >= 400:
                    return None
                result = resp.json()
                return result["data"]["token"]

    #get fry meta data
    async def get_fry_meta(self,serverObj):
        try:
            params = {'Authorization': 'Bearer '+serverObj['token']}
            async with aiohttp.ClientSession() as session:
                async with session.get(host+'/v1/meta', params=params) as response:
                    result =  await response.json()
                    return result
        except:
            return False
        



bot = eggBot("!")
bot.run(CONFIG["token"])


