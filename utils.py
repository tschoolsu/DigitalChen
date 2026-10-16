import json
import os
from pathlib import Path

from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

DATA_FILE = Path(__file__).resolve().parent / "members.json"

ALLOWED_USER_IDS = {
    int(x.strip())
    for x in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if x.strip()
}


def load_members():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_member(data: dict):
    members = [m for m in load_members() if m["discord_id"] != data["discord_id"]]
    members.append(data)
    DATA_FILE.write_text(
        json.dumps(members, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def find_member_discord_mention_by_email(email: str):
    email = (email or "").lower()
    for member in load_members():
        if member.get("student_email", "").lower() == email and member.get("discord_id"):
            return f"<@{member['discord_id']}>"
    return None


def authorized_only():
    return app_commands.check(lambda interaction: interaction.user.id in ALLOWED_USER_IDS)


def get_member_choices():
    """Return a list of member choice dicts with 'label' and 'value'.

    'value' will be the member's student_email when available, otherwise discord_id.
    """
    choices = []
    for m in load_members():
        label = m.get("name") or m.get("discord_username") or m.get("student_email")
        value = m.get("student_email") or m.get("discord_id") or label
        choices.append({"label": label, "value": value})
    return choices


def get_emails_from_values(values):
    """Given a list of values (student_email or discord_id), return deduplicated list of emails found."""
    if not values:
        return []
    email_by_discord_id = {
        m.get("discord_id"): m.get("student_email")
        for m in load_members()
        if m.get("discord_id")
    }
    emails = []
    for v in values:
        if not v:
            continue
        email = v if "@" in v else email_by_discord_id.get(v)
        if email and email not in emails:
            emails.append(email)
    return emails
