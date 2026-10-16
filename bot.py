import discord
from discord.ext import commands

from utils import GUILD_ID, TOKEN

EXTENSIONS = (
    "cogs.registration",
    "cogs.messaging",
    "cogs.voice",
    "cogs.meeting",
)


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)


bot = MyBot()

if __name__ == "__main__":
    bot.run(TOKEN)
