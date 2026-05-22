"""Scrape job boards using the python-jobspy library.

We were originally scraping Naukri via Playwright + HTML, but Naukri now
captcha-gates its JSON API for every IP (Render and residential alike) and
strips the job rows from server-rendered HTML, fetching them via the same
gated XHR. There's no path to scrape Naukri from the cloud without a
human-solved session.

JobSpy supports several boards out of the box; local testing showed Indeed
(India) returns ~15 fresh jobs per query with no auth, and LinkedIn
returns a few more. We hit both: Indeed for volume, LinkedIn as a free
complement. Naukri is dropped.

The Job dataclass and scrape_all() signature are unchanged so
scheduler/scrape_job.py and the upsert path keep working.
"""
import hashlib
import logging
import time
from dataclasses import dataclass, asdict
from typing import Iterable

import pandas as pd  # ships with jobspy
from jobspy import scrape_jobs as _jobspy_scrape

log = logging.getLogger("scraper")

SITES = ["indeed", "linkedin"]
RESULTS_PER_QUERY = 20  # per site
HOURS_OLD = 72


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    location: str
    experience: str
    posted: str
    description: str
    url: str
    query: str

    def to_dict(self):
        return asdict(self)


def _row_to_job(row: pd.Series, query: str) -> Job | None:
    """Map one JobSpy DataFrame row → our Job dataclass."""
    try:
        # url first — without it we can't form an id.
        raw_url = row.get("job_url")
        url = "" if raw_url is None or (isinstance(raw_url, float) and pd.isna(raw_url)) else str(raw_url).strip()
        if not url:
            return None
        # Stable per-posting id: hash of URL. JobSpy doesn't expose a clean
        # provider id across all sites, but job_url is unique per posting.
        job_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

        def _clean(v) -> str:
            """Coerce a pandas cell to a stripped string, treating NaN as ''."""
            if v is None:
                return ""
            try:
                if pd.isna(v):
                    return ""
            except (TypeError, ValueError):
                pass
            return str(v).strip()

        posted = _clean(row.get("date_posted"))
        description = _clean(row.get("description"))
        title = _clean(row.get("title"))
        company = _clean(row.get("company"))
        location = _clean(row.get("location"))
        experience = _clean(row.get("experience_range") or row.get("job_level"))

        # Salary: JobSpy normalises into min_amount/max_amount/currency/interval.
        sal_parts = []
        min_amt, max_amt = row.get("min_amount"), row.get("max_amount")
        min_ok = pd.notna(min_amt) if min_amt is not None else False
        max_ok = pd.notna(max_amt) if max_amt is not None else False
        if min_ok or max_ok:
            cur = _clean(row.get("currency")) or "INR"
            interval = _clean(row.get("interval")) or "yearly"
            if min_ok and max_ok:
                sal_parts.append(f"{cur} {int(min_amt):,}–{int(max_amt):,} / {interval}")
            elif min_ok:
                sal_parts.append(f"{cur} {int(min_amt):,}+ / {interval}")
            else:
                sal_parts.append(f"up to {cur} {int(max_amt):,} / {interval}")
        if sal_parts:
            salary_line = "💰 " + sal_parts[0]
            description = f"{salary_line}\n{description}" if description else salary_line

        # Source tag in description so users can see where it came from.
        site = (row.get("site") or "").strip()
        if site:
            description = f"[{site}] {description}" if description else f"[{site}]"

        return Job(
            job_id=job_id,
            title=title,
            company=company,
            location=location,
            experience=experience,
            posted=posted,
            description=description[:4000],  # keep DB row small
            url=url,
            query=query,
        )
    except Exception as e:
        log.debug("scraper: row→Job failed: %s", e)
        return None


def _scrape_one_query(query: str, experience_years: int) -> list[Job]:
    """Call JobSpy once for one query across all configured sites."""
    try:
        df = _jobspy_scrape(
            site_name=SITES,
            search_term=query,
            location="India",
            results_wanted=RESULTS_PER_QUERY,
            hours_old=HOURS_OLD,
            country_indeed="india",
            verbose=1,
        )
    except Exception as e:
        log.exception("scraper: jobspy raised q=%r: %s", query, e)
        return []

    if df is None or df.empty:
        log.warning("scraper: q=%r returned empty DataFrame", query)
        return []

    log.info("scraper: q=%r raw_rows=%d cols=%s",
             query, len(df), list(df.columns)[:8])

    jobs: list[Job] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        j = _row_to_job(row, query)
        if j and j.job_id not in seen:
            seen.add(j.job_id)
            jobs.append(j)
    log.info("scraper: q=%r parsed=%d unique=%d", query, len(df), len(jobs))
    return jobs


def scrape_all(queries: Iterable[str], experience_years: int,
               locations: list[str]) -> list[Job]:
    """For each query, call JobSpy across SITES and collect Job rows.

    `locations` is accepted for backward compatibility but ignored — JobSpy's
    location handling per site is unreliable, so we filter geographically at
    delivery time (app/filters.py already does this against the job's
    location string).

    `experience_years` is forwarded as best-effort to JobSpy, but most sites
    don't filter on it well; per-user experience filtering also happens in
    app/filters.py.
    """
    queries = list(queries)
    out: list[Job] = []
    for q in queries:
        got = _scrape_one_query(q, experience_years)
        out.extend(got)
        # Small pause between queries — Indeed is generous, LinkedIn isn't.
        time.sleep(2.0)

    # Dedup across queries by job_id (same posting can appear under multiple
    # search terms).
    seen: set[str] = set()
    deduped: list[Job] = []
    for j in out:
        if j.job_id in seen:
            continue
        seen.add(j.job_id)
        deduped.append(j)

    log.info("scraper: total_jobs=%d (deduped from %d) across %d queries",
             len(deduped), len(out), len(queries))
    return deduped
