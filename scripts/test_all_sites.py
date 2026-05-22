"""Smoke-test every JobSpy-supported board for our use case (fresher / entry
roles in India). Prints rows-per-site for one query and saves CSV per site.

Run:
    .\\.venv\\Scripts\\Activate.ps1
    python scripts\\test_all_sites.py

JobSpy supports: linkedin, indeed, glassdoor, google, zip_recruiter, bayt,
naukri, bdjobs. We skip zip_recruiter (US/Canada only) and naukri (just
proved it's 406'd). We try the rest.
"""
from __future__ import annotations

import traceback

from jobspy import scrape_jobs

SITES = ["indeed", "linkedin", "glassdoor", "google", "bayt", "bdjobs"]
QUERY = "python developer fresher"
LOCATION = "India"

SHOW_COLS = ["title", "company", "location", "job_url", "date_posted"]


def main() -> None:
    summary = {}
    for site in SITES:
        print(f"\n=== site: {site} ===")
        kwargs = dict(
            site_name=[site],
            search_term=QUERY,
            location=LOCATION,
            results_wanted=15,
            hours_old=72,
            country_indeed="india",
            verbose=1,
        )
        if site == "google":
            kwargs["google_search_term"] = f"{QUERY} jobs in India since yesterday"
        try:
            df = scrape_jobs(**kwargs)
        except Exception as e:
            print(f"  raised: {type(e).__name__}: {e}")
            traceback.print_exc()
            summary[site] = f"ERROR: {type(e).__name__}"
            continue

        n = 0 if df is None else len(df)
        summary[site] = n
        print(f"  rows: {n}")
        if n > 0:
            cols = [c for c in SHOW_COLS if c in df.columns]
            if cols:
                print(df[cols].head(3).to_string(index=False))
            df.to_csv(f"scripts/jobspy_{site}.csv", index=False)

    print("\n=== summary ===")
    for site, n in summary.items():
        print(f"  {site:12s} -> {n}")


if __name__ == "__main__":
    main()
