import discord
from discord import app_commands
from discord.ext import commands

from utils import authorized_only


class MessageModal(discord.ui.Modal, title="發送訊息"):
    content = discord.ui.TextInput(
        label="訊息內容",
        style=discord.TextStyle.paragraph,
        placeholder="在此輸入要發送的內容，支援 Markdown 與換行",
        max_length=2000
    )

    def __init__(self, target: discord.abc.Messageable, success_text: str):
        super().__init__()
        self.target = target
        self.success_text = success_text

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.target.send(self.content.value)
        except discord.Forbidden:
            await interaction.response.send_message(
                "無法傳送訊息，請確認對象可接收訊息。",
                ephemeral=True
            )
            return

        await interaction.response.send_message(self.success_text, ephemeral=True)


class MessagingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="say", description="在此頻道發送訊息")
    @authorized_only()
    async def say(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            MessageModal(
                target=interaction.channel,
                success_text="已於此頻道發送訊息。"
            )
        )

    @say.error
    async def say_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "只有授權使用者可以使用 `/say`。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "執行時發生錯誤。",
                ephemeral=True
            )
            raise error

    @app_commands.command(name="dm", description="對指定使用者發送私人訊息")
    @authorized_only()
    @app_commands.describe(user="要發送私訊的使用者")
    async def dm(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.send_modal(
            MessageModal(
                target=user,
                success_text=f"已將訊息送出給 {user.mention}。"
            )
        )

    @dm.error
    async def dm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "只有授權使用者可以使用 `/dm`。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "執行時發生錯誤。",
                ephemeral=True
            )
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(MessagingCog(bot))
