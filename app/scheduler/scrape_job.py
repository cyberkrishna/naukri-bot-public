"""Scrape Naukri for the broad superset of queries and upsert into jobs_pool."""
import argparse
import logging
from datetime import datetime, timedelta, timezone

import yaml
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import QUERIES_PATH
from app.db import engine, session_scope
from app.models import JobPool, ScrapeRun
from app.scraper import scrape_all

log = logging.getLogger("scrape")

POOL_RETENTION = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_queries() -> list[str]:
    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("queries", []))


def _upsert_jobs(jobs: list, dry_run: bool) -> int:
    """Upsert jobs into jobs_pool. Returns count of jobs processed."""
    if not jobs:
        return 0
    if dry_run:
        for j in jobs[:5]:
            log.info("[DRY] %s | %s | %s | %s", j.job_id, j.title, j.company, j.location)
        log.info("[DRY] would upsert %d jobs total", len(jobs))
        return len(jobs)

    rows = [
        {
            "job_id": j.job_id,
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "experience": j.experience,
            "posted": j.posted,
            "description": j.description,
            "url": j.url,
            "query": j.query,
            "scraped_at": _now(),
        }
        for j in jobs
    ]
    dialect = engine.dialect.name
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert_fn(JobPool).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=[JobPool.job_id])
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def _prune_old_jobs() -> int:
    cutoff = _now() - POOL_RETENTION
    with session_scope() as s:
        res = s.execute(delete(JobPool).where(JobPool.scraped_at < cutoff))
        return res.rowcount or 0


def run(dry_run: bool = False) -> None:
    queries = _load_queries()
    log.info("scrape: starting with %d queries", len(queries))

    run_id = None
    if not dry_run:
        with session_scope() as s:
            r = ScrapeRun(status="running")
            s.add(r)
            s.flush()
            run_id = r.id

    try:
        jobs = scrape_all(queries, experience_years=0, locations=[])
        log.info("scrape: %d raw jobs", len(jobs))
        n = _upsert_jobs(jobs, dry_run=dry_run)
        pruned = 0 if dry_run else _prune_old_jobs()
        log.info("scrape: upserted=%d pruned=%d", n, pruned)

        if not dry_run and run_id is not None:
            with session_scope() as s:
                r = s.get(ScrapeRun, run_id)
                r.finished_at = _now()
                r.jobs_found = n
                r.status = "done"
    except Exception as e:
        log.exception("scrape failed: %s", e)
        if not dry_run and run_id is not None:
            with session_scope() as s:
                r = s.get(ScrapeRun, run_id)
                r.finished_at = _now()
                r.status = "failed"
                r.error = str(e)[:1000]
        raise


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
