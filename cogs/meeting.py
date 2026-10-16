import asyncio
import email.message
import os
import smtplib
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    get_member_choices,
    get_emails_from_values,
    find_member_discord_mention_by_email,
)


MEETINGS_API_URL = os.getenv("MEETINGS_API_URL", "").strip()
MEETINGS_API_KEY = os.getenv("MEETINGS_API_KEY", "").strip()


DEPARTMENT_MEET_LINKS = {
    "無分類": "https://meet.google.com/xpw-jxic-tva",
    "會本部": "https://meet.google.com/rvc-aeot-hqw",
    "學權部": "https://meet.google.com/bry-hqwp-qit",
    "學術部": "https://meet.google.com/iri-nnhp-tyr",
    "數位部": "https://meet.google.com/yhu-gbht-fgd",
    "設計部": "https://meet.google.com/vnc-stdj-qgk",
}

class MeetingSelectionView(discord.ui.View):
    def __init__(self, name: str, dept: str, dept_link: str, guild: discord.Guild = None):
        super().__init__(timeout=600)
        self.name = name
        self.dept = dept
        self.dept_link = dept_link
        self.guild = guild
        # selected_location holds a value like 'channel:<id>' or None
        self.selected_location = None
        self.selected_location_label = None

        self.mode = "attendees"  # or 'chairs'
        self.attendees_vals = []
        self.chairs_vals = []
        self.page = 0

        self._members = get_member_choices()
        self.per_page = 25
        # accumulate selections across pages for attendees and chairs
        self._accumulator = {"attendees": set(), "chairs": set()}

        # single select used for both attendees and chairs (paged)
        opts = self._get_page_options()
        self.paged_select = discord.ui.Select(placeholder="選擇出席人員 (頁 1)", min_values=0, max_values=min(len(opts), 25), options=opts)
        async def paged_select_cb(interaction: discord.Interaction):
            # acknowledge the select interaction so Discord doesn't show "此交互失敗"
            await interaction.response.defer()
        self.paged_select.callback = paged_select_cb
        self.add_item(self.paged_select)

        # Location selection: prefer choosing a Discord voice/stage channel first
        loc_options = []
        if self.guild:
            for ch in self.guild.channels:
                try:
                    if ch.type == discord.ChannelType.voice or ch.type == discord.ChannelType.stage_voice:
                        emoji = "🔊" if ch.type == discord.ChannelType.voice else "🎤"
                        loc_options.append(discord.SelectOption(label=f"{emoji} {ch.name}", value=f"channel:{ch.id}"))
                except Exception:
                    continue
        if loc_options:
            loc_options.insert(0, discord.SelectOption(label="不選（使用部會 meet 或稍後手動輸入）", value="none"))
            self.location_select = discord.ui.Select(placeholder="選擇 Discord 語音/舞台頻道（可選）", min_values=0, max_values=1, options=loc_options)
            async def loc_cb(interaction: discord.Interaction):
                vals = getattr(self.location_select, "values", []) or []
                if vals:
                    val = vals[0]
                    if val == "none":
                        self.selected_location = None
                        self.selected_location_label = None
                    elif val.startswith("channel:"):
                        self.selected_location = val
                        self.selected_location_label = next((opt.label for opt in self.location_select.options if opt.value == val), None)
                await interaction.response.defer()
            self.location_select.callback = loc_cb
            self.add_item(self.location_select)

        prev_btn = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.secondary)
        next_btn = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.secondary)
        done_btn = discord.ui.Button(label="下一步 (選好後點此)", style=discord.ButtonStyle.primary)

        async def prev_cb(interaction: discord.Interaction):
            # capture current selections before changing page
            await self._capture_current_selection()
            if self.page > 0:
                self.page -= 1
                await self._refresh_select(interaction)
            else:
                await interaction.response.defer()

        async def next_cb(interaction: discord.Interaction):
            # capture current selections before changing page
            await self._capture_current_selection()
            if (self.page + 1) * self.per_page < len(self._members):
                self.page += 1
                await self._refresh_select(interaction)
            else:
                await interaction.response.defer()

        async def done_cb(interaction: discord.Interaction):
            # store selected
            # capture current page selection first
            await self._capture_current_selection()
            members_map = {c['label']: c['value'] for c in self._members}
            if self.mode == "attendees":
                mapped = list(self._accumulator.get("attendees", set()))
                if not mapped:
                    await interaction.response.send_message("請至少選擇一位出席者。", ephemeral=True)
                    return
                self.attendees_vals = mapped
                # switch to chairs
                self.mode = "chairs"
                self.page = 0
                # update select placeholder and clear selection
                self.paged_select.placeholder = "選擇主席 (頁 1)"
                # reset select to page 0 for chairs
                self.paged_select.options = self._get_page_options()
                # clear any default selections on the Select options
                for o in self.paged_select.options:
                    try:
                        o.default = False
                    except Exception:
                        pass
                # disable location select once moving to chairs so it isn't chosen again
                if hasattr(self, 'location_select') and self.location_select:
                    try:
                        self.location_select.disabled = True
                    except Exception:
                        pass
                await interaction.response.edit_message(content=f"請選擇主席（目前已選 {len(self.attendees_vals)} 位出席者）", view=self)
                return
            else:
                mapped = list(self._accumulator.get("chairs", set()))
                if not mapped:
                    await interaction.response.send_message("請至少選擇一位主席。", ephemeral=True)
                    return
                self.chairs_vals = mapped
                # open details modal
                # pass selected location info to modal so it can prefer channel over manual input
                await interaction.response.send_modal(MeetingDetailsModal(self.name, self.attendees_vals, self.chairs_vals, self.dept, self.dept_link, selected_location=self.selected_location, selected_location_label=self.selected_location_label))

        prev_btn.callback = prev_cb
        next_btn.callback = next_cb
        done_btn.callback = done_cb

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(done_btn)

    def _get_page_options(self):
        start = self.page * self.per_page
        end = start + self.per_page
        slice = self._members[start:end]
        # use label as the Select value so we can map back to real value later
        return [discord.SelectOption(label=c['label'], value=c['label']) for c in slice]

    async def _refresh_select(self, interaction: discord.Interaction):
        opts = self._get_page_options()
        self.paged_select.options = opts
        page_no = self.page + 1
        self.paged_select.max_values = min(len(opts), 25)
        # update placeholder to show current page
        self.paged_select.placeholder = f"選擇{'主席' if self.mode=='chairs' else '出席人員'} (頁 {page_no})"
        await interaction.response.edit_message(view=self)

    async def _capture_current_selection(self):
        # read selected labels from current select and map to actual values, storing in accumulator
        vals = getattr(self.paged_select, "values", []) or []
        if not vals:
            return
        members_map = {c['label']: c['value'] for c in self._members}
        mapped = [members_map.get(v, v) for v in vals]
        key = "attendees" if self.mode == "attendees" else "chairs"
        acc = self._accumulator.get(key, set())
        for m in mapped:
            acc.add(m)
        self._accumulator[key] = acc
        # clear current select values so UI doesn't retain them (clear option defaults)
        for o in getattr(self.paged_select, "options", []):
            try:
                o.default = False
            except Exception:
                pass


