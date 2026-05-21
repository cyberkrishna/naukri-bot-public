import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Iterable

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import stealth_sync


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


def scrape_query(page, query: str, experience_years: int, locations: list[str], max_pages: int = 2) -> list[Job]:
    jobs: list[Job] = []
    base = build_url(query, experience_years, locations)
    for page_num in range(1, max_pages + 1):
        url = base + (f"&pageNo={page_num}" if page_num > 1 else "")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("div.srp-jobtuple-wrapper, article.jobTuple", timeout=15000)
        except PWTimeout:
            break

        cards = page.query_selector_all("div.srp-jobtuple-wrapper, article.jobTuple")
        if not cards:
            break

        for c in cards:
            try:
                link_el = c.query_selector("a.title, a.title-link")
                if not link_el:
                    continue
                href = link_el.get_attribute("href") or ""
                title = (link_el.inner_text() or "").strip()
                company_el = c.query_selector("a.comp-name, a.subTitle")
                company = (company_el.inner_text().strip() if company_el else "").strip()
                exp_el = c.query_selector("span.exp-wrap, span.expwdth, li.experience")
                experience = (exp_el.inner_text().strip() if exp_el else "")
                loc_el = c.query_selector("span.loc-wrap, span.locWdth, li.location")
                location = (loc_el.inner_text().strip() if loc_el else "")
                desc_el = c.query_selector("span.job-desc, span.job-description")
                description = (desc_el.inner_text().strip() if desc_el else "")
                posted_el = c.query_selector("span.job-post-day, span.fleft.postedDate")
                posted = (posted_el.inner_text().strip() if posted_el else "")

                jobs.append(Job(
                    job_id=_extract_job_id(href),
                    title=title,
                    company=company,
                    location=location,
                    experience=experience,
                    posted=posted,
                    description=description,
                    url=href if href.startswith("http") else f"https://www.naukri.com{href}",
                    query=query,
                ))
            except Exception:
                continue
        time.sleep(1.5)
    return jobs


def scrape_all(queries: Iterable[str], experience_years: int, locations: list[str]) -> list[Job]:
    out: list[Job] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        stealth_sync(page)
        for q in queries:
            try:
                out.extend(scrape_query(page, q, experience_years, locations))
            except Exception as e:
                print(f"[scrape] {q!r} failed: {e}")
        browser.close()
    return out
