"""Pull GA4 data: sessions and engagement by channel and by page, current vs prior period."""
from __future__ import annotations

import datetime
import json

from common import load_config, load_env, google_session

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
API = "https://analyticsdata.googleapis.com/v1beta"


def _run_report(session, prop_id: str, dimension: str, start: str, end: str, limit: int = 25) -> list[dict]:
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": dimension}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "engagedSessions"},
            {"name": "keyEvents"},
        ],
        "limit": limit,
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    r = session.post(f"{API}/properties/{prop_id}:runReport", json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    rows = []
    for row in data.get("rows", []):
        vals = [v["value"] for v in row["metricValues"]]
        rows.append(
            {
                dimension: row["dimensionValues"][0]["value"],
                "sessions": int(vals[0]),
                "users": int(vals[1]),
                "engaged_sessions": int(vals[2]),
                "key_events": float(vals[3]),
            }
        )
    return rows


def collect(cfg: dict, env: dict[str, str]) -> dict:
    prop_id = env.get("GA4_PROPERTY_ID", "").strip()
    session = google_session(env, SCOPES)
    if session is None or not prop_id:
        return {"status": "skipped", "reason": "credentials or GA4_PROPERTY_ID missing, see SETUP.md"}

    days = int(cfg.get("gsc_days", 28))
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    prior_end = start - datetime.timedelta(days=1)
    prior_start = prior_end - datetime.timedelta(days=days - 1)

    return {
        "status": "ok",
        "property_id": prop_id,
        "period": {"start": str(start), "end": str(end)},
        "prior_period": {"start": str(prior_start), "end": str(prior_end)},
        "channels": _run_report(session, prop_id, "sessionDefaultChannelGroup", str(start), str(end)),
        "channels_prior": _run_report(session, prop_id, "sessionDefaultChannelGroup", str(prior_start), str(prior_end)),
        "pages": _run_report(session, prop_id, "pagePath", str(start), str(end), limit=30),
        "pages_prior": _run_report(session, prop_id, "pagePath", str(prior_start), str(prior_end), limit=30),
    }


if __name__ == "__main__":
    print(json.dumps(collect(load_config(), load_env()), indent=2))
