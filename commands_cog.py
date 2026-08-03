import discord
import random
import re
import asyncio
from discord.ext import commands
from loguru import logger
from discord.utils import get
from datetime import datetime
import pytz

color=0x00ff00


async def member_allowed(ctx):
    if ctx.guild is None:
        return False
    if ctx.author.guild_permissions.administrator:
        return True
    member_role_id = ctx.bot.runtime_config.require_int("memberRoleID")
    return any(role.id == member_role_id for role in ctx.author.roles)


def member_only():
    return commands.check(member_allowed)

class CommandsCog(commands.Cog, name='Commands'):
    def __init__(self, client):
        self.client = client

    def _servers(self):
        return self.client.servers.list_enabled()

    #status command, s for short, because people are lazy.
    @commands.command(description='Get info about one or all the Minecraft servers')
    @member_only()
    async def s(self, ctx, arg=None):
        """Get info about one or all the Minecraft servers"""
        if arg is not None:
            pattern = re.compile("<@!*[0-9]*>")
            if pattern.match(arg):
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
                arg = user.name.lower()
            else:
                arg = (arg.replace("@","")).lower()
            #get arg(server) info details a send that
            info = await self.client.get_fry_meta(arg)
            if info:
                message ="```asciidoc\n"
                message+="= "+info["name"]+" =\n\n"
                message+="== Pack Information == \n"
                message+="Pack Name :: "+info.get("pack_name","")+"\n"
                message+="Pack Version :: "+info.get("pack_version","").lstrip()+"\n"
                message+="Pack Url :: "+info.get("pack_url","")+"\n"
                message+="Pack Launcher :: "+info.get("pack_launcher","")+"\n"
                message+="Pack Description :: "+info.get("pack_long_description","")+"\n\n"
                message+="== Connection Info == \n"
                message+="Redirect Address :: "+info.get("server_hostname","")+"\n"
                message+="Host server :: "+info.get("server_address","")+"\n"
                message+="Server port :: "+info.get("server_port","")+"\n"
                message+="Users Online :: "+", ".join(info["players_online"])+"\n\n"
                message+="== Game Info == \n"
                message+="Game :: "+info.get("game","")+"\n"
                message+="Game Version :: "+info.get("game_version","").lstrip()+"\n"
                message+="Game Url :: "+info.get("game_url","")+"\n"
                message +="```"
                
                author = ctx.message.author
                await author.send(message)
                await ctx.message.add_reaction('👍')
        else:
            #send complete server list info
            methods = []
            for server in self._servers():
                methods.append(self.client.get_fry_meta(server.name))
            
            data = await asyncio.gather(*methods)
            data.sort(key=lambda s: "" if not s else s.get("name"))
            #displaying data
            embed=discord.Embed(title="Servers", color=color)
            for info in data:
                if info:
                    try:
                        message = info["pack_name"]+" ("+info["pack_version"].lstrip()+") \n"
                        message += "Online: "+", ".join(info["players_online"])
                        if info["status"] == "RUNNING":
                            embed.add_field(name="🟢 "+info["name"], value=message, inline=False)
                        elif info["status"] == "STARTING":
                            embed.add_field(name="🟡 "+info["name"], value=message, inline=False)
                        else:
                            embed.add_field(name="🔴 "+info["name"], value=message, inline=False)
                    except:
                        embed.add_field(name="‼ "+info["name"], value="Meta data is missing!", inline=False)
            embed.set_footer(text="!s <servername> for more details, !o for detail online.")
            await ctx.send(embed=embed)
                
    #last seen commands
    @commands.command(description='Who was last on the servers')
    @member_only()
    async def ls(self, ctx, username: str = commands.parameter(default=None, description=":in game name used in minecraft")):
        """Shows last seen information for players.
        Usage:
        !ls             Show last players on servers.
        !ls <ign>  Show last seen information for a specific player.
        """
        message = "```asciidoc\n"
        if username is None:
            servers = self._servers()
            snapshots = await asyncio.gather(
                *(self.client.get_fry_meta(server.name) for server in servers)
            )
            for server, info in zip(servers, snapshots):
                message += "= " + server.name.upper() + " =\n"
                players = info.get("players_online", {}) if info else {}
                for player, details in players.items():
                    duration = details.get("duration", "unknown") if isinstance(details, dict) else "unknown"
                    message += f"{player} :: online for {duration} min.\n"
        else:
            last_seen = self.client.tracking_service.last_seen(username)
            message += f"{username} :: {str(last_seen)}\n"
        
        message += "```"
        await ctx.send(message)


    #get online users
    @commands.command(description='who is currently online')
    @member_only()
    async def o(self, ctx):
        """Who is currently online and for how long."""
        methods = []
        for server in self._servers():
            methods.append(self.client.get_fry_meta(server.name))
            
        data = await asyncio.gather(*methods)
        data.sort(key=lambda s: "" if not s else s.get("name"))

        message ="```asciidoc\n"
        for info in data:
            if info:
                message += "= "+info["name"].strip()+" =\n"
                try:
                    for player in info["players_online"]:
                        message += "     "+player+" :: "+str(info["players_online"][player]["duration"])+" min\n"
                except:
                    pass        
        message += "```"
        await ctx.send(message)


    #connection info command
    @commands.command(description='Get connection instructions')
    @member_only()
    async def c(self, ctx, arg=None):
        """server connection info"""
        
        #displaying data
        if arg is not None:
            pattern = re.compile("<@!*[0-9]*>")
            if pattern.match(arg):
                user = get(self.client.get_all_members(), id= int(re.sub(r'[<@!>]', '', arg)))
                arg = user.name.lower()
            else:
                arg = (arg.replace("@","")).lower()
            info = await self.client.get_fry_meta(arg)
            embed=discord.Embed(title="Detailed Connection Info", color=color)
            embed.add_field(name="\u200b", value = "***"+info["name"]+":***",inline=False)
            embed.add_field(name="✅ By redirect name:", value =info["server_hostname"], inline=False)
            embed.add_field(name="🆗 By hostname:", value =info["server_address"]+info["server_port"],inline=False)
            embed.add_field(name="⚠  By IP:", value =info["server_ip"]+":"+info["server_port"], inline=False)
            
        else:
            methods = []
            for server in self._servers():
                methods.append(self.client.get_fry_meta(server.name))
        
            data = await asyncio.gather(*methods)
            data.sort(key=lambda s: s["name"])
            message = "In your game, click add server. In the server connection, set the name of the server you want to connect. \n"
            message += "Then you can pick one of the three ways to connect to the server. Preferred method is by redirect name."

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
    
    # schedule command
    @commands.command(description='List the server reboot schedules\n\nUsage: !schedule <timezone>\n\nExample: !schedule America/New_York')
    @member_only()
    async def schedule(self, ctx, timezone: str):
        """List the server reboot schedules"""
        message = ""
        output_timezone = pytz.timezone(timezone)

        if timezone is None:
            await ctx.send("Please provide the timezone argument. Example: !schedule America/New_York")
            return

        for schedule_info in self.client.runtime_config.schedules.list_enabled():
            reboot_time_str = schedule_info.time_value
            reboot_time_utc = datetime.fromisoformat(reboot_time_str).astimezone(pytz.UTC)
            reboot_time_local = reboot_time_utc.astimezone(output_timezone)
            formatted_time = reboot_time_local.strftime('%H:%M:%S')

            message += f"{schedule_info.server_name}: {formatted_time} ({schedule_info.frequency})\n"

        embed = discord.Embed(title="Reboot Schedule", color=color)
        embed.add_field(name="Server Reboot Times", value=message, inline=False)
        await ctx.send(embed=embed)

    @commands.command(brief='Rolls dice for DND games', description='Rolls dice for DND games. Format: !roll NdS')
    @member_only()
    async def roll(self, ctx, dice: str = commands.parameter(default="1d100", description="<NdS> exemple: 3d4")):
        """Rolls dice for DND games where N is between 1 and 10 and S is a valid DND dice side (4, 6, 8, 10, 12, 20, or 100). Only 1d100 is allowed."""
        try:
            num_dice, dice_sides = map(int, dice.split('d'))
            valid_sides = [4, 6, 8, 10, 12, 20, 100]  # Added 100 for d100
            max_num_dice = 10  # Adjust this limit as needed

            if num_dice < 1 or num_dice > max_num_dice or dice_sides not in valid_sides:
                raise ValueError

            rolls = [random.randint(1, dice_sides) for _ in range(num_dice)]
            total = sum(rolls)

            percentage = 0
            if num_dice == 2 and dice_sides == 10:
                percentage = (rolls[0] * 10) + rolls[1]   
            elif dice_sides == 100:
                percentage = rolls[0] 
            roll_result = ', '.join(str(roll) for roll in rolls)
            if percentage:
                roll_result += f' : {percentage}%'
            
            await ctx.send(f"Rolled {num_dice}d{dice_sides}: {roll_result} (Total: {total})")

        except ValueError:
            await ctx.send(f"Invalid input. Please use the format `!roll NdS` where N is between 1 and {max_num_dice} and S is a valid DND dice side (4, 6, 8, 10, 12, 20, or 100). Only 1d100 is allowed.")
