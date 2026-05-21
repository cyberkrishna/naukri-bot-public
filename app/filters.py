import re

from app.scraper import Job


def _norm(s: str) -> str:
    return (s or "").lower()


def _haystack(job: Job) -> str:
    return _norm(f"{job.title} {job.description} {job.experience}")


def passes(
    job: Job,
    must_include_any: list[str],
    exclude_any: list[str],
    posted_within_days: int,
    require_zero_experience: bool = False,
) -> bool:
    h = _haystack(job)

    if exclude_any and any(kw.lower() in h for kw in exclude_any):
        return False

    if must_include_any and not any(kw.lower() in h for kw in must_include_any):
        return False

    if posted_within_days is not None and job.posted:
        if not _is_recent(job.posted, posted_within_days):
            return False

    if require_zero_experience and not _is_zero_experience(job.experience):
        return False

    return True


def _is_zero_experience(exp: str) -> bool:
    """True only if posting requires 0 years (fresher / 0-x range)."""
    if not exp:
        return False
    e = exp.lower().strip()
    if "fresher" in e:
        return True
    m = re.search(r"(\d+)\s*[-–to ]+\s*\d+\s*yr", e)
    if m:
        return int(m.group(1)) == 0
    m = re.search(r"^(\d+)\s*yr", e)
    if m:
        return int(m.group(1)) == 0
    return False


def _is_recent(posted: str, within_days: int) -> bool:
    p = posted.lower().strip()
    if "just now" in p or "few" in p or "hour" in p or "today" in p:
        return True
    if "yesterday" in p:
        return within_days >= 1
    m = re.search(r"(\d+)\s*day", p)
    if m:
        return int(m.group(1)) <= within_days
    if "week" in p or "month" in p:
        return False
    return True


def location_matches(job_location: str, user_locations: list[str]) -> bool:
    """True if user has no location preference OR any of user's locations
    is a case-insensitive substring of job.location."""
    if not user_locations:
        return True
    if not job_location:
        return False
    job_loc_lower = job_location.lower()
    return any(loc.lower().strip() in job_loc_lower for loc in user_locations if loc.strip())