class MeetingDetailsModal(discord.ui.Modal, title="會議：其他資訊"):
    start_duration = discord.ui.TextInput(label="開始與時長", placeholder="2026-06-20 14:30 / 60", required=True)
    location = discord.ui.TextInput(label="會議地點或連結（留空則使用部會 meet）", placeholder="語音頻道或 Google Meet 連結", required=False)
    agenda = discord.ui.TextInput(label="手 key 議程", style=discord.TextStyle.paragraph, required=True)
    observers = discord.ui.TextInput(label="列席（可空，逗號分隔，支援 email/名稱/Discord ID）", style=discord.TextStyle.paragraph, required=False)
    extras = discord.ui.TextInput(label="額外出席者（可空，逗號分隔，支援 email/名稱/Discord ID）", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, name, attendees_vals, chairs_vals, dept, dept_link, selected_location=None, selected_location_label=None):
        super().__init__()
        self.name = name
        self.attendees_vals = attendees_vals
        self.chairs_vals = chairs_vals
        self.dept = dept
        self.dept_link = dept_link
        self.selected_location = selected_location
        self.selected_location_label = selected_location_label
        # if a Discord channel was preselected, indicate it in the placeholder
        if self.selected_location_label:
            try:
                self.location.placeholder = f"已選頻道：{self.selected_location_label}（留空即使用此頻道）"
            except Exception:
                pass

    async def on_submit(self, interaction: discord.Interaction):
        # gather data
        start_duration = self.start_duration.value.strip() if self.start_duration.value else None
        duration = 60
        start = None
        if start_duration:
            parts = [p.strip() for p in start_duration.replace('/', ' ').split() if p.strip()]
            if len(parts) >= 2 and parts[-1].isdigit():
                duration = int(parts[-1])
                start = ' '.join(parts[:-1])
            else:
                start = ' '.join(parts)
        raw_location = self.location.value.strip() if self.location.value else ""
        if raw_location:
            location = raw_location
            location_display = raw_location
        elif self.selected_location_label and self.selected_location:
            if self.selected_location.startswith("channel:") and interaction.guild:
                channel_id = int(self.selected_location.split(":", 1)[1])
                location = f"https://discord.com/channels/{interaction.guild.id}/{channel_id}"
                location_display = self.selected_location_label
            else:
                location = self.selected_location_label
                location_display = self.selected_location_label
        else:
            location = self.dept_link
            location_display = self.dept_link
        agenda = self.agenda.value.strip() or ""
        observers_raw = self.observers.value.strip() if self.observers.value else ""
        extras_raw = self.extras.value.strip() if self.extras.value else ""

        # parse start (assumed to be in Asia/Taipei)
        if start:
            try:
                naive_dt = datetime.fromisoformat(start.replace('T', ' '))
                taipei_tz = ZoneInfo("Asia/Taipei")
                start_dt_taipei = naive_dt.replace(tzinfo=taipei_tz)
                start_dt_utc = start_dt_taipei.astimezone(timezone.utc)
            except Exception:
                await interaction.response.send_message("開始時間格式錯誤，請使用 YYYY-MM-DD HH:MM / 60。", ephemeral=True)
                return
        else:
            await interaction.response.send_message("請填寫開始時間與時長。", ephemeral=True)
            return

        end_dt_utc = start_dt_utc + timedelta(minutes=(duration or 60))

        # calendar
        start_str = start_dt_utc.strftime("%Y%m%dT%H%M%SZ")
        end_str = end_dt_utc.strftime("%Y%m%dT%H%M%SZ")
        text = self.name
        details = f"議程:\n{agenda}\n\n會議連結: {location}"
        cal_params = {
            "action": "TEMPLATE",
            "text": text,
            "details": details,
            "location": location,
            "dates": f"{start_str}/{end_str}",
        }
        cal_query = urllib.parse.urlencode(cal_params, quote_via=urllib.parse.quote)
        calendar_url = f"https://www.google.com/calendar/render?{cal_query}"

        member_choices = get_member_choices()
        members_map = {c['label']: c['value'] for c in member_choices}

        def resolve_token(token):
            if '@' in token:
                return token
            if token.isdigit():
                return token
            return members_map.get(token, token)

        observers = [resolve_token(token) for token in [t.strip() for t in observers_raw.split(',') if t.strip()]]
        extras_tokens = [t.strip() for t in extras_raw.split(',') if t.strip()]
        extras_values = [resolve_token(token) for token in extras_tokens]
        extras_display = []
        for token, resolved in zip(extras_tokens, extras_values):
            if resolved == token and '@' not in token and not token.isdigit() and token not in members_map:
                extras_display.append(token)
            else:
                extras_display.append(find_member_discord_mention_by_email(resolved) or resolved)

        mentions = [find_member_discord_mention_by_email(e) or e for e in self.attendees_vals]
        chair_mentions = [find_member_discord_mention_by_email(e) or e for e in self.chairs_vals]
        observer_mentions = [find_member_discord_mention_by_email(e) or e for e in observers]

        start_dt_taipei = start_dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
        end_dt_taipei = end_dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
        start_ts = int(start_dt_utc.timestamp())
        end_ts = int(end_dt_utc.timestamp())

        meeting_id = await create_meeting_via_api(
            title=self.name,
            starts_at=start_dt_taipei.strftime("%Y-%m-%dT%H:%M"),
            department=self.dept,
            participants=get_emails_from_values(self.attendees_vals),
        )

        meeting_text = build_meeting_markdown(
            title=f"會議：{self.name}",
            time_line=f"<t:{start_ts}:F> -> <t:{end_ts}:F>",
            location_display=location_display,
            location=location,
            attendees=mentions,
            extras=extras_display,
            observers=observer_mentions,
            chairs=chair_mentions,
            agenda=agenda,
            calendar_url=calendar_url,
            meeting_id=meeting_id,
        )
        await send_markdown_response(interaction, meeting_text)

        if not meeting_id:
            try:
                await interaction.followup.send("未同步至會議系統（未設定 API 或呼叫失敗）。", ephemeral=True)
            except discord.HTTPException:
                pass

        # Create Discord Scheduled Event
        try:
            guild = interaction.guild
            if guild:
                event_description = f"議程:\n{agenda}\n\n會議連結: {location}"
                await guild.create_scheduled_event(
                    name=self.name,
                    start_time=start_dt_utc,
                    end_time=end_dt_utc,
                    description=event_description,
                    entity_type=discord.EntityType.external,
                    location=location or "線上",
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
                await interaction.followup.send("已在 Discord 建立會議活動。", ephemeral=True)
        except Exception as e:
            print(f"Failed to create scheduled event: {e}")
            await interaction.followup.send(f"無法在 Discord 建立活動：{e}", ephemeral=True)

        emails = get_emails_from_values(self.attendees_vals + self.chairs_vals + observers + extras_values)
        if emails:
            email_body = f"{text}\n\n時間: {start_dt_taipei.isoformat()} - {end_dt_taipei.isoformat()}\n地點: {location}\n\n議程:\n{agenda}\n\n加入至 Google Calendar: {calendar_url}"
            email_html = f"<p>{text}</p><p>時間: {start_dt_taipei.isoformat()} - {end_dt_taipei.isoformat()}<br>地點: {location}</p><p>議程:<br>{agenda.replace(chr(10), '<br>')}</p><p><a href=\"{calendar_url}\">加入至 Google Calendar</a></p>"
            sent = await try_send_emails(subject=f"邀請：{self.name}", body=email_body, html=email_html, recipients=emails)
            if sent:
                await interaction.followup.send(f"已發送 email 給 {len(emails)} 位與會者。", ephemeral=True)
            else:
                await interaction.followup.send("未設定 SMTP，無法發送 email。", ephemeral=True)


def split_discord_message(content: str, limit: int = 2000) -> list[str]:
    lines = content.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) <= limit:
            current += line
            continue
        if current:
            chunks.append(current)
            current = line
        else:
            # line itself is too long, split it raw
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            current = ""
    if current:
        chunks.append(current)
    return chunks


