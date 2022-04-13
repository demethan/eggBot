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
            asyncio.ensure_future(self.timer(300)) #cooldown of the admin notifcation. 
    
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
            methods.append(self.get_fry_meta(name,server))
            
        data = await asyncio.gather(*methods)
        data.sort(key=lambda s: "" if not s else s.get("name"))
        for info in data:
            if info:
                server = info["name"].strip().lower()
                if info["players_online"].__len__() > 0:
                    DATA["server_list"][server]["players"] = info["players_online"]
        logger.info("Storing online players...")
        save_data()

    #gets started in class __init__
    async def recuring_task(self):
        while True:
            await asyncio.sleep(300) #every 5 min get the user online meta data.
            await self.store_online_users()


    #error handling
    async def on_command_error(self, ctx, error):
        logger.error(error)
        logger.error(ctx)
        try:
            embed=discord.Embed(Title="Error:", color=0x00ff00)
            if error.param._name == "arg":
                message = "!"+ctx.command.name+" requires an argument. Type !help to get a list"
            if error.param._name == "key":
                message = "!"+ctx.command.name+" requires the server name without the @.  Ex. MyFry \n"
                message += "possible server name: "
                embed.add_field(name="servers",value = list(DATA["server_list"].keys()), inline=False)
            if error.param._name == "host":
                message = "!"+ctx.command.name+" is missing the host argument. Type !help to get a list"
                
            if message is not None:
                embed.add_field(name="⚠",value = message, inline=False)
                await ctx.send(embed=embed)
        except:
            pass




bot = eggBot("!")
bot.run(CONFIG["token"])


