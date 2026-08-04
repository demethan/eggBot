import discord
import aiohttp
import random
import re
import asyncio
from discord.ext import commands
from loguru import logger
from discord.utils import get
from datetime import datetime, timezone
import pytz
from minecraft_api import validate_minecraft_username

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
    def __init__(self, client, application_service=None):
        self.client = client
        self.application_service = application_service

    def _servers(self):
        return self.client.servers.list_enabled()

    def _hours_embed(self, minecraft_name=None):
        week_start, week_end, players = self.client.tracking_service.weekly_hours(
            minecraft_name
        )
        embed = discord.Embed(
            title=f"Player hours — week of {week_start:%Y-%m-%d}", color=color
        )
        if not players:
            embed.description = (
                f"No tracked sessions for `{minecraft_name}` this week."
                if minecraft_name
                else "No tracked sessions this week."
            )
        for player in players[:25]:
            servers = ", ".join(
                f"{name}: {hours:.1f} h"
                for name, hours in player.server_hours.items()
            )
            open_note = (
                f"\nOpen sessions: {player.open_sessions}"
                if player.open_sessions
                else ""
            )
            embed.add_field(
                name=f"{player.player_name}: {player.total_hours:.1f} h",
                value=(servers or "No server breakdown") + open_note,
                inline=False,
            )
        embed.set_footer(
            text=f"{week_start:%Y-%m-%d %H:%M %Z} to {week_end:%Y-%m-%d %H:%M %Z}"
        )
        return embed

    async def _request_hours_association(self, ctx):
        try:
            dm = await ctx.author.send(
                "I do not have a Minecraft IGN associated with your Discord account.\n\n"
                "Reply with **your own Minecraft IGN** to create a permanent association. "
                "Members can only view hours for their associated IGN. If you enter the "
                "wrong IGN, an admin must correct it. Type `cancel` to stop."
            )
        except discord.HTTPException:
            await ctx.send("I could not DM you. Enable DMs and run `!hours` again.")
            return None

        def check(message):
            return message.author.id == ctx.author.id and message.channel.id == dm.channel.id

        for _ in range(3):
            try:
                reply = await self.client.wait_for("message", timeout=120, check=check)
            except asyncio.TimeoutError:
                await ctx.author.send("IGN association timed out. No association was created.")
                return None
            ign = reply.content.strip()
            if ign.casefold() == "cancel":
                await ctx.author.send("IGN association cancelled.")
                return None
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                valid = await validate_minecraft_username(
                    session, ign, max_retries=3, retry_delay=1
                )
            if valid is True:
                try:
                    return self.application_service.record_self_reported_link(
                        discord_user_id=ctx.author.id,
                        discord_username=ctx.author.name,
                        discord_display_name=ctx.author.display_name,
                        minecraft_name=ign,
                    )
                except ValueError as error:
                    if str(error) == "minecraft_already_linked":
                        await ctx.author.send(
                            "That IGN is already associated with another Discord account. "
                            "Contact an admin if this is incorrect."
                        )
                    else:
                        await ctx.author.send(
                            "Your Discord account already has an IGN association. "
                            "Contact an admin to correct it."
                        )
                    return None
            if valid is None:
                await ctx.author.send(
                    "Minecraft name validation is temporarily unavailable. Try again later."
                )
                return None
            await ctx.author.send("That Minecraft IGN was not found. Try again or type `cancel`.")
        await ctx.author.send("Too many invalid attempts. No association was created.")
        return None

    @commands.command(
        brief="Show your Minecraft play hours for the current week",
        description=(
            "Members: DM your current-week hours using your Discord-to-IGN association. "
            "Admins in the admin channel: !hours [Minecraft IGN]."
        ),
    )
    @member_only()
    async def hours(self, ctx, *, minecraft_name=None):
        """Show weekly hours. Members can only view their associated IGN; admins may use !hours [IGN] in the admin channel."""
        permissions = ctx.author.guild_permissions
        admin_mode = (
            ctx.channel.id == self.client.runtime_config.require_int("adminChannelID")
            and (permissions.administrator or permissions.manage_roles)
        )
        if admin_mode:
            await ctx.send(embed=self._hours_embed(minecraft_name))
            return
        if minecraft_name:
            await ctx.author.send(
                "Members cannot look up an IGN directly. Run `!hours` without a name; "
                "I will use the IGN associated with your Discord account."
            )
            return
        if self.application_service is None:
            await ctx.author.send("Player associations are temporarily unavailable.")
            return
        links = self.application_service.find_player_links(str(ctx.author.id))
        link = links[0] if links else await self._request_hours_association(ctx)
        if link is None:
            return
        await ctx.author.send(embed=self._hours_embed(link.minecraft_name))
        try:
            await ctx.message.add_reaction("✅")
        except discord.HTTPException:
            pass

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
                recent = self.client.tracking_service.recent_players(arg)
                if recent:
                    lines = []
                    for player, activity_at, online in recent:
                        if online:
                            activity = "online now"
                        else:
                            timestamp = datetime.fromisoformat(activity_at)
                            if timestamp.tzinfo is None:
                                timestamp = timestamp.replace(tzinfo=timezone.utc)
                            activity = f"<t:{int(timestamp.timestamp())}:R>"
                        lines.append(f"**{player}:** {activity}")
                    message += "\n**Recent player activity**\n" + "\n".join(lines)
                
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
                    except (KeyError, TypeError, AttributeError):
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
                except (KeyError, TypeError):
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
                except (KeyError, TypeError):
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
