"""Per-user delivery: query jobs_pool, filter, send batched Telegram messages."""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select

from app.config import BOT_TOKEN
from app.db import session_scope
from app.filters import location_matches, passes
from app.formatter import chunked, format_job
from app.models import JobPool, SentJob, User
from app.scraper import Job

log = logging.getLogger("delivery")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_JOBS_PER_DELIVERY = 30
BATCH_SIZE = 10
RECENT_JOBS_WINDOW = timedelta(days=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _frequency_to_days(freq: str) -> int:
    return {"daily": 1, "twice_daily": 1, "weekly": 7}.get(freq, 1)


def _frequency_to_delta(freq: str) -> timedelta:
    return {
        "daily": timedelta(days=1),
        "twice_daily": timedelta(hours=12),
        "weekly": timedelta(days=7),
    }.get(freq, timedelta(days=1))


def _csv(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _job_pool_row_to_job(row: JobPool) -> Job:
    return Job(
        job_id=row.job_id,
        title=row.title,
        company=row.company,
        location=row.location,
        experience=row.experience,
        posted=row.posted,
        description=row.description,
        url=row.url,
        query=row.query,
    )


def _send_telegram(chat_id: int, html: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("[DRY] -> chat=%s len=%d", chat_id, len(html))
        return True
    url = TELEGRAM_API.format(token=BOT_TOKEN)
    try:
        r = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=20,
        )
        if not r.ok:
            log.warning("telegram %s -> %s %s", chat_id, r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.exception("telegram send failed for chat=%s: %s", chat_id, e)
        return False


def _candidate_jobs_for_user(s, user: User) -> list[JobPool]:
    """Pool jobs scraped recently that this user hasn't been sent yet."""
    cutoff = _now() - RECENT_JOBS_WINDOW
    sent_subq = select(SentJob.job_id).where(SentJob.chat_id == user.chat_id).scalar_subquery()
    stmt = (
        select(JobPool)
        .where(JobPool.scraped_at >= cutoff)
        .where(~JobPool.job_id.in_(sent_subq))
        .order_by(JobPool.scraped_at.desc())
    )
    return list(s.execute(stmt).scalars())


def _filter_for_user(candidates: list[JobPool], user: User) -> list[Job]:
    keywords = _csv(user.keywords)
    excludes = _csv(user.exclude_keywords)
    locations = _csv(user.locations)
    posted_within = _frequency_to_days(user.frequency)
    out: list[Job] = []
    for row in candidates:
        j = _job_pool_row_to_job(row)
        if not passes(
            j,
            must_include_any=keywords,
            exclude_any=excludes,
            posted_within_days=posted_within,
            require_zero_experience=user.require_zero_experience,
        ):
            continue
        if not location_matches(j.location, locations):
            continue
        out.append(j)
        if len(out) >= MAX_JOBS_PER_DELIVERY:
            break
    return out


def _send_jobs_to_user(chat_id: int, jobs: list[Job], dry_run: bool) -> int:
    """Send batched messages. Returns count of jobs successfully sent."""
    if not jobs:
        return 0
    header = f"<b>📬 {len(jobs)} new Naukri job(s) for you</b>"
    if not _send_telegram(chat_id, header, dry_run):
        return 0
    sent = 0
    for batch in chunked(jobs, BATCH_SIZE):
        body = "\n\n———\n\n".join(format_job(j) for j in batch)
        if _send_telegram(chat_id, body, dry_run):
            sent += len(batch)
        time.sleep(0.05)  # gentle per-chat rate limit
    return sent


def deliver_one(chat_id: int, dry_run: bool = False) -> int:
    """Run delivery for a single chat_id. Returns jobs delivered. Always advances next_send_at."""
    with session_scope() as s:
        u = s.get(User, chat_id)
        if u is None or u.status != "active":
            return 0
        candidates = _candidate_jobs_for_user(s, u)
        jobs = _filter_for_user(candidates, u)

        sent = _send_jobs_to_user(chat_id, jobs, dry_run)

        if not dry_run:
            now = _now()
            for j in jobs[:sent]:
                s.merge(SentJob(chat_id=chat_id, job_id=j.job_id, sent_at=now))
            u.last_send_at = now
            u.next_send_at = now + _frequency_to_delta(u.frequency)
        return sent


def deliver_due(dry_run: bool = False) -> dict:
    """Run delivery for all active users whose next_send_at <= now."""
    with session_scope() as s:
        due = (
            s.execute(
                select(User.chat_id).where(
                    User.status == "active", User.next_send_at <= _now()
                )
            )
            .scalars()
            .all()
        )
    log.info("delivery: %d users due", len(due))
    stats = {"users": len(due), "jobs_sent": 0, "errors": 0}
    for chat_id in due:
        try:
            stats["jobs_sent"] += deliver_one(chat_id, dry_run=dry_run)
        except Exception as e:
            log.exception("deliver_one failed for chat=%s: %s", chat_id, e)
            stats["errors"] += 1
    return stats
