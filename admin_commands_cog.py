import discord
import random
import re
import aiohttp
import requests
import asyncio
from discord.ext import commands
from config import DATA, save_data
from loguru import logger
from discord.utils import get
from addon import admin_only

class AdminCommandsCog(commands.Cog, name='AdminCommands'):
    def __init__(self, client):
        self.client = client
        self.cog_check(admin_only)

    async def admin_only(self, ctx: commands.Context):
        return ctx.channel.id in self.app.config["plugins"]["discord"]["admin-channels"]
    #set command to change urls and messages
    @commands.command(description='Set or change the apply url, connection url and welcome message of the concierge', rest_is_raw=True)
    async def set(self, ctx, arg, *,text):
        """Admin: Change the apply url, connection url or the Welcome message. usage !set <applyUrl|connectUrl|joinMessage> <text>. """
        
        set_command_dict = {"applyurl":"applyUrl","connecturl":"connectUrl", "joinmessage":"joinMessage"  }
        
        if not self.client.is_allowed(ctx):
            await ctx.message.add_reaction('⛔')
            return
        try:
            arg = set_command_dict.get(arg.lower(), None)        
            if arg is not None and text is not None:
                DATA[arg] = text.strip()
                save_data()
                await ctx.send("Saved!")
            else:
                await ctx.send(" !set argument is not valid. usage !set <applyUrl|connectUrl|joinMessage> <text>.")
        except Exception as inst:
                logger.exception(inst)
                await ctx.send("you are missing something, try again!")
                return

    #setmeta command to store meta data in fry
    @commands.command(description='Not a command. Setting meta is done individually to the @server bot.', rest_is_raw=True)
    async def setmeta(self, ctx):
        """Setting meta is done individually to the @server bot. usage @server !meta <set|get> <packname|version> <text>"""
        with open('meta_instruction.txt', 'r') as file:
            message = file.read()
        embed=discord.Embed(title="Detailed Instruction", color=0x00ff00)
        embed.add_field(name="Info:", value=message, inline=False)
        await ctx.send(embed=embed)

    #add command is to add a server.
    @commands.command(description='Add a server to eggbot.', rest_is_raw=True)
    async def add(self, ctx, host, user, password):
        """Admin: Setup a server. The server name will be what ever you setup in the fry config. usage !add <host> <user> <password>"""

        if not self.client.is_allowed(ctx):
            await ctx.message.add_reaction('⛔')
            return        

        if host is not None and user is not None and password is not None:
            try:
                host = host.strip('/')
                data={"endpoint":host,"id":user,"password":password}
                token = await self.client.get_token(data)
                await ctx.send("Token aquired!")
            except Exception as inst:
                logger.exception(inst)
                await ctx.send("something went wrong, check the url, username and password")
                return
            try:
                headers = {'Authorization': 'Bearer '+token}
                async with aiohttp.ClientSession() as session:
                    async with session.get(host+'/v1/meta', headers=headers) as response:
                        result =  await response.json()
                        name = result['data']['name']
                        await ctx.send(name+" Found!")
            except Exception as inst:
                logger.exception(inst)
                await ctx.send("Failed to get server name, set fry's meta data.")
                return

            try:
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password, 'token':token}
            except KeyError:
                DATA["server_list"] = {}
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password, 'token':token}
            save_data()
            await ctx.send("Added succesfully!")
        else:
            await ctx.send(" !set argument is not valid. usage !add <servername> <host> <user> <password>. exemple !add https://localhost:4321 admin 12345abc")

    #remove command is to add a server.
    @commands.command(description='remove a server from eggbot.', rest_is_raw=True)
    async def remove(self, ctx, key):
        """Admin: Remove a server from eggbot. Use the server name as displayed in !s command. usage !remove <servername>"""

        if not self.client.is_allowed(ctx):
            await ctx.message.add_reaction('⛔')
            return        

        if key is not None:
            await ctx.send(key+ " will be removed from the list, confirm with YES ")
            msg = await self.client.wait_for('message')
            if msg.content=="YES" and ctx.message.author == msg.author:
                try:
                    del DATA["server_list"][key]
                    save_data()
                    await ctx.send(key+" as been removed!")
                    logger.warning(key+" deleted from config")
                except KeyError:
                    await ctx.send(key+" is not in the config.")
            else:
                await ctx.send("removal cancelled!")
    
    