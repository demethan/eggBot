import discord
import random
import json
import re
from commands_cog import CommandsCog
from discord.ext import commands
from config import CONFIG, DATA



class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_cog(CommandsCog(self))
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None:
            channel = await member.create_dm()
            await channel.send(DATA["joinMessage"].format(member=member.mention,applyUrl=DATA["applyUrl"]))


    #startup connection to discord
    async def on_ready(self):
        print('Logged on as {0}!'.format(self.user))
        print(self.user.name)
        print(self.user.id)
        print('------')




bot = eggBot("~")
bot.run(CONFIG["token"])


