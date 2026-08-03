import discord
import re
import aiohttp
import asyncio
from discord.ext import commands
from config import DATA
from loguru import logger
from discord.utils import get
from datetime import datetime, timedelta, timezone
import pytz
color=0x00ff00

STAT_PERIODS = {
    "week": ("Last 7 days", timedelta(days=7)),
    "month": ("Last 30 days", timedelta(days=30)),
    "year": ("Last 365 days", timedelta(days=365)),
    "all": ("All tracked time", None),
}

REFRESH_STATUS = {
    "active": "🟢 Active — players in the last 14 days",
    "refresh_watch": "🟡 Refresh watch — no players in 14 days",
    "refresh_candidate": "🔴 Refresh candidate — no players in 28 days",
    "insufficient_data": "⚪ Insufficient history for a 28-day decision",
    "no_pack_data": "⚪ No current pack history",
}


def parse_stats_scope(arguments):
    tokens = (arguments or "").split()
    period = "month"
    if tokens and tokens[-1].casefold() in STAT_PERIODS:
        period = tokens.pop().casefold()
    server_name = " ".join(tokens) or None
    label, duration = STAT_PERIODS[period]
    now = datetime.now(timezone.utc)
    since = now - duration if duration is not None else None
    return server_name, label, now, since


def engagement_summary(item):
    window = item.window(7)
    return (
        f"{window.active_players} active • {window.person_hours:.1f} person-h • "
        f"score {window.participation_score:.1f}"
    )


def engagement_details(item):
    lines = []
    for window in item.windows:
        lines.append(
            f"**{window.days} days:** {window.active_players} active • "
            f"{window.person_hours:.1f} person-h • "
            f"score {window.participation_score:.1f}"
        )
    last_activity = (
        f"<t:{int(item.last_activity_at.timestamp())}:F> "
        f"(<t:{int(item.last_activity_at.timestamp())}:R>)"
        if item.last_activity_at
        else "No recorded play"
    )
    lines.extend(
        (
            f"**Last activity:** {last_activity}",
            f"**Refresh status:** {REFRESH_STATUS[item.refresh_status]}",
        )
    )
    return "\n".join(lines)

#get admins
def admin_only():
    async def predicate(ctx):
        return ctx.channel.id == DATA["adminChannelID"]
    return commands.check(predicate)

