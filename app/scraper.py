"""Scrape Naukri search result pages.

Naukri runs on Next.js 13+ App Router (RSC). The SRP HTML loads fine from
Render's IP (warmup returns 200), but:
  - The legacy `<script id="__NEXT_DATA__">` blob no longer exists.
  - The JSON API (`/jobapi/v3/search`) returns 406 "recaptcha required".

So the only viable path from a cloud IP is to render the SRP with Playwright
and read job data out of the rendered DOM after React hydrates. We do this
once per query × page, reusing one Chromium session across queries.

We try two extraction paths, in order:
  1. Parse the Flight RSC payload from `<script>self.__next_f.push([...])</script>`
     chunks. Cleanest — gives us the same JSON the React tree consumes.
  2. Wait for the job-card selector to appear (proves hydration completed),
     then read fields directly from the DOM. Resilient to Flight payload
     restructuring.
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

# Matches each Flight payload chunk Next.js streams into the page.
# The arg is a JSON-encoded string (with escaped quotes) we then re-parse.
_FLIGHT_RE = re.compile(
    r'self\.__next_f\.push\((\[[^\)]+\])\)',
    re.DOTALL,
)

# Job card selectors Naukri's SRP renders post-hydration. Multiple variants
# so we don't break on a minor class rename.
_CARD_SELECTOR = ("div.srp-jobtuple-wrapper, article.jobTuple, "
                  "div[data-job-id], div.jobTupleHeader")


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


def _extract_job_id(href: str) -> str:
    m = re.search(r"-(\d+)(?:\?|$)", href)
    return m.group(1) if m else href


# --- Path 1: Flight RSC payload --------------------------------------------

def _walk_for_job_rows(obj) -> list[dict]:
    """Walk a JSON tree, returning every list of Naukri job rows we find.

    A Naukri job row reliably has both `jobId` and `title` keys.
    """
    found: list[dict] = []

    def _is_row(d) -> bool:
        return (isinstance(d, dict)
                and ("jobId" in d or "jobid" in d)
                and "title" in d)

    def _walk(node):
        if isinstance(node, list):
            if node and _is_row(node[0]):
                found.extend(x for x in node if _is_row(x))
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)

    _walk(obj)
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in found:
        jid = str(r.get("jobId") or r.get("jobid") or "")
        if jid and jid not in seen:
            seen.add(jid)
            uniq.append(r)
    return uniq


def _parse_flight_row(row: dict, query: str) -> Job | None:
    try:
        job_id = str(row.get("jobId") or row.get("jobid") or "")
        if not job_id:
            return None
        title = (row.get("title") or "").strip()
        company = (row.get("companyName") or row.get("company") or "").strip()
        experience = (row.get("experienceText") or row.get("experience") or "").strip()

        location = ""
        salary = ""
        ph = row.get("placeholders") or []
        if isinstance(ph, list):
            for p in ph:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "location" and not location:
                    location = (p.get("label") or "").strip()
                elif p.get("type") == "salary" and not salary:
                    salary = (p.get("label") or "").strip()
        if not location:
            location = (row.get("location") or row.get("locationText") or "").strip()

        posted = (row.get("footerPlaceholderLabel") or row.get("createdDate")
                  or row.get("postedDate") or "").strip()
        desc = (row.get("jobDescription") or row.get("description") or "").strip()
        if salary:
            desc = f"💰 {salary}\n{desc}" if desc else f"💰 {salary}"

        url = row.get("jdURL") or row.get("jdUrl") or row.get("url") or ""
        if url and not url.startswith("http"):
            url = f"https://www.naukri.com{url}"

        return Job(job_id=job_id, title=title, company=company, location=location,
                   experience=experience, posted=posted, description=desc,
                   url=url, query=query)
    except Exception as e:
        log.debug("scraper: flight row parse failed: %s", e)
        return None


def _extract_from_flight(html: str, query: str) -> list[Job]:
    """Parse all `self.__next_f.push([...])` chunks, search the merged JSON
    tree for job rows."""
    chunks = _FLIGHT_RE.findall(html)
    if not chunks:
        return []

    payloads: list = []
    for raw in chunks:
        try:
            outer = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Each push is [tag, "json-string"] — the second element is the
        # actual Flight payload as a string.
        if not isinstance(outer, list) or len(outer) < 2:
            continue
        inner = outer[1]
        if not isinstance(inner, str):
            continue
        # The Flight payload is line-delimited; each non-empty line is either
        # a model reference (e.g. `0:"$Sreact.suspense"`) or
        # `<index>:<JSON>`. We try every JSON-looking suffix.
        for line in inner.splitlines():
            colon = line.find(":")
            if colon < 0:
                continue
            tail = line[colon + 1:]
            if not tail or tail[0] not in "[{":
                continue
            try:
                payloads.append(json.loads(tail))
            except json.JSONDecodeError:
                continue

    rows: list[dict] = []
    for p in payloads:
        rows.extend(_walk_for_job_rows(p))
    if not rows:
        return []

    # Dedup once more across payloads.
    seen: set[str] = set()
    jobs: list[Job] = []
    for r in rows:
        jid = str(r.get("jobId") or r.get("jobid") or "")
        if not jid or jid in seen:
            continue
        seen.add(jid)
        j = _parse_flight_row(r, query)
        if j:
            jobs.append(j)
    return jobs


# --- Path 2: DOM after hydration -------------------------------------------

def _extract_from_dom(page, query: str) -> list[Job]:
    """Read job rows from the rendered DOM. Used when Flight parsing yields
    nothing — slower but resilient to payload-shape changes."""
    cards = page.query_selector_all(_CARD_SELECTOR)
    jobs: list[Job] = []
    for c in cards:
        try:
            link_el = c.query_selector("a.title, a.title-link, a[href*='-jobs-']")
            if not link_el:
                continue
            href = link_el.get_attribute("href") or ""
            title = (link_el.inner_text() or "").strip()
            company_el = c.query_selector("a.comp-name, a.subTitle, span.comp-name")
            company = (company_el.inner_text().strip() if company_el else "")
            exp_el = c.query_selector("span.exp-wrap, span.expwdth, li.experience, span.exp")
            experience = (exp_el.inner_text().strip() if exp_el else "")
            loc_el = c.query_selector("span.loc-wrap, span.locWdth, li.location, span.loc")
            location = (loc_el.inner_text().strip() if loc_el else "")
            desc_el = c.query_selector("span.job-desc, span.job-description")
            description = (desc_el.inner_text().strip() if desc_el else "")
            sal_el = c.query_selector("span.sal-wrap, span.salWdth, span.sal, li.salary")
            salary = (sal_el.inner_text().strip() if sal_el else "")
            if salary:
                description = f"💰 {salary}\n{description}" if description else f"💰 {salary}"
            posted_el = c.query_selector("span.job-post-day, span.fleft.postedDate, span.post-day")
            posted = (posted_el.inner_text().strip() if posted_el else "")

            jobs.append(Job(
                job_id=_extract_job_id(href),
                title=title, company=company, location=location,
                experience=experience, posted=posted, description=description,
                url=href if href.startswith("http") else f"https://www.naukri.com{href}",
                query=query,
            ))
        except Exception as e:
            log.debug("scraper: DOM card parse failed q=%r: %s", query, e)
    return jobs


# --- Driver ----------------------------------------------------------------

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

    # Wait for hydration: either the job-card selector appears in the DOM,
    # or networkidle fires (Flight chunks all streamed in).
    hydrated = False
    try:
        page.wait_for_selector(_CARD_SELECTOR, timeout=15000)
        hydrated = True
    except PWTimeout:
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass

    html = page.content() or ""

    # Try Flight RSC payload first.
    jobs = _extract_from_flight(html, query)
    if jobs:
        log.info("scraper: q=%r flight_jobs=%d (hydrated=%s)",
                 query, len(jobs), hydrated)
        return jobs

    # Fall back to DOM. If hydration never completed, this will be empty.
    jobs = _extract_from_dom(page, query)
    if jobs:
        log.info("scraper: q=%r dom_jobs=%d (hydrated=%s, flight_empty)",
                 query, len(jobs), hydrated)
        return jobs

    # Neither path worked — emit one diagnostic line so we can iterate.
    title = ""
    try:
        title = page.title() or ""
    except Exception:
        pass
    flight_chunks = len(_FLIGHT_RE.findall(html))
    log.warning("scraper: q=%r ZERO jobs hydrated=%s flight_chunks=%d title=%r "
                "html_len=%d", query, hydrated, flight_chunks, title[:120], len(html))
    return []


def scrape_all(queries: Iterable[str], experience_years: int,
               locations: list[str], max_pages: int = 2) -> list[Job]:
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
                    break
                out.extend(got)
                time.sleep(1.2)
            time.sleep(0.6)

        browser.close()

    log.info("scraper: total_jobs=%d across %d queries", len(out), len(queries))
    return out
