"""Write the last 90 days of Search Console queries to inbox/ as a Queries CSV, via the API.

Produces the same columns as the UI export so gsc_inbox.py can process it unchanged.
"""
from __future__ import annotations

import csv
import datetime
import sys
from pathlib import Path

from common import load_config, load_env, google_session
from gsc_pull import _query, SCOPES

INBOX = Path(__file__).resolve().parent.parent / "inbox"


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    session = google_session(load_env(), SCOPES)
    if session is None:
        print("GOOGLE_APPLICATION_CREDENTIALS missing, see SETUP.md")
        sys.exit(1)
    end = datetime.date.today() - datetime.timedelta(days=3)  # GSC data lags ~2 days
    start = end - datetime.timedelta(days=days - 1)
    rows = _query(session, load_config()["gsc_property"], str(start), str(end), "query", limit=5000)
    out = INBOX / f"Queries-api-{datetime.date.today()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Top queries", "Clicks", "Impressions", "CTR", "Position"])
        for r in rows:
            w.writerow([r["query"], r["clicks"], r["impressions"], r["ctr"], r["position"]])
    print(f"{len(rows)} queries, {start} to {end}, written to {out}")


if __name__ == "__main__":
    main()
