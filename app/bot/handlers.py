import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config import ADMIN_CHAT_ID
from app.db import session_scope
from app.delivery import deliver_one
from app.models import SentJob, User

WELCOME = (
    "👋 Welcome to the Naukri Job Alerts bot.\n\n"
    "You're now subscribed with default settings (fresher-level, daily digest).\n\n"
    "Set your filters:\n"
    "• <code>/set_keywords python, ml, pytorch</code>\n"
    "• <code>/set_locations bangalore, remote</code>\n"
    "• <code>/set_experience 0</code>\n"
    "• <code>/set_frequency daily</code>\n\n"
    "Run <code>/status</code> to see your current settings or <code>/help</code> for all commands."
)

HELP = (
    "<b>Commands</b>\n"
    "• /start — subscribe with defaults\n"
    "• /set_keywords python,ml — required terms (any match)\n"
    "• /set_exclude senior,manager — disqualifying terms\n"
    "• /set_locations bangalore,remote — empty = anywhere\n"
    "• /set_experience 0 — years (0 = fresher-only)\n"
    "• /set_frequency daily|twice_daily|weekly\n"
    "• /pause — stop sending\n"
    "• /resume — resume sending\n"
    "• /unsubscribe — remove me\n"
    "• /status — show my settings\n"
    "• /help — this message"
)