async def send_markdown_response(interaction: discord.Interaction, content: str, ephemeral: bool = False):
    chunks = split_discord_message(content)
    await interaction.response.send_message(chunks[0], ephemeral=ephemeral, suppress_embeds=True)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral, suppress_embeds=True)


def build_meeting_markdown(
    title: str,
    time_line: str,
    location_display: str,
    location: str,
    attendees: list[str],
    extras: list[str] | None,
    observers: list[str],
    chairs: list[str],
    agenda: str,
    calendar_url: str,
    meeting_id: str | None = None,
) -> str:
    parts = [
        f"**{title}**",
        "────────────────────────────",
        "",
        f"**時間**",
        f"  {time_line}",
        "",
    ]
    if agenda:
        parts.extend(["**議程**", f"  \n{agenda}", ""])
    if location_display != location:
        parts.extend(["**地點/連結**", f"  {location_display}", f"  {location}"])
    else:
        parts.extend(["**地點/連結**", f"  {location}"])
    parts.extend([
        "",
        f"**出席**",
        f"  {', '.join(attendees) if attendees else '無'}",
    ])
    if extras is not None:
        parts.extend([
            "",
            f"**額外出席**",
            f"  {', '.join(extras) if extras else '無'}",
        ])
    parts.extend([
        "",
        f"**列席**",
        f"  {', '.join(observers) if observers else '無'}",
        "",
        f"**主席**",
        f"  {', '.join(chairs) if chairs else '無'}",
        "",
        f"**Google Calendar**",
        f"  [點我加入日曆]({calendar_url})",
    ])
    if meeting_id:
        parts.extend([
            "",
            f"**會議系統**",
            f"  簽到連結: https://meeting.tschoolsu.org/checkin?id={meeting_id}",
        ])
    quoted = []
    for line in "\n".join(parts).splitlines():
        quoted.append(f"> {line}" if line else "> ")
    return "\n".join(quoted)


