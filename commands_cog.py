import discord
import random
import re
import aiohttp
import requests
import asyncio
from discord.ext import commands
from config import DATA, save_data


class CommandsCog(commands.Cog):
    def __init__(self, client):
        self.client = client

    #set command to change urls and messages
    @commands.command(description='Set or change the apply url, connection url and welcome message of the concierge', rest_is_raw=True)
    async def set(self, ctx, arg, *,text):
        """Admin: Change the apply url, connection url or the Welcome message. usage !set <applyUrl|connectUrl|joinMessage> <text>. """
        
        set_command_dict = {"applyurl":"applyUrl","connecturl":"connectUrl", "joinmessage":"joinMessage"  }
        
        if not self.client.is_allowed(ctx):
            return

        arg = set_command_dict.get(arg.lower(), None)        
        if arg is not None and text is not None:
            DATA[arg] = text.strip()
            save_data()
            await ctx.send("Saved!")
        else:
            await ctx.send(" !set argument is not valid. usage !set <applyUrl|connectUrl|joinMessage> <text>.")
    
    #setmeta command to store meta data in fry
    @commands.command(description='Set or change fry server information like packname, packversion. Done individually to the @server bot', rest_is_raw=True)
    async def setmeta(self, ctx, server, key, meta):
        """Set or change fry server information like packname, packversion. Done individually to the @server bot. usage @server !set <packname|version> <text> """
        

    #add command is to add a server.
    @commands.command(description='Add a server to eggbot.', rest_is_raw=True)
    async def add(self, ctx, host, user, password):
        """Admin: Setup a server. The server name will be what ever you setup in the fry config. usage !add <host> <user> <password>"""

        if not self.client.is_allowed(ctx):
            return        

        if host is not None and user is not None and password is not None:
            try:
                data={"endpoint":host,"id":user,"password":password}
                token = await self.client.get_token(data)
                await ctx.send("Token aquired!")
            except Exception as inst:
                print(type(inst))    # the exception instance
                print(inst.args)     # arguments stored in .args
                print(inst)
                await ctx.send("something went wrong, check the url, username and password")
            try:
                params = {'Authorization': 'Bearer '+token}
                async with aiohttp.ClientSession() as session:
                    async with session.get(host+'/v1/meta', params=params) as response:
                        result =  await response.json()
                        name = result['name']
            except:
                return False

            try:
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password}
                DATA["server_list"][name]["token"] = token
            except KeyError:
                DATA["server_list"] = {}
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password}
                DATA["server_list"][name]["token"] = token 
            save_data()
            await ctx.send("Saved!")
        else:
            await ctx.send(" !set argument is not valid. usage !add <servername> <host> <user> <password>. exemple !add https://localhost:4321 admin 12345abc")

    #remove command is to add a server.
    @commands.command(description='remove a server from eggbot.', rest_is_raw=True)
    async def remove(self, ctx, key):
        """Admin: Remove a server from eggbot. Use the server name as displayed in !s command. usage !remove <servername>"""

        if not self.client.is_allowed(ctx):
            return        

        if key is not None:
            await ctx.send(key+ " will be removed from the list, confirm with YES ")
            msg = await self.client.wait_for('message')
            if msg.content=="YES" and ctx.message.author == msg.author:
                try:
                    del DATA["server_list"][key]
                    save_data()
                    await ctx.send(key+" as been removed!")
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
            await ctx.send(arg+' info: blah blah blah')
        else:
            #send complete server list info
            message = "Server List: \n"
            for serverData in DATA["server_list"]:
                status,serverName,packName,version = self.client.get_fry_meta(serverData)
                message += status+":"+serverName+"\n"
                message += packname+" ("+packversion+")"
            await ctx.send(message)

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
    
    #ban command
    @commands.command(description='ban a user from the minecraft servers')
    async def ban(self, ctx, ign: str):
        """Admin: ban a user from the minecraft servers."""
        if not self.client.is_allowed(ctx):
            return
        await ctx.send('Deploying ban hammer!')
    
    #schedule
    @commands.command(description='list the server reboot schedules')
    async def schedule(self, ctx):
        """list the server reboot schedules"""
        message = "Server reboot schedule \n"
        for line in self.client.schedule():
            message += "{server} : {time} \n".format(server=line[0], time=line[1])
        await ctx.send(message)