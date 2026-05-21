"""Nightly cleanup: drop sent_jobs rows older than 30 days."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db import session_scope
from app.models import SentJob

log = logging.getLogger("prune")

SENT_RETENTION = timedelta(days=30)


def run() -> None:
    cutoff = datetime.now(timezone.utc) - SENT_RETENTION
    with session_scope() as s:
        res = s.execute(delete(SentJob).where(SentJob.sent_at < cutoff))
        log.info("prune: deleted %d sent_jobs rows older than %s", res.rowcount or 0, cutoff)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run()


if __name__ == "__main__":
    main()
