import discord
from discord import app_commands
from discord.ext import commands

from utils import authorized_only


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="joinvoice", description="加入語音頻道")
    @authorized_only()
    @app_commands.describe(channel="要加入的語音頻道")
    async def joinvoice(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        await interaction.response.defer(ephemeral=True)

        try:
            await channel.connect()
        except discord.Forbidden:
            message = "沒有權限加入該語音頻道。"
        except RuntimeError as e:
            error_text = str(e).lower()
            if "davey" in error_text:
                message = "無法使用語音功能：請安裝 davey 依賴（例如 pip install davey）。"
            elif "pynacl" in error_text:
                message = "無法使用語音功能：請安裝 PyNaCl 依賴（例如 pip install PyNaCl）。"
            else:
                raise
        except discord.ClientException:
            message = "加入語音頻道時發生錯誤。"
        else:
            await interaction.followup.send(
                f"已加入 {channel.mention}。",
                ephemeral=True
            )
            return

        await interaction.followup.send(message, ephemeral=True)

    @joinvoice.error
    async def joinvoice_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            message = "只有授權使用者可以使用 `/joinvoice`。"
        else:
            message = "執行時發生錯誤。"

        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot))
