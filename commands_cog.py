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
        if arg is not None:
            pattern = re.compile("<@!*[0-9]*>")
            if pattern.match(arg):
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
                arg = user.name
            else:
                arg = arg.replace("@","")
            #get arg(server) info details a send that
            info = await self.client.get_fry_meta(arg,DATA["server_list"][arg])
            message ="```asciidoc\n"
            message+="= "+info["name"]+" =\n\n"
            message+="== Pack Information == \n"
            message+="Pack Name :: "+info.get("pack_name","")+"\n"
            message+="Pack Version :: "+info.get("pack_version","")+"\n"
            message+="Pack Url :: "+info.get("pack_url","")+"\n"
            message+="Pack Launcher :: "+info.get("pack_launcher","")+"\n"
            message+="Pack Description :: "+info.get("pack_long_description","")+"\n\n"
            message+="== Connection Info == \n"
            message+="Server Adresse :: "+info.get("server_adress","")+"\n\n"
            message+="== Game Info == \n"
            message+="Game :: "+info.get("game","")+"\n"
            message+="Game Version :: "+info.get("game_version","")+"\n"
            message+="Game Url :: "+info.get("game_url","")+"\n"
            message +="```"
            
            author = ctx.message.author
            await author.send(message)
            await ctx.message.add_reaction('👍')
        else:
            #send complete server list info
            methods = []
            for name, server in DATA["server_list"].items():
                methods.append(self.client.get_fry_meta(name,server))
            
            data = await asyncio.gather(*methods)
            data.sort(key=lambda s: s["name"])
            #displaying data
            embed=discord.Embed(title="Servers", color=color)
            for info in data:
                try:
                    message = info["pack_name"]+" ("+info["pack_version"]+") \n"
                    for player in info["players_online"]:
                        message += player+", "
                    if info["status"] == "RUNNING":
                        embed.add_field(name="🟢 "+info["name"], value=message, inline=False)
                    elif info["status"] == "STARTING":
                        embed.add_field(name="🟡 "+info["name"], value=message, inline=False)
                    else:
                        embed.add_field(name="🔴 "+info["name"], value=message, inline=False)
                except:
                    embed.add_field(name="‼ "+info["name"], value="Meta data is missing!", inline=False)
            embed.set_footer(text="!s <servername> for more details")
            await ctx.send(embed=embed)
                


    #connection info command
    @commands.command(description='Get connection instructions')
    async def c(self, ctx, arg=None):
        """server connection info"""
        
        #displaying data
        if arg is not None:
            pattern = re.compile("<@!*[0-9]*>")
            if pattern.match(arg):
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
                arg = user.name
            else:
                arg = arg.replace("@","")
            info = await self.client.get_fry_meta(arg,DATA["server_list"][arg])
            embed=discord.Embed(title="Detailed Connection Info", color=color)
            embed.add_field(name="\u200b", value = "***"+info["name"]+":***",inline=False)
            embed.add_field(name="✅ By redirect name:", value =info["server_hostname"], inline=False)
            embed.add_field(name="🆗 By hostname:", value ="breakfastcraft.com:"+info["server_port"],inline=False)
            embed.add_field(name="⚠  By IP:", value =info["server_ip"]+":"+info["server_port"], inline=False)
            
        else:
            methods = []
            for name, server in DATA["server_list"].items():
                methods.append(self.client.get_fry_meta(name,server))
        
            data = await asyncio.gather(*methods)
            data.sort(key=lambda s: s["name"])
            message = "In your game, click add server. In the server connection, set the name of the server you want to connect. \n"
            message += "Then you can pick one of the three ways to connect to the server. Prefered method is by ridirect name."

            embed=discord.Embed(title="Connection Info", color=color)
            embed.add_field(name="Instructions:",value=message)
            for info in data:
                
                #embed.add_field(name="~~                                                          ~~",value='\u200b', inline=False)
                embed.add_field(name="\u200b", value = "***"+info["name"]+":***",inline=False)
                try:
                    embed.add_field(name="✅ By redirect name:", value =info["server_hostname"], inline=False)
                except:
                    embed.add_field(name="‼ Sorry!", value="Info missing! Contact an admin!", inline=False)
            embed.set_footer(text = "!c <servername> for more details")
        author = ctx.message.author
        await author.send(embed=embed)
        await ctx.message.add_reaction('👍')
        

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
        """List the server reboot schedules"""
        message=""
        for line in self.client.schedule():
            message += "{server} : {time} \n".format(server=line[0], time=line[1])
        embed=discord.Embed(title="Reboot Schedule", color=color )
        embed.add_field(name="⏲",value=message,inline=False)
        await ctx.send(embed=embed)