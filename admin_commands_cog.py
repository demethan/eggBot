import discord
import random
import re
import aiohttp
import asyncio
from discord.ext import commands
from config import DATA, save_data
from loguru import logger
from discord.utils import get
from datetime import datetime, timedelta
import pytz
color=0x00ff00

#get admins
def admin_only():
    async def predicate(ctx):
        return ctx.channel.id == DATA["adminChannelID"]
    return commands.check(predicate)

class AdminCommandsCog(commands.Cog, name='AdminCommands'):
    def __init__(self, client):
        self.client = client
        self.client.applications = {}
    
    @commands.command()
    @admin_only()
    async def mc(self,ctx):
        await ctx.send("Do I look like Cakebot?")

    @commands.command(description='Update dictionary values for IDs.', rest_is_raw=True)
    @admin_only()
    async def updateids(self, ctx, key, value):
        """Admin: Update dictionary values for IDs. Usage: !updateids <key> <value>"""
        if key.lower() == "supportchannelid":
            DATA["supportChannelID"] = int(value)
        elif key.lower() == "adminchannelid":
            DATA["adminChannelID"] = int(value)
        elif key.lower() == "memberroleid":
            DATA["memberRoleID"] = int(value)
        elif key.lower() == "frybotroleid":
            DATA["fryBotRoleID"] = int(value)
        else:
            await ctx.send("Invalid key. Available keys: supportChannelID, adminChannelID, memberRoleID, fryBotRoleID")
            return

        save_data()
        await ctx.send("Dictionary value updated successfully!")


    @commands.command(description='Get all the meta data of a specific server.', rest_is_raw=True)
    @admin_only()
    async def getmeta(self,ctx,arg):
        """Admin: Get all the meta data of a specific server"""
        pattern = re.compile("<@!*[0-9]*>")
        if pattern.match(arg): #to convert discord id for server name if user call command with @<server>
            user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
            arg = user.name.lower()
        else:
            arg = arg.replace("@","").lower()
        info = await self.client.get_fry_meta(arg,DATA["server_list"][arg])
        if not info:#function return false if it can't connect to api
            await ctx.send("Fry doesn't seem to be running. No connection!")
            return
        message = ""
        for key in info:#display all meta data except players online.
            if key == "players_online":
                continue
            value = info[key]
            message += key+" : "+value+"\n"
        embed=discord.Embed(title="Meta Data",color=color)
        embed.add_field(name="💬",value=message)
        embed.set_footer(text="!setmeta for more info.")
        await ctx.send(embed=embed)
        
    
    #set things for bot to respond to support channel. 
    @commands.command(description='Set or change the apply url, connection url and welcome message of the concierge', rest_is_raw=True)
    @admin_only()
    async def set(self, ctx, arg, *,text):
        """Admin: Change the apply url or the Welcome message. usage !set <applyUrl|joinMessage> <text>. For the Join message you have two parameter {member} {applyUrl} to work into your message. """
        
        set_command_dict = {"applyurl":"applyUrl", "joinmessage":"joinMessage"  }
        
        try:
            arg = set_command_dict.get(arg.lower(), None)        
            if arg is not None and text !="":
                DATA[arg] = text.strip()
                save_data()
                await ctx.send("Saved!")
            else:
                await ctx.send(" !set argument is not valid. usage !set <applyUrl|connectUrl|joinMessage> <text>.")
        except Exception as inst:
                logger.exception(inst)
                await ctx.send("you are missing something, try again!")
                return

    #display how to for setting the FryBot's meta data.
    @commands.command(description='Not a command. Setting meta is done individually to the @server bot.', rest_is_raw=True)
    @admin_only()
    async def setmeta(self, ctx):
        """Setting meta is done individually to the @server bot. More Info here!"""
        with open('meta_instruction.txt', 'r') as file:
            message = file.read()
        embed=discord.Embed(title="Detailed Instruction", color=0x00ff00)
        embed.add_field(name="Info:", value=message, inline=False)
        await ctx.send(embed=embed)

    #add command is to add a server.
    @commands.command(description='Add a server to eggbot.', rest_is_raw=True)
    @admin_only()
    async def add(self, ctx, host, user, password):
        """Admin: Setup a server. The server name will be whatever you setup in the fry config. usage !add <host> <user> <password> The name meta value of the FryBot has to be set first. !setmeta for more info."""
        logger.debug(host)
        logger.debug(user)
        logger.debug(password)
        if host is not None and user is not None and password is not None:
            try: #tries to connect to the api site of the frybot.
                host = host.strip('/')
                token = await self.client.get_token_by_auth(host, user, password)
                if token is not None:
                    await ctx.send("Token acquired!")
                    await ctx.send(str(token["data"]["token"]))
            except Exception as inst:
                logger.exception(inst)
                await ctx.send("something went wrong, check the url, username and password")
                return
            try:#uses the token to get the name from the meta data.
                headers = {'Authorization': 'Bearer '+token["data"]["token"]}
                async with aiohttp.ClientSession() as session:
                    async with session.get(host+'/v1/meta', headers=headers) as response:
                        result =  await response.json()
                        name = result['data']['name']
                        name = name.replace(" ","").lower()
                        await ctx.send(name+" Found!")
            except Exception as inst:
                logger.exception(inst)
                await ctx.send("Failed to get server name, Please set Fry's name in the meta data with the Fry commands. Ex. @FryBot !meta set name <FryBot> ")
                return

            try:
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password, 'token':token["data"]["token"]}
            except KeyError:
                DATA["server_list"] = {}
                DATA["server_list"][name] = {"endpoint":host,"id":user,"password":password, 'token':token["data"]["token"]}
            save_data()
            await ctx.send("Added successfully !")
        else:
            await ctx.send(" !set argument is not valid. usage !add <servername> <host> <user> <password>. Ex: !add https://localhost:4321 admin 12345abc")

    #remove command is to remove a server from the DATA.
    @commands.command(description='remove a server from eggbot.', rest_is_raw=True)
    @admin_only()
    async def remove(self, ctx, key):
        """Admin: Remove a server from eggbot. Use the server name as displayed in !s command. usage !remove <servername>"""

        if key is not None:
            pattern = re.compile("<@!*[0-9]*>")
            if pattern.match(key): #to convert discord id for server name if user call command with @<server>
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', key)))
                key = user.name.lower()
            else:
                key = key.replace("@","").lower()
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
    
    # set_schedule command
    @commands.command(description='Set the server reboot schedule')
    @admin_only()
    async def set_schedule(self, ctx, server: str, time: str, frequency: str, timezone: str):
        """Set the server reboot schedule"""
        # Convert time to a valid time format
        try:
            reboot_time = datetime.strptime(time, '%H:%M:%S').time()
        except ValueError:
            await ctx.send("Invalid time format. Please use the format: HH:MM:SS")
            return

        # Get the current date
        now = datetime.now().date()

        # Create the datetime object with the current date and specified time
        reboot_datetime = datetime.combine(now, reboot_time)

        # Convert the reboot_datetime to UTC
        utc_timezone = pytz.timezone('UTC')
        reboot_datetime_utc = reboot_datetime.astimezone(utc_timezone)

        # Convert the provided timezone string to a timezone object
        try:
            author_timezone = pytz.timezone(timezone)
        except pytz.UnknownTimeZoneError:
            await ctx.send("Invalid timezone. Please provide a valid timezone.")
            return

        # Convert reboot time to the user's timezone for display
        reboot_datetime_local = reboot_datetime_utc.astimezone(author_timezone)

        # Ensure that the 'reboot_schedule' key exists in the DATA dictionary
        if 'reboot_schedule' not in DATA:
            DATA['reboot_schedule'] = {}

        # Convert reboot_datetime_utc to string representation
        reboot_time_str = reboot_datetime_utc.isoformat()

        # Store the schedule in the DATA dictionary
        DATA['reboot_schedule'][server] = {
            'time': reboot_time_str,
            'frequency': frequency.lower(),
            'timezone': timezone
        }
        save_data()  # Save the updated schedule to the data file

        await ctx.send(f"The reboot schedule for {server} has been set to {reboot_datetime_local.strftime('%H:%M:%S')} ({frequency}) in the {timezone} timezone.")
