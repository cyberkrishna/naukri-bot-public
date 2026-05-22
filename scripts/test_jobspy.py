"""Smoke-test python-jobspy's Naukri support from this machine.

Run from the repo root:
    .\\.venv\\Scripts\\Activate.ps1
    pip install python-jobspy
    python scripts\\test_jobspy.py

What it does:
- Calls jobspy.scrape_jobs(site_name=['naukri'], ...) for 3 sample queries.
- Prints how many rows came back and a few fields per query.
- Saves the full result to scripts/jobspy_out.csv for inspection.

What to look for:
- jobs_found > 0 for each query → JobSpy works on your IP, ready to deploy.
- All zero or exceptions → either Naukri changed shape, or your IP is also
  blocked (the latter would be surprising — residential IPs rarely are).
"""
from __future__ import annotations

import sys
import traceback

try:
    from jobspy import scrape_jobs
except ImportError:
    print("ERROR: python-jobspy not installed.")
    print("Run: pip install python-jobspy")
    sys.exit(1)

QUERIES = [
    "python developer",
    "data analyst",
    "software engineer fresher",
]

# Columns we care about for our bot's Job model.
SHOW_COLS = [
    "title", "company", "location", "job_url",
    "experience_range", "skills", "date_posted",
]


def main() -> None:
    all_rows = []
    for q in QUERIES:
        print(f"\n=== query: {q!r} ===")
        try:
            df = scrape_jobs(
                site_name=["naukri"],
                search_term=q,
                results_wanted=20,
                hours_old=72,
                country_indeed="india",  # required arg even when not scraping indeed
                verbose=2,
            )
        except Exception as e:
            print(f"  scrape_jobs raised: {type(e).__name__}: {e}")
            traceback.print_exc()
            continue

        n = 0 if df is None else len(df)
        print(f"  rows returned: {n}")
        if n == 0:
            continue

        # Show first 3 rows of the columns we care about.
        cols_present = [c for c in SHOW_COLS if c in df.columns]
        if cols_present:
            print(df[cols_present].head(3).to_string(index=False))
        else:
            print(f"  (none of {SHOW_COLS} present; available: {list(df.columns)[:15]})")
        df["_query"] = q
        all_rows.append(df)

    if not all_rows:
        print("\n=== summary ===")
        print("No rows returned for ANY query. JobSpy is likely broken for Naukri "
              "on this machine, OR your IP is captcha-gated too.")
        return

    # Concat and save.
    import pandas as pd
    combined = pd.concat(all_rows, ignore_index=True)
    out = "scripts/jobspy_out.csv"
    combined.to_csv(out, index=False)
    print(f"\n=== summary ===")
    print(f"total_rows: {len(combined)}")
    print(f"queries_with_results: {len(all_rows)}/{len(QUERIES)}")
    print(f"unique companies: {combined['company'].nunique() if 'company' in combined.columns else 'n/a'}")
    print(f"saved full output to {out}")


if __name__ == "__main__":
    main()
