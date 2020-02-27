#!/usr/bin/env python

import discord
import random
import json
import re
import aiohttp
import asyncio
from commands_cog import CommandsCog
from admin_commands_cog import AdminCommandsCog
from discord.ext import commands
from config import CONFIG, DATA
from config import DATA, save_data
from crontab import CronTab
from loguru import logger


class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_cog(CommandsCog(self))
        self.add_cog(AdminCommandsCog(self))
        self.flag = True
        asyncio.ensure_future(self.recuring_task())
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None:
            channel = await member.create_dm()
            await channel.send(DATA["joinMessage"].format(member=member.mention,applyUrl=DATA["applyUrl"]))
        channel = self.get_channel(DATA["adminChannelID"])
        if self.flag :
            await channel.send("Fresh meat to process!")
            self.flag=False
            asyncio.ensure_future(self.timer(300))
    
    async def timer(self,time):
        await asyncio.sleep(time)
        self.flag = True



    #startup connection to discord
    async def on_ready(self):
        logger.info('Logged on as {0}!'.format(self.user))

    def schedule(self):
        my_cron =  CronTab(user=True)
        schedule = [(job.command.split()[3], job.description()) for job in my_cron if "restart" in job.command ]
        return schedule

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
        try:
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
        except:
            return False
    
    async def store_online_users(self):
        methods = []
        for name, server in DATA["server_list"].items():
            methods.append(self.get_fry_meta(name,server))
            
        data = await asyncio.gather(*methods)
        data.sort(key=lambda s: "" if not s else s.get("name"))
        for info in data:
            if info:
                server = info["name"].strip()
                if info["players_online"] != None:
                    DATA["server_list"][server]["players"] = info["players_online"]
        logger.info("Storing online players...")
        save_data()

    
    async def recuring_task(self):
        while True:
            await asyncio.sleep(300) #every 5 min get the user online meta data.
            await self.store_online_users()


    #error handling
    async def on_command_error(self, ctx, error):
        logger.error(error)
        embed=discord.Embed(Title="Error:", color=0x00ff00)
        if error.param._name == "arg":
            message = "!"+ctx.command.name+" requires an argument. Type !help to get a list"
        if error.param._name == "key":
            message = "!"+ctx.command.name+" requires the server name without the @.  Ex. MyFry \n"
            message += "possible server name: "
            embed.add_field(name="severs",value = list(DATA["server_list"].keys()), inline=False)
        if error.param._name == "host":
            message = "!"+ctx.command.name+" is missing the host argument. Type !help to get a list"
            
        if message is not None:
            embed.add_field(name="⚠",value = message, inline=False)
            await ctx.send(embed=embed)




bot = eggBot("!")
bot.run(CONFIG["token"])


