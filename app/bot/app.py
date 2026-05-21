import logging

from telegram import BotCommand
from telegram.ext import AIORateLimiter, Application, CommandHandler

from app.bot.handlers import (
    help_cmd,
    pause,
    resume,
    set_exclude,
    set_experience,
    set_frequency,
    set_keywords,
    set_locations,
    start,
    status,
    unsubscribe,
)
from app.config import (
    BOT_MODE,
    BOT_TOKEN,
    WEBHOOK_LISTEN,
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
    WEBHOOK_URL,
    assert_runtime_env,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Silence httpx — it logs full request URLs including the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("bot")


COMMANDS = [
    BotCommand("start", "subscribe with defaults"),
    BotCommand("set_keywords", "required terms (CSV)"),
    BotCommand("set_exclude", "disqualifying terms (CSV)"),
    BotCommand("set_locations", "location filter (CSV)"),
    BotCommand("set_experience", "years (0 = fresher-only)"),
    BotCommand("set_frequency", "daily | twice_daily | weekly"),
    BotCommand("pause", "stop sending"),
    BotCommand("resume", "resume sending"),
    BotCommand("unsubscribe", "remove me"),
    BotCommand("status", "show my settings"),
    BotCommand("help", "command reference"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(COMMANDS)
    log.info("Registered %d bot commands", len(COMMANDS))


def build_app() -> Application:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("set_keywords", set_keywords))
    application.add_handler(CommandHandler("set_exclude", set_exclude))
    application.add_handler(CommandHandler("set_locations", set_locations))
    application.add_handler(CommandHandler("set_experience", set_experience))
    application.add_handler(CommandHandler("set_frequency", set_frequency))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    return application


def main() -> None:
    assert_runtime_env()
    app = build_app()

    if BOT_MODE == "webhook":
        log.info("Starting in webhook mode on %s:%d", WEBHOOK_LISTEN, WEBHOOK_PORT)
        app.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            url_path=WEBHOOK_SECRET,
            secret_token=WEBHOOK_SECRET,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_SECRET}",
        )
    else:
        log.info("Starting in polling mode")
        app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
