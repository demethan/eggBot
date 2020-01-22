import discord
import asyncio
from discord.ext import commands
from loguru import logger


#get admins
async def admin_only(ctx: commands.Context):
    logger.debug(ctx.channel.id == DATA["adminChannelID"])
    return ctx.channel.id == DATA["adminChannelID"]