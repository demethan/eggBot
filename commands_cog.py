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
color=0x00ff00

class CommandsCog(commands.Cog, name='Commands'):
    def __init__(self, client):
        self.client = client

    #status command, s for short, because people are lazy.
    @commands.command(description='Get info about one or all the Minecraft servers')
    async def s(self, ctx, arg=None):
        """Get info about one or all the Minecraft servers"""
        pattern = re.compile("<@!*[0-9]*>")
        if arg is not None:
            if pattern.match(arg):
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
                arg = user.name 
            #get arg(server) info details a send that
            info = await self.client.get_fry_meta(arg,DATA["server_list"][arg])
            try:
                embed=discord.Embed(title="Detailed Status", color=0x00ff00)
                embed.add_field(name="Game:", value=info["game"], inline=False)
                embed.add_field(name="Status:", value=info["status"], inline=False)
                embed.add_field(name="Pack:", value=info["pack_name"], inline=False)
                embed.add_field(name="Pack Version:", value=info["pack_version"], inline=False)
                embed.add_field(name="Pack Description:", value=info["pack_description"], inline=False)
                embed.add_field(name="Pack URL:", value=info["pack_url"], inline=False)

            except:
                await ctx.send("Please check server meta data for "+info["name"])
        else:
            #send complete server list info
            methods = []
            for name, server in DATA["server_list"].items():
                methods.append(self.client.get_fry_meta(name,server))
            
            data = await asyncio.gather(*methods)

            #displaying data
            embed=discord.Embed(title="Servers", color=color)
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
                


    #connection info command
    @commands.command(description='Get connection intructions')
    async def c(self, ctx):
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
        message=""
        for line in self.client.schedule():
            message += "{server} : {time} \n".format(server=line[0], time=line[1])
        embed=discord.Embed(title="Reboot Schedule", color=color )
        embed.add_field(name="⏲",value=message,inline=False)
        await ctx.send(embed=embed)