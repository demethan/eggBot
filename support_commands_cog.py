import discord
import random
import re
import aiohttp
import asyncio
import requests
import time
from discord.ext import commands
from config import DATA, save_data
from loguru import logger
from discord.utils import get

color=0x00ff00

MAX_RETRIES = 3
RATE_LIMIT_WAIT_TIME = 1  # Time to wait in seconds when rate limit is hit

class SupportCommandsCog(commands.Cog, name='SupportCommands'):
    def __init__(self, client):
        self.client = client

    async def validate_ign(self, ign):
        url = f'https://api.mojang.com/users/profiles/minecraft/{ign}'

        for retry in range(MAX_RETRIES):
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    return True
                elif response.status_code == 204:
                    # IGN not found
                    return False
                elif response.status_code == 429:
                    # Rate limit hit
                    await asyncio.sleep(RATE_LIMIT_WAIT_TIME)
                    continue
                else:
                    return False
            except requests.RequestException:
                # Exception occurred during request, retry after a delay
                await asyncio.sleep(RATE_LIMIT_WAIT_TIME)
                continue

        return False


    @commands.command(description='Apply to be a member.', rest_is_raw=True)
    async def apply(self, ctx):
        if ctx.channel.id != DATA["supportChannelID"]:
            return

        def check(message):
            return message.author == ctx.author and message.channel == ctx.author.dm_channel

        # Check if the user is already a member
        member_role = discord.utils.get(ctx.guild.roles, id=int(DATA["memberRoleID"]))
        if member_role in ctx.author.roles:
            await ctx.author.send("You are already a member.")
            return

        while True:
            await ctx.author.send('What is your IGN (Minecraft name)? Please remember that it is case sensitive.')
            ign_message = await self.client.wait_for('message', check=check)
            ign = ign_message.content

            # Validate IGN using Minecraft API
            valid_ign = await self.validate_ign(ign)
            if valid_ign:
                break
            else:
                await ctx.author.send('Invalid IGN. Please enter a valid Minecraft name.')

        # React with a checkmark to the IGN entry message
        await ign_message.add_reaction('✅')

        while True:
            await ctx.author.send('Are you older than 18? (yes/no)')
            age_check_message = await self.client.wait_for('message', check=check)
            age_check = age_check_message.content.lower()

            if age_check in ['yes', 'no']:
                break
            else:
                await ctx.author.send('Invalid response. Please answer with either "yes" or "no".')


        if age_check == 'no':
            while True:
                await ctx.author.send('Do you have a sponsor? (yes/no)')
                sponsor_check_message = await self.client.wait_for('message', check=check)
                sponsor_check = sponsor_check_message.content.lower()

                if sponsor_check in ['yes', 'no']:
                    break
                else:
                    await ctx.author.send('Invalid response. Please answer with either "yes" or "no".')

            if sponsor_check == 'yes':
                await ctx.author.send('Who is your sponsor?')
                sponsor_message = await self.client.wait_for('message', check=check)
                sponsor = sponsor_message.content[:20]
            else:
                sponsor = None
        else:
            sponsor = None

        await ctx.author.send('Where did you find us?')
        source_message = await self.client.wait_for('message', check=check)
        source = source_message.content[:100]

        if len(source) == 0:
            await ctx.author.send('Invalid source. Please provide a source.')

        admin_channel = discord.utils.get(ctx.guild.text_channels, id=int(DATA["adminChannelID"]))
        if not admin_channel:
            await ctx.send('Admin channel not found.')
            return

        approval_message = await admin_channel.send(
            f'New application:\nIGN: {ign}\nOver 18: {age_check}\nSource: {source}\nSponsor: {sponsor}\nReact with 👍 to approve, 👎 to deny.')
        await approval_message.add_reaction('👍')
        await approval_message.add_reaction('👎')

        # Store the user ID, IGN with the message ID for later retrieval
        self.client.applications = getattr(self.client, 'applications', {})
        self.client.applications[approval_message.id] = (ctx.author.id, ign, sponsor)


    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if reaction.message.channel.id != int(DATA["adminChannelID"]):
            return
        
        # Fetch the user who made the application and their IGN
        application = self.client.applications.get(reaction.message.id)
        if not application:
            logger.debug("No applicant data to fetch")
            return
        # Unpack the application tuple based on its length
        if len(application) == 2:
            applicant_id, ign = application
            sponsor = None
        elif len(application) == 3:
            applicant_id, ign, sponsor = application
        else:
            logger.debug("Invalid application data")
            return
        applicant = await reaction.message.guild.fetch_member(applicant_id)
        if not applicant:
            logger.debug("No applicant found with {}".format(applicant_id))
            return

        # Fetch the 'member' role by ID
        member_role = discord.utils.get(reaction.message.guild.roles, id=int(DATA["memberRoleID"]))
        if not member_role:
            logger.debug("No member role found.")
            return

        try:
            if str(reaction.emoji) == '👍':
                await applicant.add_roles(member_role)
                await reaction.message.channel.send(f'Application approved by {user.name}. {applicant.name} has been added to the member role.')
                await reaction.message.channel.send(f'{reaction.message.guild.get_role(DATA["fryBotRoleID"]).mention} !send whitelist add {ign}')
                await reaction.message.channel.send(f'{reaction.message.guild.get_role(DATA["fryBotRoleID"]).mention} !send whitelist reload')
                await applicant.send(f'Your application has been approved by {user.name}. You have been added to the member role.')
            elif str(reaction.emoji) == '👎':
                await reaction.message.channel.send(f'{applicant.name}`s application was denied by {user.name}.')
                await applicant.send(f'Your application has been denied by {user.name}.')
            
            # Remove the original application message
            await reaction.message.delete()
        except Exception as e:
            logger.error(f"Error while processing reaction: {str(e)}")
            logger.error(f"Error details: {type(e).__name__}, {e.args}")


