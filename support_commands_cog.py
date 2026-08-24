import asyncio
import sqlite3

import aiohttp
import discord
from discord.ext import commands
from loguru import logger

from application_service import (
    ApplicationAnswers,
    ApplicationService,
    DecisionResult,
)
from minecraft_api import validate_minecraft_username


MAX_RETRIES = 3
RATE_LIMIT_WAIT_TIME = 1
APPLICATION_TIMEOUT = 300
CANCEL_WORD = "cancel"


class MemberRoleAssignmentError(RuntimeError):
    """A configuration or Discord state prevents assigning the member role."""


class SupportCommandsCog(commands.Cog, name="SupportCommands"):
    def __init__(self, client, application_service: ApplicationService | None = None):
        self.client = client
        self.application_service = application_service

    async def validate_ign(self, ign):
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await validate_minecraft_username(
                session,
                ign,
                max_retries=MAX_RETRIES,
                retry_delay=RATE_LIMIT_WAIT_TIME,
            )

    async def _answer(self, ctx, prompt):
        await ctx.author.send(f"{prompt}\nType `{CANCEL_WORD}` to stop applying.")

        def check(message):
            return message.author == ctx.author and message.channel == ctx.author.dm_channel

        try:
            message = await self.client.wait_for(
                "message", check=check, timeout=APPLICATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            await ctx.author.send("Your application timed out. Run `!apply` to start again.")
            return None
        value = message.content.strip()
        if value.casefold() == CANCEL_WORD:
            await ctx.author.send("Application cancelled. Nothing was submitted.")
            return None
        return message

    async def _yes_no(self, ctx, prompt):
        message = await ctx.author.send(
            f"{prompt}\n"
            "✅ Yes\n"
            "🚫 No\n"
            "🔴 Cancel application"
        )
        choices = {"✅": True, "🚫": False, "🔴": None}
        for emoji in choices:
            await message.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == message.id
                and str(reaction.emoji) in choices
            )

        try:
            reaction, _ = await self.client.wait_for(
                "reaction_add", check=check, timeout=APPLICATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            await ctx.author.send("Your application timed out. Run `!apply` to start again.")
            return None
        answer = choices[str(reaction.emoji)]
        if answer is None:
            await ctx.author.send("Application cancelled. Nothing was submitted.")
        return answer

    async def _sponsor_type_reaction(self, ctx, sponsor_identifier):
        message = await ctx.author.send(
            f"How should `{sponsor_identifier}` be identified?\n"
            "💬 Discord name\n"
            "🎮 Minecraft IGN\n"
            "🔴 Cancel application"
        )
        choices = {"💬": "discord", "🎮": "minecraft", "🔴": None}
        for emoji in choices:
            await message.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == message.id
                and str(reaction.emoji) in choices
            )

        try:
            reaction, _ = await self.client.wait_for(
                "reaction_add", check=check, timeout=APPLICATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            await ctx.author.send("Your application timed out. Run `!apply` to start again.")
            return None
        sponsor_type = choices[str(reaction.emoji)]
        if sponsor_type is None:
            await ctx.author.send("Application cancelled. Nothing was submitted.")
        return sponsor_type

    async def _source_reaction(self, ctx):
        message = await ctx.author.send(
            "Where did you find us?\n"
            "🔎 Web search\n"
            "📦 Modpack search\n"
            "🧑 Sponsor / friend\n"
            "▶️ YouTube\n"
            "🔴 Cancel application"
        )
        choices = {
            "🔎": "Web search",
            "📦": "Modpack search",
            "🧑": "Sponsor / friend",
            "▶️": "YouTube",
            "🔴": None,
        }
        for emoji in choices:
            await message.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == message.id
                and str(reaction.emoji) in choices
            )

        try:
            reaction, _ = await self.client.wait_for(
                "reaction_add", check=check, timeout=APPLICATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            await ctx.author.send("Your application timed out. Run `!apply` to start again.")
            return None
        source = choices[str(reaction.emoji)]
        if source is None:
            await ctx.author.send("Application cancelled. Nothing was submitted.")
        return source

    @commands.command(
        brief="Apply for server membership",
        description=(
            "Apply for membership through a private DM interview. "
            "Usage: !apply in the support channel. You can type cancel at any question."
        ),
        rest_is_raw=True,
    )
    async def apply(self, ctx):
        """Apply for membership through a private DM interview.

        Usage: !apply in the support channel. Type cancel to stop without submitting.
        """
        if ctx.channel.id != self.client.runtime_config.require_int("supportChannelID"):
            return
        if self.application_service is None:
            await ctx.author.send("Applications are temporarily unavailable.")
            logger.error("Application service is not configured")
            return

        member_role = discord.utils.get(
            ctx.guild.roles, id=self.client.runtime_config.require_int("memberRoleID")
        )
        if member_role in ctx.author.roles:
            await ctx.author.send("You are already a member.")
            return

        while True:
            ign_message = await self._answer(
                ctx,
                "What is your IGN (Minecraft name)? It is case sensitive.",
            )
            if ign_message is None:
                return
            ign = ign_message.content.strip()
            valid_ign = await self.validate_ign(ign)
            if valid_ign is True:
                await ign_message.add_reaction("✅")
                break
            if valid_ign is None:
                await ctx.author.send("Minecraft name validation is temporarily unavailable.")
            else:
                await ctx.author.send("That is not a valid Minecraft name.")

        over_18 = await self._yes_no(ctx, "Are you 18 or older?")
        if over_18 is None:
            return

        sponsor_type = None
        sponsor_identifier = None
        if not over_18:
            has_sponsor = await self._yes_no(ctx, "Do you have a sponsor?")
            if has_sponsor is None:
                return
            if has_sponsor:
                sponsor = await self._answer(
                    ctx,
                    "What is your sponsor's Discord name or Minecraft IGN?",
                )
                if sponsor is None:
                    return
                sponsor_identifier = sponsor.content.strip()[:100]
                sponsor_type = await self._sponsor_type_reaction(
                    ctx, sponsor_identifier
                )
                if sponsor_type is None:
                    return

        source = await self._source_reaction(ctx)
        if source is None:
            return

        admin_channel = discord.utils.get(
            ctx.guild.text_channels,
            id=self.client.runtime_config.require_int("adminChannelID"),
        )
        if admin_channel is None:
            await ctx.author.send("The admin review channel is unavailable. Please contact an admin.")
            return

        try:
            application = self.application_service.submit(
                ApplicationAnswers(
                    discord_user_id=ctx.author.id,
                    minecraft_name=ign,
                    over_18=over_18,
                    sponsor_type=sponsor_type,
                    sponsor_identifier=sponsor_identifier,
                    source=source,
                )
            )
        except sqlite3.IntegrityError:
            await ctx.author.send("You already have an application awaiting review.")
            return
        except Exception:
            logger.exception("Unable to save membership application")
            await ctx.author.send("Your application could not be saved. Please contact an admin.")
            return

        reviewer_role = discord.utils.get(
            ctx.guild.roles,
            id=int(self.client.runtime_config.get("applicationReviewerRoleID", 0)),
        )
        try:
            approval_message = await admin_channel.send(
                content=reviewer_role.mention if reviewer_role else None,
                embed=self._application_embed(application, "Pending review"),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[reviewer_role] if reviewer_role else False,
                ),
            )
        except discord.HTTPException:
            self.application_service.fail_submission(
                application.id, "Could not post application for admin review"
            )
            logger.exception("Unable to post membership application")
            await ctx.author.send("Your application could not be posted. Please try again.")
            return
        self.application_service.attach_admin_message(
            application.id, admin_channel.id, approval_message.id
        )
        await approval_message.add_reaction("👍")
        await approval_message.add_reaction("👎")
        await ctx.author.send("Your application was submitted for admin review.")

    @staticmethod
    def _reviewer_allowed(user):
        permissions = user.guild_permissions
        return permissions.administrator or permissions.manage_roles

    @staticmethod
    def _application_embed(application, heading, detail=None):
        colors = {
            "Pending review": 0xF1C40F,
            "Approved": 0x2ECC71,
            "Denied": 0xE74C3C,
            "Needs retry": 0xE67E22,
        }
        embed = discord.Embed(title=f"Membership application #{application.id}", color=colors[heading])
        embed.add_field(name="Status", value=heading, inline=False)
        embed.add_field(name="Minecraft IGN", value=application.minecraft_name)
        embed.add_field(name="18 or older", value="Yes" if application.over_18 else "No")
        sponsor = "None"
        if application.sponsor_identifier:
            sponsor = f"{application.sponsor_identifier} ({application.sponsor_type})"
        embed.add_field(name="Sponsor", value=sponsor, inline=False)
        embed.add_field(name="Found us through", value=application.source, inline=False)
        embed.add_field(name="Discord user", value=f"<@{application.discord_user_id}>", inline=False)
        if detail:
            embed.add_field(name="Review details", value=detail[:1024], inline=False)
        return embed

    @staticmethod
    def _server_summary(result: DecisionResult):
        if not result.servers:
            return result.message or "No server results."
        return "\n".join(
            f"{name}: {outcome.message}" for name, outcome in result.servers.items()
        )

    @staticmethod
    async def _remove_voting_reactions(message):
        for emoji in ("👍", "👎"):
            try:
                await message.clear_reaction(emoji)
            except discord.HTTPException:
                logger.exception("Unable to remove application voting reaction")

    async def _assign_member_role(self, guild, application):
        role_id = self.client.runtime_config.require_int("memberRoleID")
        member_role = guild.get_role(role_id)
        if member_role is None:
            raise MemberRoleAssignmentError(
                f"Configured member role `{role_id}` does not exist in this Discord server."
            )
        if member_role.managed:
            raise MemberRoleAssignmentError(
                "The configured member role is managed by an integration and cannot be assigned."
            )

        bot_member = guild.me
        if bot_member is None:
            bot_member = await guild.fetch_member(self.client.user.id)
        permissions = bot_member.guild_permissions
        if not (permissions.administrator or permissions.manage_roles):
            raise MemberRoleAssignmentError(
                "EggBot needs the Manage Roles permission to assign the member role."
            )
        if member_role.position >= bot_member.top_role.position:
            raise MemberRoleAssignmentError(
                "EggBot's highest role must be above the configured member role."
            )

        try:
            applicant = await guild.fetch_member(application.discord_user_id)
        except discord.NotFound as exc:
            raise MemberRoleAssignmentError(
                "The applicant is no longer a member of this Discord server."
            ) from exc

        if member_role.id not in {role.id for role in applicant.roles}:
            await applicant.add_roles(
                member_role, reason=f"Application #{application.id} approved"
            )
            applicant = await guild.fetch_member(application.discord_user_id)
        if member_role.id not in {role.id for role in applicant.roles}:
            raise MemberRoleAssignmentError(
                "Discord accepted the request but did not assign the member role."
            )
        return applicant

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        if await self._handle_followup_reaction(reaction, user):
            return
        if reaction.message.channel.id != self.client.runtime_config.require_int("adminChannelID"):
            return
        emoji = str(reaction.emoji)
        if emoji not in ("👍", "👎") or not self._reviewer_allowed(user):
            return
        if self.application_service is None:
            return

        application = self.application_service.get_by_admin_message(reaction.message.id)
        if application is None:
            return
        try:
            applicant = await reaction.message.guild.fetch_member(application.discord_user_id)
        except discord.NotFound:
            applicant = None

        if emoji == "👎":
            result = self.application_service.deny(reaction.message.id, user.id)
            if result.status != "denied":
                return
            audit = await self.application_service.audit_denied_whitelist(
                result.application
            )
            present = [
                name for name, outcome in audit.items() if outcome.status == "present"
            ]
            details = [
                f"**Reviewed by:** {user.mention} — **Denied**",
                "**Whitelist results:**",
            ]
            details.extend(
                f"{name}: {outcome.message}" for name, outcome in audit.items()
            )
            await reaction.message.edit(
                embed=self._application_embed(
                    result.application, "Denied", "\n".join(details)
                )
            )
            await self._remove_voting_reactions(reaction.message)
            if present:
                reviewer_role = discord.utils.get(
                    reaction.message.guild.roles,
                    id=int(self.client.runtime_config.get("applicationReviewerRoleID", 0)),
                )
                await reaction.message.channel.send(
                    content=reviewer_role.mention if reviewer_role else None,
                    embed=discord.Embed(
                        title="Denied applicant is still whitelisted",
                        description=(
                            f"**Minecraft IGN:** {result.application.minecraft_name}\n"
                            f"**Servers:** {', '.join(present)}\n"
                            f"**Application:** #{result.application.id}"
                        ),
                        color=0xE74C3C,
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=[reviewer_role] if reviewer_role else False,
                    ),
                )
            if applicant:
                followup_message = await applicant.send(
                    f"Your application was denied by **{user.display_name}**. "
                    "Would you like an admin to contact you?\n"
                    "📩 Request an admin follow-up\n"
                    "🔴 No follow-up",
                )
                await followup_message.add_reaction("📩")
                await followup_message.add_reaction("🔴")
                self.application_service.offer_followup(
                    result.application.id, followup_message.id
                )
            return

        result = await self.application_service.approve(reaction.message.id, user.id)
        if result.status == "approved":
            try:
                applicant = await self._assign_member_role(
                    reaction.message.guild, result.application
                )
            except (discord.HTTPException, MemberRoleAssignmentError) as exc:
                logger.exception(
                    "Whitelist completed but member role assignment failed: {}", exc
                )
                application = self.application_service.mark_role_failure(
                    result.application.id, str(exc)
                )
                await reaction.message.edit(
                    embed=self._application_embed(
                        application,
                        "Needs retry",
                        f"**Role assignment:** {exc}\n"
                        f"**Whitelist results:**\n{self._server_summary(result)}",
                    )
                )
                return
            try:
                self.application_service.record_player_link(
                    result.application.id,
                    applicant.name,
                    applicant.display_name,
                )
            except Exception:
                logger.exception("Member role assigned but player association failed")
                application = self.application_service.mark_role_failure(
                    result.application.id, "Discord-to-Minecraft association failed"
                )
                await reaction.message.edit(
                    embed=self._application_embed(
                        application, "Needs retry", self._server_summary(result)
                    )
                )
                return
            await reaction.message.edit(
                embed=self._application_embed(
                    result.application,
                    "Approved",
                    f"**Reviewed by:** {user.mention} — **Approved**\n"
                    f"**Whitelist results:**\n{self._server_summary(result)}",
                )
            )
            await self._remove_voting_reactions(reaction.message)
            await applicant.send(f"Your application was approved by {user.name}.")
        elif result.status == "partial_failure":
            await reaction.message.edit(
                embed=self._application_embed(
                    result.application, "Needs retry", self._server_summary(result)
                )
            )
        elif result.status == "failed":
            logger.error("Unexpected application processing failure")

    async def _handle_followup_reaction(self, reaction, user):
        if self.application_service is None or str(reaction.emoji) not in ("📩", "🔴"):
            return False
        followup = self.application_service.get_followup_by_message(reaction.message.id)
        if followup is None:
            return False
        application = self.application_service.get_application(
            followup.application_id
        )
        if application is None or application.discord_user_id != user.id:
            return True

        requested = str(reaction.emoji) == "📩"
        updated = self.application_service.respond_to_followup(
            reaction.message.id, requested
        )
        if updated is None:
            return True
        await self._remove_own_prompt_reactions(reaction.message, ("📩", "🔴"))
        if not requested:
            await reaction.message.edit(
                content="No admin follow-up requested."
            )
            return True

        await self._notify_followup_admin(application)
        self.application_service.mark_followup_notified(application.id)
        await reaction.message.edit(
            content="Your follow-up request was sent to the admins. An admin can contact you directly."
        )
        return True

    async def _remove_own_prompt_reactions(self, message, emojis):
        for emoji in emojis:
            try:
                await message.remove_reaction(emoji, self.client.user)
            except discord.HTTPException:
                logger.exception("Unable to remove EggBot's DM prompt reaction")

    async def _notify_followup_admin(self, application):
        admin_channel_id = self.client.runtime_config.require_int("adminChannelID")
        admin_channel = self.client.get_channel(admin_channel_id)
        if admin_channel is None:
            admin_channel = await self.client.fetch_channel(admin_channel_id)
        reviewer_role = discord.utils.get(
            admin_channel.guild.roles,
            id=int(self.client.runtime_config.get("applicationReviewerRoleID", 0)),
        )
        reviewer = None
        if application.reviewer_discord_user_id:
            reviewer = admin_channel.guild.get_member(
                application.reviewer_discord_user_id
            )
        mentions = []
        if reviewer_role:
            mentions.append(reviewer_role.mention)
        if reviewer:
            mentions.append(reviewer.mention)
        await admin_channel.send(
            content=" ".join(mentions) or None,
            embed=discord.Embed(
                title="Denied applicant requests a follow-up",
                description=(
                    f"**Applicant:** <@{application.discord_user_id}> "
                    f"(`{application.discord_user_id}`)\n"
                    f"**Minecraft IGN:** {application.minecraft_name}\n"
                    f"**Application:** #{application.id}\n"
                    f"**Denied by:** "
                    f"{reviewer.mention if reviewer else application.reviewer_discord_user_id}"
                ),
                color=0xF1C40F,
            ),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=[reviewer] if reviewer else False,
                roles=[reviewer_role] if reviewer_role else False,
            ),
        )

    async def reconcile_followup_notifications(self):
        """Deliver requested follow-ups that failed before the admin alert was sent."""
        if self.application_service is None:
            return
        for followup in self.application_service.list_undelivered_followups():
            application = self.application_service.get_application(
                followup.application_id
            )
            if application is None:
                continue
            try:
                await self._notify_followup_admin(application)
                self.application_service.mark_followup_notified(application.id)
                applicant = await self.client.fetch_user(application.discord_user_id)
                dm_channel = applicant.dm_channel or await applicant.create_dm()
                prompt = await dm_channel.fetch_message(followup.prompt_message_id)
                await self._remove_own_prompt_reactions(prompt, ("📩", "🔴"))
                await prompt.edit(
                    content="Your follow-up request was sent to the admins. An admin can contact you directly."
                )
            except discord.HTTPException:
                logger.exception(
                    "Unable to deliver follow-up notification for application {}",
                    application.id,
                )

    async def reconcile_pending_reviews(self):
        """Process one clear admin decision that arrived while EggBot was offline."""
        if self.application_service is None:
            return
        for application in self.application_service.list_pending():
            try:
                channel = self.client.get_channel(application.admin_channel_id)
                if channel is None:
                    channel = await self.client.fetch_channel(
                        application.admin_channel_id
                    )
                message = await channel.fetch_message(application.admin_message_id)
                decisions = []
                for reaction in message.reactions:
                    if str(reaction.emoji) not in ("👍", "👎"):
                        continue
                    async for reaction_user in reaction.users():
                        if reaction_user.bot:
                            continue
                        member = message.guild.get_member(reaction_user.id)
                        if member is None:
                            member = await message.guild.fetch_member(reaction_user.id)
                        if self._reviewer_allowed(member):
                            decisions.append((reaction, member))
                if len(decisions) == 1:
                    reaction, reviewer = decisions[0]
                    logger.info(
                        "Recovering offline application review for application {}",
                        application.id,
                    )
                    await self.on_reaction_add(reaction, reviewer)
                elif len(decisions) > 1:
                    logger.warning(
                        "Application {} has conflicting offline review reactions",
                        application.id,
                    )
            except (discord.HTTPException, discord.NotFound):
                logger.exception(
                    "Unable to reconcile pending application {}", application.id
                )
