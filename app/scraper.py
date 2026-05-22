"""Scrape Naukri by extracting the SSR'd __NEXT_DATA__ JSON from search pages.

Naukri is a Next.js app — every search results page embeds the full job
listing as JSON inside `<script id="__NEXT_DATA__">`. We load each query's
SRP with Playwright (one Chromium session reused across queries) and parse
the JSON straight out of the HTML.

Why this beats every other path:
- No /jobapi/v3/search call → no `recaptcha required` 406s (which is what
  the JSON API returns from Render's IP).
- No `nkparam` token to harvest, no header signing to reverse-engineer.
- No fragile CSS selectors — the JSON shape is what Next.js ships to its
  own client-side hydration, far more stable than rendered DOM classes.
- Cookies + stealth make the HTML page itself reachable; that's the only
  thing we need.
"""
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Iterable

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import stealth_sync

log = logging.getLogger("scraper")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Matches the Next.js data script tag. Naukri uses the standard Next layout.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL,
)


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


def build_url(query: str, experience_years: int, locations: list[str],
              page_no: int = 1) -> str:
    slug = query.strip().lower().replace(" ", "-")
    loc_slug = "-".join(l.strip().lower() for l in locations) if locations else ""
    path = f"{slug}-jobs"
    if loc_slug:
        path = f"{slug}-jobs-in-{loc_slug}"
    params = {"experience": str(experience_years), "sortBy": "date"}
    url = f"https://www.naukri.com/{path}?{urllib.parse.urlencode(params)}"
    if page_no > 1:
        url += f"&pageNo={page_no}"
    return url


def _walk_for_job_rows(obj) -> list[dict]:
    """Recursively walk a JSON tree looking for the Naukri jobs array.

    Naukri's __NEXT_DATA__ shape is roughly:
      props.pageProps.jobDetails / .srpResultDetails.jobDetails / etc.
    Rather than hardcode the path (it has shifted across rewrites), we
    walk the tree and pick the first list whose items look like Naukri job
    rows. A Naukri job row reliably has a `jobId` key.
    """
    found: list[dict] = []

    def _is_job_row(d) -> bool:
        if not isinstance(d, dict):
            return False
        # jobId / title / companyName are the stable trio.
        has_id = any(k in d for k in ("jobId", "jobid"))
        has_title = "title" in d
        return has_id and has_title

    def _walk(node):
        if isinstance(node, list):
            if node and _is_job_row(node[0]):
                found.extend(item for item in node if _is_job_row(item))
                # Don't return — there may be multiple lists (e.g. ads).
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)

    _walk(obj)
    # Dedup by jobId in case multiple list paths overlap.
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in found:
        jid = str(r.get("jobId") or r.get("jobid") or "")
        if jid and jid not in seen:
            seen.add(jid)
            uniq.append(r)
    return uniq


def _parse_row(row: dict, query: str) -> Job | None:
    try:
        job_id = str(row.get("jobId") or row.get("jobid") or "")
        if not job_id:
            return None
        title = (row.get("title") or "").strip()
        company = (row.get("companyName") or row.get("company") or "").strip()
        experience = (row.get("experienceText") or row.get("experience") or "").strip()

        # Location: prefer placeholders[type=location].label; fall back to flat fields.
        location = ""
        ph = row.get("placeholders") or []
        if isinstance(ph, list):
            for p in ph:
                if isinstance(p, dict) and p.get("type") == "location":
                    location = (p.get("label") or "").strip()
                    break
        if not location:
            location = (row.get("location") or row.get("locationText") or "").strip()

        # Salary placeholder (helpful free signal; we don't model it yet but
        # show it in description so users can see).
        salary = ""
        if isinstance(ph, list):
            for p in ph:
                if isinstance(p, dict) and p.get("type") == "salary":
                    salary = (p.get("label") or "").strip()
                    break

        posted = (row.get("footerPlaceholderLabel") or row.get("createdDate")
                  or row.get("postedDate") or "").strip()
        desc = (row.get("jobDescription") or row.get("description") or "").strip()
        if salary:
            desc = f"💰 {salary}\n{desc}" if desc else f"💰 {salary}"

        url = row.get("jdURL") or row.get("jdUrl") or row.get("url") or ""
        if url and not url.startswith("http"):
            url = f"https://www.naukri.com{url}"

        return Job(
            job_id=job_id,
            title=title,
            company=company,
            location=location,
            experience=experience,
            posted=posted,
            description=desc,
            url=url,
            query=query,
        )
    except Exception as e:
        log.debug("scraper: parse row failed: %s", e)
        return None


def _scrape_one_page(page, url: str, query: str) -> list[Job]:
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp is not None else "n/a"
    except PWTimeout as e:
        log.warning("scraper: goto timeout q=%r url=%s err=%s", query, url, e)
        return []
    except Exception as e:
        log.warning("scraper: goto failed q=%r url=%s err=%s", query, url, e)
        return []

    if status != 200:
        log.warning("scraper: SRP non-200 q=%r status=%s url=%s", query, status, url)
        return []

    html = page.content() or ""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        # Either Next.js layout changed, or page is a captcha / interstitial.
        title = ""
        try:
            title = page.title() or ""
        except Exception:
            pass
        log.warning("scraper: __NEXT_DATA__ not found q=%r status=%s title=%r "
                    "html_head=%r", query, status, title[:120],
                    html[:300].replace("\n", " "))
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        log.warning("scraper: __NEXT_DATA__ JSON parse failed q=%r err=%s", query, e)
        return []

    rows = _walk_for_job_rows(data)
    if not rows:
        # Log a hint of the JSON shape so we can map a new path if Naukri
        # restructures.
        top_keys = list(data.get("props", {}).get("pageProps", {}).keys())[:10] \
            if isinstance(data.get("props"), dict) else []
        log.warning("scraper: __NEXT_DATA__ parsed but no job rows found "
                    "q=%r pageProps_keys=%s", query, top_keys)
        return []

    jobs = [j for j in (_parse_row(r, query) for r in rows) if j]
    log.info("scraper: q=%r rows=%d parsed=%d", query, len(rows), len(jobs))
    return jobs


def scrape_all(queries: Iterable[str], experience_years: int,
               locations: list[str], max_pages: int = 2) -> list[Job]:
    """For each query, load the SRP and extract jobs from the SSR'd JSON.

    One Chromium session is reused across all queries — much lighter than
    one navigation per query in a fresh browser.
    """
    queries = list(queries)
    out: list[Job] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        context = browser.new_context(
            user_agent=_UA,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        stealth_sync(page)

        for q in queries:
            for pn in range(1, max_pages + 1):
                url = build_url(q, experience_years, locations, page_no=pn)
                try:
                    got = _scrape_one_page(page, url, q)
                except Exception as e:
                    log.exception("scraper: q=%r page=%d failed: %s", q, pn, e)
                    got = []
                if not got:
                    break  # No point loading page 2 if page 1 was empty.
                out.extend(got)
                time.sleep(1.2)
            time.sleep(0.6)

        browser.close()

    log.info("scraper: total_jobs=%d across %d queries", len(out), len(queries))
    return out
