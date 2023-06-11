import discord
import random
import re
import aiohttp
import asyncio
from discord.ext import commands
from config import DATA, save_data
from loguru import logger
from discord.utils import get
color=0x00ff00


class SupportCommandsCog(commands.Cog,name='SupportCommands'):
    def __init__(self, client):
        self.client = client

    async def apply(ctx):
        if ctx.channel.name != 'support':
            return

        def check(message):
            return message.author == ctx.author and message.channel == ctx.author.dm_channel

        await ctx.author.send('What is your IGN (Minecraft name)? Please remember that it is case sensitive.')
        ign = await self.client.wait_for('message', check=check)
        
        await ctx.author.send('Are you older than 18? (yes/no)')
        age_check = await self.client.wait_for('message', check=check)
        
        await ctx.author.send('Where did you find us?')
        source = await self.client.wait_for('message', check=check)

        admin_channel = discord.utils.get(ctx.guild.text_channels, name='admin')
        if not admin_channel:
            await ctx.send('Admin channel not found.')
            return

        approval_message = await admin_channel.send(
            f'New application:\nIGN: {ign.content}\nOver 18: {age_check.content}\nSource: {source.content}\nReact with 👍 to approve, 👎 to deny.')
        await approval_message.add_reaction('👍')
        await approval_message.add_reaction('👎')

        # Store the user ID and IGN with the message ID for later retrieval
        self.client.applications = getattr(self.client, 'applications', {})
        self.client.applications[approval_message.id] = (ctx.author.id, ign.content)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.self.client or reaction.message.channel.id != DATA["adminChannelID"]:
            return

        # Fetch the user who made the application and their IGN
        application = self.self.client.applications.get(reaction.message.id)
        if not application:
            return
        applicant_id, ign = application
        applicant = self.self.client.get_user(applicant_id)
        if not applicant:
            return

        # Fetch the 'member' role by ID
        member_role = discord.utils.get(reaction.message.guild.roles.id, id=DATA["memberRoleID"])
        if not member_role:
            return

        if str(reaction.emoji) == '👍':
            await applicant.add_roles(member_role)
            await reaction.message.channel.send(f'Application approved by {user.name}. {applicant.name} has been added to the member role.')
            await reaction.message.channel.send(f'@fryserver !send whitelist add {ign}')
        elif str(reaction.emoji) == '👎':
            await reaction.message.channel.send(f'Application denied by {user.name}.')

    @commands.command()
    async def apply(self, ctx):
        if ctx.channel.id != 'support':
            return

        def check(message):
            return message.author == ctx.author and message.channel == ctx.author.dm_channel

        await ctx.author.send('What is your IGN (Minecraft name)? Please remember that it is case sensitive.')
        ign = await self.self.client.wait_for('message', check=check)
    
        await ctx.author.send('Are you older than 18? (yes/no)')
        age_check = await self.self.client.wait_for('message', check=check)
    
        await ctx.author.send('Where did you find us?')
        source = await self.self.client.wait_for('message', check=check)

        admin_channel = self.self.client.get_channel(DATA["adminChannelID"])
        if not admin_channel:
            await ctx.send('Admin channel not found.')
            return

        approval_message = await admin_channel.send(
            f'New application:\nIGN: {ign.content}\nOver 18: {age_check.content}\nSource: {source.content}\nReact with 👍 to approve, 👎 to deny.')
        await approval_message.add_reaction('👍')
        await approval_message.add_reaction('👎')

        # Store the user ID and IGN with the message ID for later retrieval
        self.self.client.applications[approval_message.id] = (ctx.author.id, ign.content)


