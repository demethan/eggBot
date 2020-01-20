import discord
import random
import re
import aiohttp
import requests
import asyncio
from discord.ext import commands
from config import DATA, save_data
from loguru import logger


class CommandsCog(commands.Cog, name='Commands'):
    def __init__(self, client):
        self.client = client
    
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
        """Setting meta is done individually to the @server bot. usage @server !meta <set|get> <packname|version> <text> """
        await ctx.send("Setting meta is done individually to the @server bot. usage @server !meta <set|get> <packname|version> <text>")
        

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
        


    #status command, s for short, because people are lazy.
    @commands.command(description='Get info about one or all the Minecraft servers')
    async def s(self, ctx, arg=None):
        """Get info about one or all the Minecraft servers"""

        if arg is not None:
            #get arg(server) info details a send that
            info = await self.client.get_fry_meta(arg,DATA["server_list"][arg])
            try:
                embed=discord.Embed(title="Detailed Status", color=0x00ff00)
                embed.add_field(name="Game", value=info["game"], inline=False)
                embed.add_field(name="Status", value=info["status"], inline=False)
                embed.add_field(name="Pack", value=info["pack_name"], inline=False)
                embed.add_field(name="Pack Version", value=info["pack_version"], inline=False)
                embed.add_field(name="Pack Description", value=info["pack_description"], inline=False)
                embed.add_field(name="Pack URL", value=info["pack_url"], inline=False)

            except:
                await ctx.send("Please check server meta data for "+info["name"])
        else:
            #send complete server list info
            methods = []
            for name, server in DATA["server_list"].items():
                methods.append(self.client.get_fry_meta(name,server))
            
            data = await asyncio.gather(*methods)

            #displaying data
            embed=discord.Embed(title="Servers", color=0x00ff00)
            for info in data:
                try:
                    if info["status"] == "RUNNING":
                        embed.add_field(name="🟢 "+info["name"], value=info["pack_name"]+" ("+info["pack_version"]+")", inline=False)
                    elif info["status"] == "STARTING":
                        embed.add_field(name="🟡 "+info["name"], value=info["pack_name"]+" ("+info["pack_version"]+")", inline=False)
                    else:
                        embed.add_field(name="🔴 "+info["name"], value=info["pack_name"]+" ("+info["pack_version"]+")", inline=False)
                except:
                    await ctx.send("Please check server meta data for "+info["name"])
            
        await ctx.send(embed=embed)
                


    #howto command
    @commands.command(description='Get URL to connection intructions')
    async def howto(self, ctx):
        """Get URL to connection intructions"""
        author = ctx.message.author
        channel = await author.create_dm()
        await channel.send(DATA["connectUrl"])
        

    #roll command
    @commands.command(description='Roll a dice of your choice.')
    async def roll(self, ctx, dice: str):
        """Rolls a dice in NdN format."""
        try:
            rolls, limit = map(int, dice.split('d'))
        except Exception:
            await ctx.send('Format has to be in NdN!')
            return

        result = ', '.join(str(random.randint(1, limit)) for r in range(rolls))
        await ctx.send(result)
    
    #schedule
    @commands.command(description='list the server reboot schedules')
    async def schedule(self, ctx):
        """list the server reboot schedules"""
        message = "Server reboot schedule \n"
        for line in self.client.schedule():
            message += "{server} : {time} \n".format(server=line[0], time=line[1])
        await ctx.send(message)