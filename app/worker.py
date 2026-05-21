"""Unified entrypoint for Render's Background Worker.

Runs in a single process, sharing one asyncio event loop:
  - python-telegram-bot in polling mode (handles user commands)
  - APScheduler with three cron jobs (scrape, deliver, prune)

The scheduler jobs are sync; we hand them to asyncio's default executor so
they don't block the event loop while Playwright is running.
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
    return sched


async def amain() -> None:
    assert_runtime_env()
    app = build_app()
    sched = build_scheduler()

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