VALID_FREQUENCIES = {"daily", "twice_daily", "weekly"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _arg_text(update: Update) -> str:
    """Return everything after the command, raw. Empty string if none."""
    text = update.message.text or ""
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _parse_csv(raw: str) -> list[str]:
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _upsert_user(chat_id: int, username: str | None) -> None:
    with session_scope() as s:
        u = s.get(User, chat_id)
        if u is None:
            s.add(User(chat_id=chat_id, username=username, next_send_at=_now()))
        else:
            u.username = username
            if u.status == "unsubscribed":
                u.status = "active"
                u.next_send_at = _now()


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    user = update.effective_user
    _upsert_user(chat.id, user.username if user else None)
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start to begin.", parse_mode=ParseMode.HTML
            )
            return

        seven_days_ago = _now() - timedelta(days=7)
        sent_total = (
            s.query(SentJob)
            .filter(SentJob.chat_id == chat.id, SentJob.sent_at >= seven_days_ago)
            .count()
        )

        last = u.last_send_at.strftime("%Y-%m-%d %H:%M UTC") if u.last_send_at else "never"
        keywords = u.keywords or "(none — will match defaults)"
        excludes = u.exclude_keywords or "(none)"
        locations = u.locations or "(anywhere)"
        fresher_tag = " (fresher-only)" if u.require_zero_experience else ""

        msg = (
            f"<b>Your settings</b>\n"
            f"• status: <code>{u.status}</code>\n"
            f"• keywords: <code>{keywords}</code>\n"
            f"• exclude: <code>{excludes}</code>\n"
            f"• locations: <code>{locations}</code>\n"
            f"• experience: <code>{u.experience_years} yrs</code>{fresher_tag}\n"
            f"• frequency: <code>{u.frequency}</code>\n"
            f"• last delivery: <code>{last}</code>\n"
            f"• jobs sent (7d): <code>{sent_total}</code>"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def _require_user(update: Update) -> User | None:
    """Return the User row for the chat, or send 'not subscribed' and return None."""
    chat = update.effective_chat
    if chat is None:
        return None
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return None
        s.expunge(u)
        return u


async def _show_or_update_csv_field(
    update: Update, field_name: str, display_name: str, empty_meaning: str
) -> None:
    """Common implementation for /set_keywords, /set_exclude, /set_locations.

    No args → show current value.
    With args → parse CSV, save, echo result.
    """
    chat = update.effective_chat
    if chat is None:
        return
    raw = _arg_text(update)
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return

        if not raw:
            current = getattr(u, field_name) or f"(none — {empty_meaning})"
            await update.message.reply_text(
                f"Current {display_name}: <code>{current}</code>\n"
                f"Use <code>/{update.message.text.lstrip('/').split()[0]} term1, term2</code> to set.",
                parse_mode=ParseMode.HTML,
            )
            return

        parsed = _parse_csv(raw)
        new_val = ",".join(parsed)
        setattr(u, field_name, new_val)

    pretty = ", ".join(parsed) if parsed else f"(cleared — {empty_meaning})"
    await update.message.reply_text(
        f"✅ {display_name} set to: <code>{pretty}</code>", parse_mode=ParseMode.HTML
    )


async def set_keywords(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_or_update_csv_field(update, "keywords", "keywords", "will match defaults")


async def set_exclude(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_or_update_csv_field(update, "exclude_keywords", "exclude", "nothing excluded")


async def set_locations(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_or_update_csv_field(update, "locations", "locations", "anywhere")


async def set_experience(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    raw = _arg_text(update)
    if not raw:
        with session_scope() as s:
            u = s.get(User, chat.id)
            if u is None:
                await update.message.reply_text(
                    "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
                )
                return
            tag = " (fresher-only)" if u.require_zero_experience else ""
            await update.message.reply_text(
                f"Current experience: <code>{u.experience_years} yrs</code>{tag}\n"
                f"Use <code>/set_experience 0</code> (0 = fresher-only) or any int 0–20.",
                parse_mode=ParseMode.HTML,
            )
            return

    try:
        years = int(raw)
    except ValueError:
        await update.message.reply_text(
            "Experience must be an integer (e.g. <code>0</code>, <code>2</code>).",
            parse_mode=ParseMode.HTML,
        )
        return
    if not 0 <= years <= 20:
        await update.message.reply_text(
            "Experience must be between 0 and 20.", parse_mode=ParseMode.HTML
        )
        return

    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return
        u.experience_years = years
        u.require_zero_experience = years == 0

    tag = " (fresher-only)" if years == 0 else ""
    await update.message.reply_text(
        f"✅ Experience set to <code>{years} yrs</code>{tag}.", parse_mode=ParseMode.HTML
    )


async def set_frequency(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    raw = _arg_text(update).lower()
    if not raw:
        with session_scope() as s:
            u = s.get(User, chat.id)
            if u is None:
                await update.message.reply_text(
                    "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
                )
                return
            await update.message.reply_text(
                f"Current frequency: <code>{u.frequency}</code>\n"
                f"Options: <code>daily</code>, <code>twice_daily</code>, <code>weekly</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

    if raw not in VALID_FREQUENCIES:
        await update.message.reply_text(
            "Invalid frequency. Choose: <code>daily</code>, <code>twice_daily</code>, <code>weekly</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return
        u.frequency = raw
        u.next_send_at = _now()

    await update.message.reply_text(
        f"✅ Frequency set to <code>{raw}</code>. Next delivery on the next scheduler tick.",
        parse_mode=ParseMode.HTML,
    )


async def pause(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return
        u.status = "paused"
    await update.message.reply_text(
        "⏸ Paused. Send /resume when you want jobs again.", parse_mode=ParseMode.HTML
    )


async def resume(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You're not subscribed. Send /start first.", parse_mode=ParseMode.HTML
            )
            return
        u.status = "active"
        u.next_send_at = _now()
    await update.message.reply_text(
        "▶️ Resumed. You'll get jobs on the next scheduler tick.", parse_mode=ParseMode.HTML
    )


async def unsubscribe(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    with session_scope() as s:
        u = s.get(User, chat.id)
        if u is None:
            await update.message.reply_text(
                "You were already not subscribed.", parse_mode=ParseMode.HTML
            )
            return
        u.status = "unsubscribed"
    await update.message.reply_text(
        "👋 Unsubscribed. No more jobs will be sent. Send /start anytime to resubscribe.",
        parse_mode=ParseMode.HTML,
    )


async def test_deliver(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: force an immediate delivery to the admin's own chat.

    Runs deliver_one in a thread because it uses sync requests + sqlalchemy.
    """
    chat = update.effective_chat
    if chat is None:
        return
    if ADMIN_CHAT_ID == 0 or chat.id != ADMIN_CHAT_ID:
        return  # silently ignore for non-admins
    await update.message.reply_text("⏳ Running delivery now…", parse_mode=ParseMode.HTML)
    sent, failed = await asyncio.to_thread(deliver_one, chat.id, False)
    await update.message.reply_text(
        f"✅ delivery done: jobs_sent={sent} batches_failed={failed}",
        parse_mode=ParseMode.HTML,
    )
