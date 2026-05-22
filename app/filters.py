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

    if require_zero_experience and not _is_zero_experience(job.experience, h):
        return False

    return True


# Words/phrases that signal the job is fresher-friendly even without a
# numeric experience range. Indeed/LinkedIn rarely expose Naukri-style
# "0-3 yrs" strings, so we look in the full haystack (title + description
# + experience). We're permissive here: a job with no experience signal
# either way is treated as fresher-eligible so we don't filter to zero.
_FRESHER_HINTS = (
    "fresher", "freshers", "intern", "internship",
    "entry level", "entry-level", "graduate", "trainee",
    "no experience", "0+ years", "0-1 year", "0 year",
)

# Words that strongly suggest senior-only roles. Used to REJECT jobs when
# require_zero_experience is true and we have no positive fresher signal.
_SENIOR_HINTS = (
    "senior", "sr.", "sr ", "lead ", "principal", "staff engineer",
    "manager", "director", "head of", "architect",
)

# Regex that catches "N+ years" / "N years experience" for any N >= 3.
_SENIOR_YEARS_RE = re.compile(
    r"\b([3-9]|1[0-9])\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def _is_zero_experience(exp: str, haystack: str = "") -> bool:
    """True if posting looks fresher-eligible.

    Strategy:
      1. If experience field has explicit 0-N years range or 'fresher' → True.
      2. If experience field has positive N-year range (N>0) → False.
      3. Otherwise fall back to scanning the haystack: positive fresher hints
         → True; senior-only hints with no fresher hint → False.
      4. No signal either way → True (don't filter to zero on platforms
         like Indeed/LinkedIn that don't surface a clean experience range).
    """
    e = (exp or "").lower().strip()
    h = (haystack or "").lower()

    # 1. Explicit fresher signal in the structured experience field.
    if "fresher" in e:
        return True
    m = re.search(r"(\d+)\s*[-–to ]+\s*\d+\s*yr", e)
    if m:
        return int(m.group(1)) == 0
    m = re.search(r"^(\d+)\s*yr", e)
    if m:
        return int(m.group(1)) == 0

    # 2. No structured experience — fall back to text hints.
    has_fresher_hint = any(hint in h for hint in _FRESHER_HINTS)
    if has_fresher_hint:
        return True
    has_senior_hint = any(hint in h for hint in _SENIOR_HINTS)
    if has_senior_hint:
        return False
    if _SENIOR_YEARS_RE.search(h):
        return False

    # 3. Neither positive nor negative signal — treat as eligible.
    return True


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
