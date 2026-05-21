"""Unified entrypoint for Render's Web Service (free tier).

Runs in a single process, sharing one asyncio event loop:
  - python-telegram-bot in polling mode (handles user commands)
  - APScheduler with three cron jobs (scrape, deliver, prune)
  - Tornado HTTP server exposing /healthz so Render keeps the service alive
  - Optional self-ping job that hits /healthz every 12 min so free-tier
    Web Services (which sleep after 15 min idle) don't suspend the scheduler

The scheduler jobs are sync; we hand them to asyncio's default executor so
they don't block the event loop while Playwright is running.
"""
import asyncio
import logging
import os

import httpx
import tornado.web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.app import build_app
from app.config import assert_runtime_env
from app.scheduler import delivery_job, prune_job, scrape_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.INFO)
log = logging.getLogger("worker")


async def _run_blocking(fn):
    """Run a sync function in the default executor so it doesn't block asyncio."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, fn)


async def _scheduled_scrape():
    log.info("tick: scrape")
    await _run_blocking(scrape_job.run)


async def _scheduled_delivery():
    log.info("tick: delivery")
    await _run_blocking(delivery_job.run)


async def _scheduled_prune():
    log.info("tick: prune")
    await _run_blocking(prune_job.run)


async def _scheduled_self_ping():
    """Hit our own /healthz so Render's free-tier 15-min idle sleep doesn't suspend us."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        return  # not on Render or var not set
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/healthz")
            log.info("self-ping %s -> %s", url, r.status_code)
    except Exception as e:
        log.warning("self-ping failed: %s", e)


class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/plain")
        self.write("ok")


def build_http_app() -> tornado.web.Application:
    return tornado.web.Application([(r"/healthz", HealthHandler), (r"/", HealthHandler)])


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        _scheduled_scrape,
        CronTrigger(hour="*/6", minute=13),
        id="scrape",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        _scheduled_delivery,
        CronTrigger(minute=37),
        id="deliver",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        _scheduled_prune,
        CronTrigger(hour=3, minute=17),
        id="prune",
        max_instances=1,
        coalesce=True,
    )
    # Self-ping every 12 min to defeat Render free-tier 15-min idle sleep.
    # No-op when RENDER_EXTERNAL_URL isn't set (e.g. local dev).
    sched.add_job(
        _scheduled_self_ping,
        IntervalTrigger(minutes=12),
        id="self_ping",
        max_instances=1,
        coalesce=True,
    )
    return sched


async def amain() -> None:
    assert_runtime_env()
    app = build_app()
    sched = build_scheduler()
    http_app = build_http_app()

    # Render injects PORT; local dev falls back to 8080.
    port = int(os.environ.get("PORT", "8080"))
    http_server = http_app.listen(port, address="0.0.0.0")
    log.info("http: listening on 0.0.0.0:%d (/healthz)", port)

    log.info(
        "worker: starting bot (polling) + scheduler (%s)",
        ", ".join(j.id for j in sched.get_jobs()),
    )

    # PTB v21 manages its own event loop with run_polling, which conflicts
    # with us managing the asyncio loop here. Instead drive PTB via its
    # explicit lifecycle methods so we can co-host APScheduler in the same loop.
    await app.initialize()
    await app.start()
    sched.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query"])

    log.info("worker: running")
    try:
        # Block forever, until cancelled (SIGTERM from Render on redeploy).
        await asyncio.Event().wait()
    finally:
        log.info("worker: shutting down")
        http_server.stop()
        await app.updater.stop()
        sched.shutdown(wait=False)
        await app.stop()
        await app.shutdown()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
