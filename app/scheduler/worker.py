"""APScheduler entrypoint for the Render Background Worker."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

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


def main() -> None:
    assert_runtime_env()
    sched = BlockingScheduler(timezone="UTC")

    # Scrape every 6h. Stagger 13 min after the hour to dodge contention.
    sched.add_job(
        scrape_job.run,
        CronTrigger(hour="*/6", minute=13),
        id="scrape",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    # Deliver hourly at :37 — far from scrape, off-the-hour.
    sched.add_job(
        delivery_job.run,
        CronTrigger(minute=37),
        id="deliver",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    # Prune nightly.
    sched.add_job(
        prune_job.run,
        CronTrigger(hour=3, minute=17),
        id="prune",
        max_instances=1,
        coalesce=True,
    )

    log.info(
        "worker: starting with %d jobs (%s)",
        len(sched.get_jobs()),
        ", ".join(j.id for j in sched.get_jobs()),
    )
    sched.start()


if __name__ == "__main__":
    main()