async def create_meeting_via_api(
    title: str,
    starts_at: str,
    department: str,
    participants: list,
) -> str | None:
    """POST the meeting to the external meetings API. Returns the meeting id on success, else None."""
    if not MEETINGS_API_URL or not MEETINGS_API_KEY:
        return None

    payload = {
        "title": title,
        "starts_at": starts_at,
        "department": department,
        "participants": participants,
        "voting_enabled": False,
        "questions": [],
    }
    url = f"{MEETINGS_API_URL.rstrip('/')}/api/v1/meetings"
    headers = {
        "Authorization": f"Bearer {MEETINGS_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status >= 400:
                    text = await response.text()
                    print(f"Meetings API error {response.status}: {text}")
                    return None
                data = await response.json(content_type=None)
                return str(data.get("id")) if data else None
    except Exception as exc:
        print(f"Meetings API error: {exc}")
        return None


async def try_send_emails(subject: str, body: str, recipients: list, html: str = None) -> bool:
    """Attempt to send emails using SMTP environment vars. Returns True on success."""
    # smtplib is blocking; run it in a thread so the bot's event loop isn't stalled.
    return await asyncio.to_thread(_send_emails_sync, subject, body, recipients, html)


def _send_emails_sync(subject: str, body: str, recipients: list, html: str = None) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT") or 587)
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_addr = os.getenv("FROM_EMAIL") or smtp_user

    if not smtp_host or not smtp_user or not smtp_pass or not from_addr:
        return False

    try:
        msg = email.message.EmailMessage()
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return True
    except Exception as exc:
        print(f"Email send error: {exc}")
        return False


class MeetingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    @app_commands.command(name="meeting", description="建立會議並通知出席者（用互動選人與表單）")
    @app_commands.describe(
        name="會議名稱",
        department="會議部別",
    )
    @app_commands.choices(department=[
        app_commands.Choice(name=k, value=k) for k in DEPARTMENT_MEET_LINKS.keys()
    ])
    async def meeting(
        self,
        interaction: discord.Interaction,
        name: str,
        department: app_commands.Choice[str],
    ):
        dept = department.value if isinstance(department, app_commands.Choice) else (department or "無分類")
        dept_link = DEPARTMENT_MEET_LINKS.get(dept)

        view = MeetingSelectionView(name, dept, dept_link, guild=interaction.guild)
        await interaction.response.send_message(f"選擇出席與主席：{name}", view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MeetingCog(bot))
