import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


BOT_TOKEN = _optional("BOT_TOKEN")
BOT_MODE = _optional("BOT_MODE", "polling").lower()
DB_URL = _optional("DB_URL", "sqlite:///./bot.db")
WEBHOOK_URL = _optional("WEBHOOK_URL")
WEBHOOK_SECRET = _optional("WEBHOOK_SECRET")
WEBHOOK_LISTEN = _optional("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT = int(_optional("WEBHOOK_PORT", "8080") or "8080")
ADMIN_CHAT_ID = int(_optional("ADMIN_CHAT_ID", "0") or "0")
QUERIES_PATH = Path(_optional("QUERIES_PATH", "app/queries.yaml"))


def assert_runtime_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    if BOT_MODE == "webhook" and not (WEBHOOK_URL and WEBHOOK_SECRET):
        raise RuntimeError("BOT_MODE=webhook requires WEBHOOK_URL and WEBHOOK_SECRET")
