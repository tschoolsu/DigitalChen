import discord
from discord import app_commands
from discord.ext import commands

from utils import save_member


class RegisterModal(discord.ui.Modal, title="部員資料建檔"):
    name = discord.ui.TextInput(
        label="姓名",
        placeholder="請輸入姓名",
        max_length=30
    )

    student_id = discord.ui.TextInput(
        label="學號",
        placeholder="請輸入學號",
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user

        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "此功能只能在伺服器內使用。",
                ephemeral=True
            )
            return

        role_names = [
            role.name
            for role in member.roles
            if role.name != "@everyone"
        ]

        current_roles = "、".join(role_names) if role_names else "無身分組"
        new_nickname = f"{current_roles} | {self.name.value}"
        nickname_warning = ""

        try:
            await member.edit(nick=new_nickname)
        except discord.Forbidden:
            nickname_warning = "\n_注意：資料已記錄，但我沒有權限修改你的暱稱。請確認 Bot 有「管理暱稱」權限，且 Bot 身分組高於你的身分組。_"

        student_email = self.student_id.value.strip() + "@tschool.tp.edu.tw"
        data = {
            "name": self.name.value,
            "student_email": student_email,
            "discord_id": str(member.id),
            "discord_username": str(member.name),
            "joined_at": member.joined_at.isoformat() if member.joined_at else discord.utils.utcnow().isoformat()
        }

        save_member(data)

        await interaction.response.send_message("建檔完成。" + nickname_warning, ephemeral=True)


class RegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="我已閱讀內文",
        style=discord.ButtonStyle.primary,
        custom_id="register_button"
    )
    async def register_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(RegisterModal())


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RegisterView())

    @app_commands.command(name="setup", description="新成員必做的事")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="新成員必做的事",
            description=(
                "1. 請新加入 Discord 的各部員按下下方按鈕進行建檔案\n"
                "2. 建檔完成後，請至 https://discord.com/channels/1515036337894588448/1515059558693732633 觀看非同步教學影片，並閱讀說明文檔\n"
            ),
            color=discord.Color.blue()
        )

        await interaction.channel.send(embed=embed, view=RegisterView())
        await interaction.response.send_message("已送出 setup 訊息。", ephemeral=True)

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "只有管理員可以使用 `/setup`。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "執行時發生錯誤。",
                ephemeral=True
            )
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))