class AdminCommandsCog(commands.Cog, name='AdminCommands'):
    def __init__(
        self, client, application_service=None, tracking_service=None, *,
        servers=None, settings=None, schedules=None,
    ):
        self.client = client
        self.application_service = application_service
        self.tracking_service = tracking_service
        self.servers = servers
        self.settings = settings
        self.schedules = schedules

    @commands.command(
        brief='Show Minecraft play hours for the current week',
        description=(
            'Admin: Show current-week hours for all players or one IGN. '
            'Usage: !hours [Minecraft IGN]. Week starts Monday in America/Montreal.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def hours(self, ctx, *, minecraft_name=None):
        """Admin: Show current-week Minecraft play hours.

        Usage: !hours or !hours <Minecraft IGN>
        Requires Manage Roles permission and must be used in the admin channel.
        """
        if self.tracking_service is None:
            await ctx.send("Player-hour tracking is unavailable.")
            return
        week_start, week_end, players = self.tracking_service.weekly_hours(
            minecraft_name
        )
        title = f"Player hours — week of {week_start:%Y-%m-%d}"
        embed = discord.Embed(title=title, color=color)
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
        await ctx.send(embed=embed)

    @commands.command(
        brief='Show tracked activity and current pack by server',
        description=(
            'Admin: Show active players, person-hours, participation score, refresh '
            'status, and current pack. Usage: !serverstats [server] '
            '[week|month|year|all]. '
            'Defaults to month; server-specific results are sent by DM.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def serverstats(self, ctx, *, arguments=None):
        """Admin: Show tracked activity and current pack by server.

        Usage: !serverstats [server] [week|month|year|all]
        Defaults to the last 30 days. Server-specific results are sent by DM.
        Requires Manage Roles permission and must be used in the admin channel.
        """
        if self.tracking_service is None:
            await ctx.send("Server statistics are unavailable.")
            return
        server_name, period_label, now, since = parse_stats_scope(arguments)
        statistics = self.tracking_service.server_statistics(
            server_name, now=now, since=since
        )
        engagement = {
            item.server_name.casefold(): item
            for item in self.tracking_service.engagement_statistics(
                server_name, now=now
            )
        }
        embed = discord.Embed(
            title=f"Server statistics — {period_label}", color=color
        )
        if not statistics:
            embed.description = f"No enabled server found for `{server_name}`."
        for item in statistics[:25]:
            if server_name is None:
                activity = engagement.get(item.server_name.casefold())
                current_pack = (
                    f"{item.current_pack} {item.current_version}"
                    if item.current_pack
                    else "No tracked pack"
                )
                embed.add_field(
                    name=item.server_name.upper(),
                    value=(
                        f"{engagement_summary(activity) if activity else 'No engagement data'}\n"
                        f"{period_label}: {item.unique_players} active • "
                        f"{item.total_hours:.1f} person-h\n"
                        f"{current_pack} • "
                        f"{REFRESH_STATUS[activity.refresh_status] if activity else 'Unknown'}"
                    ),
                    inline=False,
                )
                continue
            if item.current_pack:
                installed = (
                    f"<t:{int(item.installed_at.timestamp())}:R>"
                    if item.installed_at
                    else "unknown"
                )
                pack = (
                    f"{item.current_pack} ({item.current_version})\n"
                    f"Tracking since: {installed}"
                )
            else:
                pack = "No tracked pack installation"
            embed.add_field(
                name=item.server_name.upper(),
                value=(
                    f"**Played:** {item.total_hours:.1f} h\n"
                    f"**Sessions:** {item.sessions}\n"
                    f"**Unique players:** {item.unique_players}\n"
                    f"**Current pack:** {pack}"
                ),
                inline=False,
            )
            activity = engagement.get(item.server_name.casefold())
            if activity is not None:
                embed.add_field(
                    name="Current pack engagement",
                    value=engagement_details(activity),
                    inline=False,
                )
        embed.set_footer(
            text=(
                f"{period_label}. Participation score = Σ√(each player's hours). "
                "Statistics begin when EggBot starts tracking Fry metadata."
            )
        )
        await self._send_statistics(ctx, embed, private=server_name is not None)

    @commands.command(
        brief='Show modpack installation and play statistics',
        description=(
            'Admin: Show pack engagement, refresh status, installation duration, and '
            'tracked player-hours. '
            'Usage: !packstats [server] [week|month|year|all]. Defaults to month; '
            'server-specific results are sent by DM.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def packstats(self, ctx, *, arguments=None):
        """Admin: Show modpack installation and play statistics.

        Usage: !packstats [server] [week|month|year|all]
        Defaults to the last 30 days. Server-specific results are sent by DM.
        Requires Manage Roles permission and must be used in the admin channel.
        """
        if self.tracking_service is None:
            await ctx.send("Pack statistics are unavailable.")
            return
        server_name, period_label, now, since = parse_stats_scope(arguments)
        statistics = self.tracking_service.pack_statistics(
            server_name, now=now, since=since
        )
        engagement = {
            item.server_name.casefold(): item
            for item in self.tracking_service.engagement_statistics(
                server_name, now=now
            )
        }
        embed = discord.Embed(
            title=f"Pack statistics — {period_label}", color=color
        )
        if not statistics:
            embed.description = (
                f"No tracked pack installations for `{server_name}`."
                if server_name
                else "No tracked pack installations yet."
            )
        displayed_statistics = statistics
        if server_name is None:
            displayed_statistics = [
                item for item in statistics if item.removed_at is None
            ]
            if statistics and not displayed_statistics:
                embed.description = "No current pack installations are open."
        for item in displayed_statistics[:25]:
            if server_name is None:
                activity = engagement.get(item.server_name.casefold())
                embed.add_field(
                    name=item.server_name.upper(),
                    value=(
                        f"{item.pack_name} {item.version}\n"
                        f"{engagement_summary(activity) if activity else 'No engagement data'}\n"
                        f"{period_label}: {item.unique_players} active • "
                        f"{item.played_hours:.1f} person-h • "
                        f"{REFRESH_STATUS[activity.refresh_status] if activity else 'Unknown'}"
                    ),
                    inline=False,
                )
                continue
            state = "Current" if item.removed_at is None else "Removed"
            start_label = "First observed" if item.baseline else "Installed"
            duration_label = "Tracked duration" if item.baseline else "Installed duration"
            ended = (
                "still installed"
                if item.removed_at is None
                else f"<t:{int(item.removed_at.timestamp())}:f>"
            )
            embed.add_field(
                name=f"{item.server_name.upper()} — {item.pack_name} {item.version}",
                value=(
                    f"**Status:** {state}\n"
                    f"**{start_label}:** <t:{int(item.installed_at.timestamp())}:f> to {ended}\n"
                    f"**{duration_label}:** {item.installed_hours:.1f} h\n"
                    f"**Played:** {item.played_hours:.1f} player-hours\n"
                    f"**Sessions:** {item.sessions}\n"
                    f"**Unique players:** {item.unique_players}"
                ),
                inline=False,
            )
        if server_name is not None and engagement:
            activity = next(iter(engagement.values()))
            existing_description = embed.description or ""
            engagement_description = (
                "**Current pack engagement**\n" + engagement_details(activity)
            )
            embed.description = "\n\n".join(
                part for part in (existing_description, engagement_description) if part
            )
        if len(displayed_statistics) > 25:
            embed.set_footer(
                text=(
                    f"Showing 25 of {len(displayed_statistics)} installations. "
                    "Filter by server."
                )
            )
        else:
            embed.set_footer(
                text=(
                    f"{period_label}. Statistics begin when EggBot starts tracking "
                    "Fry metadata. Score = Σ√(each player's hours)."
                )
            )
        await self._send_statistics(ctx, embed, private=server_name is not None)

    @staticmethod
    async def _send_statistics(ctx, embed, *, private):
        if not private:
            await ctx.send(embed=embed)
            return
        try:
            await ctx.author.send(embed=embed)
            await ctx.message.add_reaction("✅")
        except discord.Forbidden:
            await ctx.send(
                "I could not send the server-specific statistics by DM. "
                "Enable direct messages and try again."
            )

    @commands.command(
        brief='Match an approved Discord user and Minecraft IGN',
        description=(
            'Admin: Find an approved player association. '
            'Usage: !whois <Discord name, mention, user ID, server display name, or Minecraft IGN>.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def whois(self, ctx, *, query):
        """Admin: Find an approved Discord account or Minecraft IGN.

        Usage: !whois <Discord name, mention, user ID, display name, or Minecraft IGN>
        Requires Manage Roles permission and must be used in the admin channel.
        """
        if self.application_service is None:
            await ctx.send("Player identity lookup is unavailable.")
            return
        normalized = re.sub(r"[<@!>]", "", query.strip())
        links = self.application_service.find_player_links(normalized)
        if not links:
            await ctx.send(f"No approved player association found for `{query}`.")
            return
        embed = discord.Embed(title="Player identity", color=color)
        for index, link in enumerate(links[:10], start=1):
            embed.add_field(
                name=f"Association {index}",
                value=(
                    f"**Minecraft IGN:** {link.minecraft_name}\n"
                    f"**Discord username:** {link.discord_username}\n"
                    f"**Server display name:** {link.discord_display_name}\n"
                    f"**Discord account:** <@{link.discord_user_id}>\n"
                    f"**Discord user ID:** `{link.discord_user_id}`\n"
                    f"**Application ID:** `{link.application_id}`\n"
                    f"**Linked:** {link.linked_at}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(
        brief='Remove a Minecraft IGN from every enabled server',
        description=(
            'Admin: Remove an IGN through every enabled FryingPan whitelist API. '
            'Usage: !unwhitelist <Minecraft IGN>. Requires reaction confirmation.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def unwhitelist(self, ctx, *, minecraft_name):
        """Admin: Remove an IGN from all enabled server whitelists.

        Usage: !unwhitelist <Minecraft IGN>
        Requires Manage Roles permission and a ✅ confirmation in the admin channel.
        """
        if self.application_service is None:
            await ctx.send("Whitelist administration is unavailable.")
            return
        minecraft_name = minecraft_name.strip()
        if not minecraft_name:
            await ctx.send("Provide a Minecraft IGN. Usage: `!unwhitelist <IGN>`")
            return
        confirmation = await ctx.send(
            f"Remove **{minecraft_name}** from every enabled server?\n"
            "✅ Confirm removal\n"
            "🔴 Cancel"
        )
        await confirmation.add_reaction("✅")
        await confirmation.add_reaction("🔴")

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == confirmation.id
                and str(reaction.emoji) in ("✅", "🔴")
            )

        try:
            reaction, _ = await self.client.wait_for(
                "reaction_add", check=check, timeout=60
            )
        except asyncio.TimeoutError:
            await confirmation.edit(content="Whitelist removal timed out. No changes made.")
            return
        if str(reaction.emoji) == "🔴":
            await confirmation.edit(content="Whitelist removal cancelled. No changes made.")
            return

        await confirmation.edit(content=f"Removing **{minecraft_name}**…")
        outcomes = await self.application_service.remove_from_whitelists(
            minecraft_name, ctx.author.id
        )
        result_lines = [
            f"**{server}:** {outcome.message}"
            for server, outcome in outcomes.items()
        ]
        failures = [
            outcome
            for outcome in outcomes.values()
            if outcome.status not in ("removed", "absent")
        ]
        embed = discord.Embed(
            title=f"Whitelist removal: {minecraft_name}",
            description="\n".join(result_lines) or "No enabled servers.",
            color=0xE74C3C if failures else 0x2ECC71,
        )
        embed.set_footer(text=f"Requested by {ctx.author}")
        await confirmation.edit(content=None, embed=embed)
        try:
            await confirmation.clear_reactions()
        except discord.HTTPException:
            logger.exception("Unable to clear whitelist removal confirmation reactions")

    @commands.command(
        brief='Add a Minecraft IGN to every enabled server',
        description=(
            'Admin: Add an IGN through the shared FryingPan whitelist API. '
            'Usage: !whitelist <Minecraft IGN>. Requires reaction confirmation.'
        ),
    )
    @admin_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def whitelist(self, ctx, *, minecraft_name):
        """Admin: Add an IGN to the shared server whitelist.

        Usage: !whitelist <Minecraft IGN>
        Requires Manage Roles permission and a ✅ confirmation in the admin channel.
        """
        if self.application_service is None:
            await ctx.send("Whitelist administration is unavailable.")
            return
        minecraft_name = minecraft_name.strip()
        if not minecraft_name:
            await ctx.send("Provide a Minecraft IGN. Usage: `!whitelist <IGN>`")
            return
        confirmation = await ctx.send(
            f"Add **{minecraft_name}** to the shared server whitelist?\n"
            "✅ Confirm addition\n"
            "🔴 Cancel"
        )
        await confirmation.add_reaction("✅")
        await confirmation.add_reaction("🔴")

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == confirmation.id
                and str(reaction.emoji) in ("✅", "🔴")
            )

        try:
            reaction, _ = await self.client.wait_for(
                "reaction_add", check=check, timeout=60
            )
        except asyncio.TimeoutError:
            await confirmation.edit(content="Whitelist addition timed out. No changes made.")
            return
        if str(reaction.emoji) == "🔴":
            await confirmation.edit(content="Whitelist addition cancelled. No changes made.")
            return

        await confirmation.edit(content=f"Adding **{minecraft_name}**…")
        outcomes = await self.application_service.add_to_whitelists(
            minecraft_name, ctx.author.id
        )
        result_lines = [
            f"**{server}:** {outcome.message}"
            for server, outcome in outcomes.items()
        ]
        failures = [
            outcome
            for outcome in outcomes.values()
            if outcome.status not in ("added", "present")
        ]
        embed = discord.Embed(
            title=f"Whitelist addition: {minecraft_name}",
            description="\n".join(result_lines) or "No enabled servers.",
            color=0xE74C3C if failures else 0x2ECC71,
        )
        embed.set_footer(text=f"Requested by {ctx.author}")
        await confirmation.edit(content=None, embed=embed)
        try:
            await confirmation.clear_reactions()
        except discord.HTTPException:
            logger.exception("Unable to clear whitelist addition confirmation reactions")
    
    @commands.command(description='Update a Discord channel or role ID in the database.', rest_is_raw=True)
    @admin_only()
    async def updateids(self, ctx, key, value):
        """Admin: Update dictionary values for IDs. Usage: !updateids <key> <value>"""
        setting_keys = {
            "supportchannelid": "supportChannelID",
            "adminchannelid": "adminChannelID",
            "memberroleid": "memberRoleID",
            "frybotroleid": "fryBotRoleID",
            "applicationreviewerroleid": "applicationReviewerRoleID",
        }
        canonical_key = setting_keys.get(key.casefold())
        if canonical_key is None:
            await ctx.send(
                "Invalid key. Available keys: supportChannelID, adminChannelID, "
                "memberRoleID, fryBotRoleID, applicationReviewerRoleID"
            )
            return
        try:
            setting_value = int(value)
        except ValueError:
            await ctx.send("The ID must be a number.")
            return
        DATA[canonical_key] = setting_value
        self.settings.set(canonical_key, setting_value)
        self.client.database_connection.commit()
        await ctx.send(f"`{canonical_key}` updated in the database.")


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
                self.settings.set(arg, DATA[arg])
                self.client.database_connection.commit()
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
    @commands.command(description='Add a Fry server; the API password is requested by DM.', rest_is_raw=True)
    @admin_only()
    async def add(self, ctx, host, user, password=None):
        """Admin: Add a Fry server. Usage: !add <host> <user>. EggBot requests the API password by DM and discovers the server name from Fry metadata."""
        if password is None:
            try:
                prompt = await ctx.author.send(
                    f"Reply with the Fry API password for `{host}`. Type `cancel` to stop."
                )
            except discord.HTTPException:
                await ctx.send("I could not DM you. Enable DMs and try again.")
                return

            def password_check(message):
                return message.author.id == ctx.author.id and message.channel.id == prompt.channel.id

            try:
                password_message = await self.client.wait_for(
                    "message", timeout=120, check=password_check
                )
            except asyncio.TimeoutError:
                await ctx.author.send("Server setup timed out. No changes made.")
                return
            password = password_message.content
            try:
                await password_message.delete()
            except discord.HTTPException:
                logger.warning("Unable to delete Fry password DM from user {}", ctx.author.id)
            if password.casefold() == "cancel":
                await ctx.author.send("Server setup cancelled.")
                return
        else:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                logger.warning("Unable to delete legacy !add invocation containing a password")
                await ctx.send(
                    "Warning: I could not delete the command containing the API password. "
                    "Delete it manually and use `!add <host> <user>` next time."
                )
        if host is not None and user is not None and password is not None:
            try: #tries to connect to the api site of the frybot.
                host = host.strip('/')
                token = await self.client.get_token_by_auth(host, user, password)
                if token is None:
                    await ctx.send("Authentication failed. Check the URL, username, and password.")
                    return
                await ctx.send("Authenticated with Fry.")
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
                server_id = self.servers.upsert(
                    name=name, endpoint=host, api_user=user,
                    api_password=password, api_token=token["data"]["token"],
                )
                self.client.database_connection.commit()
                DATA.setdefault("server_list", {})[name] = {
                    "endpoint": host, "id": user, "password": password,
                    "token": token["data"]["token"],
                }
                logger.info("Enabled Fry server {} (database id {})", name, server_id)
            except Exception:
                logger.exception("Unable to save Fry server configuration")
                await ctx.send("The server was found but could not be saved.")
                return
            await ctx.send(f"**{name}** was enabled and saved securely.")
        else:
            await ctx.send("Usage: `!add <Fry URL> <API user>`")

    #remove command is to remove a server from the DATA.
    @commands.command(description='Disable a server while retaining its statistics.', rest_is_raw=True)
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
            confirmation = await ctx.send(
                f"Disable **{key}**? Historical statistics will be retained."
            )
            await confirmation.add_reaction("✅")
            await confirmation.add_reaction("🔴")

            def check(reaction, user):
                return (
                    user.id == ctx.author.id
                    and reaction.message.id == confirmation.id
                    and str(reaction.emoji) in ("✅", "🔴")
                )

            try:
                reaction, _ = await self.client.wait_for(
                    'reaction_add', timeout=60, check=check
                )
            except asyncio.TimeoutError:
                await confirmation.edit(content="Server removal timed out. No changes made.")
                return
            if str(reaction.emoji) == "✅":
                try:
                    server = self.servers.get_by_name(key)
                    if server is None or not server.enabled:
                        raise KeyError(key)
                    self.servers.set_enabled(server.id, False)
                    self.client.database_connection.commit()
                    DATA["server_list"].pop(key, None)
                    DATA.get("reboot_schedule", {}).pop(key, None)
                    await ctx.send(key+" has been disabled; its history was retained.")
                    logger.warning("{} disabled in server configuration", key)
                except KeyError:
                    await ctx.send(key+" is not in the config.")
            else:
                await confirmation.edit(content="Server removal cancelled. No changes made.")
            try:
                await confirmation.clear_reactions()
            except discord.HTTPException:
                logger.exception("Unable to clear server removal confirmation reactions")
    
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

        server_record = self.servers.get_by_name(server)
        if server_record is None or not server_record.enabled:
            await ctx.send(f"Unknown enabled server: `{server}`")
            return

        # Get the current date in the supplied timezone.
        try:
            author_timezone = pytz.timezone(timezone)
        except pytz.UnknownTimeZoneError:
            await ctx.send("Invalid timezone. Please provide a valid timezone.")
            return
        now = datetime.now(author_timezone).date()

        # Create the datetime object with the current date and specified time
        reboot_datetime = author_timezone.localize(datetime.combine(now, reboot_time))

        # Convert the reboot_datetime to UTC
        utc_timezone = pytz.timezone('UTC')
        reboot_datetime_utc = reboot_datetime.astimezone(utc_timezone)

        # Convert reboot time to the user's timezone for display
        reboot_datetime_local = reboot_datetime_utc.astimezone(author_timezone)

        # Convert reboot_datetime_utc to string representation
        reboot_time_str = reboot_datetime_utc.isoformat()

        # Store the schedule in the DATA dictionary
        DATA.setdefault('reboot_schedule', {})[server_record.name] = {
            'time': reboot_time_str,
            'frequency': frequency.lower(),
            'timezone': timezone
        }
        self.schedules.set(
            server_record.id, time_value=reboot_time_str,
            frequency=frequency.lower(), timezone=timezone,
        )
        self.client.database_connection.commit()

        await ctx.send(f"The reboot schedule for {server} has been set to {reboot_datetime_local.strftime('%H:%M:%S')} ({frequency}) in the {timezone} timezone.")
