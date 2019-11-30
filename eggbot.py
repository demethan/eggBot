import discord
from discord.ext import commands
import random

#variables
applyUrl = "https://breakfastcraft.com/page/apply/"
connectUrl = "https://www.breakfastcraft.com/page/connection-information/"
adminChannelID = 'kitchen'
supportChannelID = 'support'

#store discord token in the config.cfg file
try:
    with open ("config.cfg","r") as file:
        token = file.read()
except Exception:
    print("config.cfg file missing")

class eggBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_command(self.status)
        self.add_command(self.howto)
        self.add_command(self.roll)
        self.add_command(self.kick)
        self.add_command(self.ban)
        self.add_command(self.packName)
        self.add_command(self.packVersion)
        self.add_command(self.schedule)
        
    # support channel welcome concierge
    async def on_member_join(self, member):
        guild = member.guild
        if guild.system_channel is None and member.channel == self.supportChannelID:
            to_send = 'Welcome {0.mention} to BreakfastCraft! \r\n'.format(member)
            to_send = to_send + 'If you haven''t done so already, visite '+ self.applyUrl +'\r\n'
            await guild.system_channel.send(to_send)
            channel = self.get_channel(self.adminChannelID)
            await channel.send('possible new member in support, please check web application.')

    #status command
    @commands.command(description='Get info about one or all the Minecraft servers')
    async def status(ctx, arg=None):
        """Get info about one or all the Minecraft servers"""
        channel = ctx.get_channel
        if channel.id not supportChannelID or channel.id not adminChannelID:
            ctx.send("Please use in the support channel. Thx!")
            return

        if arg is not None:
            #get arg(server) info details a send that
            await ctx.send(arg+' info: blah blah blah')
        else:
            #send complete server list info
            await ctx.send('list of sever info')

    #howto command
    @commands.command(description='Get URL to connection intructions')
    async def howto(ctx):
        """Get URL to connection intructions"""
        author = ctx.message.author
        channel = await author.create_dm()
        await channel.send(connectUrl)
        

    #roll command
    @commands.command(description='Roll a dice of your choice.')
    async def roll(ctx, dice: str):
        """Rolls a dice in NdN format."""
        try:
            rolls, limit = map(int, dice.split('d'))
        except Exception:
            await ctx.send('Format has to be in NdN!')
            return

        result = ', '.join(str(random.randint(1, limit)) for r in range(rolls))
        await ctx.send(result)

    #kick command
    @commands.command(description='kick a user from the minecraft servers')
    async def kick(ctx, ign: str):
        """Admin: kick a user from the minecraft servers."""
        await ctx.send('One sec, got to put my boots on!')

    #ban command
    @commands.command(description='ban a user from the minecraft servers')
    async def ban(ctx, ign: str):
        """Admin: ban a user from the minecraft servers."""
        await ctx.send('Deploying ban hammer!')

    #packName
    @commands.command(description='change the pack name of a server')
    async def packName(ctx, server: str, name: str):
        """Admin: change the pack name of a server."""

    #packVersion
    @commands.command(description='change the pack version of a server')
    async def packVersion(ctx, server: str, name: str):
        """Admin: change the pack version of a server."""
    
    #schedule
    @commands.command(description='list the server reboot schedules')
    async def schedule(ctx):
        """list the server reboot schedules"""


    #startup connection to discord
    async def on_ready(self):
        print('Logged on as {0}!'.format(self.user))
        print(self.user.name)
        print(self.user.id)
        print('------')




bot = eggBot("~")
bot.run(token)


