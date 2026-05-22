"""Scrape Naukri using its internal JSON API.

Strategy: launch one Chromium session, navigate to a Naukri search page, and
capture the `nkparam` request header (and cookies) from the page's own outgoing
XHR to `/jobapi/v3/search`. Then hit that same endpoint directly with
`requests` for every query — fast, structured JSON, no per-query browser
navigation. Falls back to logging rich diagnostics if anything fails.

See https://github.com/Traverser25/NopeRi for the technique.
"""
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Iterable

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import stealth_sync

log = logging.getLogger("scraper")

API_URL = "https://www.naukri.com/jobapi/v3/search"
WARMUP_URL = "https://www.naukri.com/python-developer-jobs?experience=0&sortBy=date"

# Headers Naukri's own browser fetch sends with the XHR. `nkparam` and cookies
# are filled in at harvest time; the rest are stable.
_STATIC_HEADERS = {
    "appid": "109",
    "systemid": "Naukri",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.naukri.com/",
    "Origin": "https://www.naukri.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


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


def build_url(query: str, experience_years: int, locations: list[str]) -> str:
    """Build the public search-page URL — used for the harvest warm-up and as a
    fallback Job.url when the API row doesn't include one."""
    slug = query.strip().lower().replace(" ", "-")
    loc_slug = "-".join(l.strip().lower() for l in locations) if locations else ""
    path = f"{slug}-jobs"
    if loc_slug:
        path = f"{slug}-jobs-in-{loc_slug}"
    params = {"experience": str(experience_years), "sortBy": "date"}
    return f"https://www.naukri.com/{path}?{urllib.parse.urlencode(params)}"


def _extract_job_id(href: str) -> str:
    m = re.search(r"-(\d+)(?:\?|$)", href)
    return m.group(1) if m else href


def _harvest_credentials() -> tuple[dict[str, str], dict[str, str]] | None:
    """Open a real Chromium, navigate to a Naukri search page, and capture the
    `nkparam` header from the page's own XHR plus the cookie jar.

    Returns (headers, cookies) or None on failure. Headers always include
    `nkparam` if successful; cookies include the full set Naukri set during
    the page load (CSRF, session, etc.).
    """
    captured: dict[str, str] = {}
    # Track every /jobapi/* request we observe so we can debug when nkparam
    # isn't appearing (wrong endpoint? renamed header? no XHR at all?).
    seen_jobapi_urls: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        context = browser.new_context(
            user_agent=_STATIC_HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        stealth_sync(page)

        def _on_request(req):
            if "/jobapi/" not in req.url:
                return
            # Always log so we can SEE what the page is calling.
            hdr_keys = sorted(req.headers.keys())
            seen_jobapi_urls.append(req.url)
            log.info("scraper: jobapi request url=%s headers=%s",
                     req.url[:160], hdr_keys)
            if "nkparam" in req.headers and "nkparam" not in captured:
                captured["nkparam"] = req.headers["nkparam"]
                log.info("scraper: harvested nkparam (len=%d)",
                         len(captured["nkparam"]))

        page.on("request", _on_request)

        try:
            resp = page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp is not None else "n/a"
            log.info("scraper: warmup status=%s url=%s", status, WARMUP_URL)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass

            # The first SRP is server-rendered, so no /jobapi/v3/search XHR
            # fires on initial load. Force one by triggering an in-page
            # interaction that Naukri handles client-side via the API:
            # navigating to page 2 of results. The pageNo=2 variant of the
            # search URL always uses the JSON API for hydration.
            if "nkparam" not in captured:
                try:
                    page.goto(WARMUP_URL + "&pageNo=2",
                              wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                except Exception as e:
                    log.warning("scraper: pageNo=2 nav failed: %s", e)

            # Last-resort: scroll to trigger any lazy XHR.
            if "nkparam" not in captured:
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(700)
                except Exception:
                    pass

        except Exception as e:
            log.warning("scraper: warmup navigation failed: %s", e)

        cookie_list = context.cookies()
        if not seen_jobapi_urls:
            log.warning("scraper: NO /jobapi/* requests observed at all — "
                        "page may be fully SSR'd or blocked by JS")
        browser.close()

    if "nkparam" not in captured:
        log.warning("scraper: harvest finished without capturing nkparam — "
                    "Naukri may have changed the header name or blocked the page")
        return None

    cookies = {c["name"]: c["value"] for c in cookie_list
               if "naukri.com" in c.get("domain", "")}
    headers = {**_STATIC_HEADERS, "nkparam": captured["nkparam"]}
    log.info("scraper: harvest ok, cookies=%d", len(cookies))
    return headers, cookies


def _parse_api_row(row: dict, query: str) -> Job | None:
    """Map one JSON row from /jobapi/v3/search into our Job dataclass.

    The API field names have been stable for years but we treat all of them as
    optional — defensive against future shape changes."""
    try:
        job_id = str(row.get("jobId") or row.get("jobid") or "")
        if not job_id:
            return None
        title = (row.get("title") or "").strip()
        company = (row.get("companyName") or row.get("company") or "").strip()
        experience = (row.get("experienceText") or row.get("experience") or "").strip()
        # Locations can be a list of {label} dicts or a comma-joined string.
        loc_field = row.get("placeholders") or []
        location = ""
        for ph in loc_field if isinstance(loc_field, list) else []:
            if isinstance(ph, dict) and ph.get("type") == "location":
                location = ph.get("label", "").strip()
                break
        if not location:
            location = (row.get("location") or row.get("locationText") or "").strip()
        posted = (row.get("footerPlaceholderLabel") or row.get("createdDate")
                  or row.get("postedDate") or "").strip()
        description = (row.get("jobDescription") or row.get("description") or "").strip()
        # `jdURL` is the canonical absolute URL; fall back to title-slug if absent.
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
            description=description,
            url=url,
            query=query,
        )
    except Exception as e:
        log.debug("scraper: parse row failed: %s row_keys=%s", e, list(row.keys())[:10])
        return None


def _api_search(query: str, experience_years: int, headers: dict, cookies: dict,
                max_pages: int = 2) -> list[Job]:
    """Call /jobapi/v3/search for one query, paging up to `max_pages`."""
    jobs: list[Job] = []
    for page_no in range(1, max_pages + 1):
        params = {
            "noOfResults": 20,
            "urlType": "search_by_keyword",
            "searchType": "adv",
            "keyword": query,
            "pageNo": page_no,
            "experience": experience_years,
            "k": query,
            "seoKey": query.lower().replace(" ", "-") + "-jobs",
            "src": "jobsearchDesk",
            "latLong": "",
        }
        try:
            r = requests.get(API_URL, params=params, headers=headers,
                             cookies=cookies, timeout=20)
        except requests.RequestException as e:
            log.warning("scraper: api request failed q=%r page=%d err=%s",
                        query, page_no, e)
            break
        if r.status_code != 200:
            body_snip = (r.text or "")[:300].replace("\n", " ")
            log.warning("scraper: api non-200 q=%r page=%d status=%d body=%r",
                        query, page_no, r.status_code, body_snip)
            break
        try:
            data = r.json()
        except ValueError as e:
            log.warning("scraper: api non-json q=%r page=%d err=%s body=%r",
                        query, page_no, e, (r.text or "")[:200])
            break
        rows = data.get("jobDetails") or data.get("jobs") or []
        if not rows:
            log.info("scraper: api q=%r page=%d returned 0 rows (keys=%s)",
                     query, page_no, list(data.keys())[:10])
            break
        page_jobs = [j for j in (_parse_api_row(row, query) for row in rows) if j]
        jobs.extend(page_jobs)
        log.info("scraper: api q=%r page=%d rows=%d parsed=%d cumulative=%d",
                 query, page_no, len(rows), len(page_jobs), len(jobs))
        if len(rows) < 20:  # last page
            break
        time.sleep(0.8)
    return jobs


def scrape_all(queries: Iterable[str], experience_years: int,
               locations: list[str]) -> list[Job]:
    """Harvest fresh API credentials, then call the Naukri JSON search API for
    every query. Returns a flat list of Job rows.

    `locations` is currently ignored at the API layer — Naukri's API supports a
    city-id filter we don't yet resolve. Per-user location filtering already
    happens in `app.filters` against the pool.
    """
    queries = list(queries)
    out: list[Job] = []

    creds = _harvest_credentials()
    if not creds:
        log.error("scraper: no credentials harvested — returning 0 jobs. "
                  "Check Render logs above for warmup status/title.")
        return out
    headers, cookies = creds

    for q in queries:
        try:
            got = _api_search(q, experience_years, headers, cookies)
            log.info("scraper: q=%r total=%d", q, len(got))
            out.extend(got)
        except Exception as e:
            log.exception("scraper: q=%r failed: %s", q, e)
        time.sleep(0.5)

    log.info("scraper: total_jobs=%d across %d queries", len(out), len(queries))
    return out
