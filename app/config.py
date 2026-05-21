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
def _normalize_db_url(raw: str) -> str:
    """Render-managed Postgres ships as 'postgres://…'. SQLAlchemy 2.x wants
    the 'postgresql+psycopg://…' form. Rewrite to the explicit driver."""
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        return "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


DB_URL = _normalize_db_url(_optional("DB_URL", "sqlite:///./bot.db"))
WEBHOOK_URL = _optional("WEBHOOK_URL")
WEBHOOK_SECRET = _optional("WEBHOOK_SECRET")
WEBHOOK_LISTEN = _optional("WEBHOOK_LISTEN", "0.0.0.0")
# Render injects PORT for web services. Fall back to WEBHOOK_PORT for local use.
WEBHOOK_PORT = int(_optional("PORT", "") or _optional("WEBHOOK_PORT", "8080") or "8080")
ADMIN_CHAT_ID = int(_optional("ADMIN_CHAT_ID", "0") or "0")
QUERIES_PATH = Path(_optional("QUERIES_PATH", "app/queries.yaml"))


def assert_runtime_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    if BOT_MODE == "webhook" and not (WEBHOOK_URL and WEBHOOK_SECRET):
        raise RuntimeError("BOT_MODE=webhook requires WEBHOOK_URL and WEBHOOK_SECRET")
