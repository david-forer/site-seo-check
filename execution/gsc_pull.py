"""Pull Search Console data: top pages and queries, current vs prior period, sitemap status."""
from __future__ import annotations

import datetime
import json

from common import load_config, load_env, google_session

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
API = "https://searchconsole.googleapis.com/webmasters/v3"


def _query(session, prop: str, start: str, end: str, dimension, limit: int = 100) -> list[dict]:
    dims = [dimension] if isinstance(dimension, str) else list(dimension)
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": dims,
        "rowLimit": limit,
    }
    r = session.post(f"{API}/sites/{prop.replace('/', '%2F').replace(':', '%3A')}/searchAnalytics/query", json=body, timeout=60)
    r.raise_for_status()
    rows = r.json().get("rows", [])
    out = []
    for row in rows:
        rec = {d: row["keys"][i] for i, d in enumerate(dims)}
        rec.update(
            {
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": round(row["ctr"], 4),
                "position": round(row["position"], 1),
            }
        )
        out.append(rec)
    return out


def collect(cfg: dict, env: dict[str, str]) -> dict:
    session = google_session(env, SCOPES)
    if session is None:
        return {"status": "skipped", "reason": "GOOGLE_APPLICATION_CREDENTIALS missing, see SETUP.md"}

    prop = cfg["gsc_property"]
    days = int(cfg.get("gsc_days", 28))
    end = datetime.date.today() - datetime.timedelta(days=3)  # GSC data lags ~2 days
    start = end - datetime.timedelta(days=days - 1)
    prior_end = start - datetime.timedelta(days=1)
    prior_start = prior_end - datetime.timedelta(days=days - 1)

    out: dict = {
        "status": "ok",
        "property": prop,
        "period": {"start": str(start), "end": str(end)},
        "prior_period": {"start": str(prior_start), "end": str(prior_end)},
    }
    out["pages"] = _query(session, prop, str(start), str(end), "page")
    out["pages_prior"] = _query(session, prop, str(prior_start), str(prior_end), "page")
    out["queries"] = _query(session, prop, str(start), str(end), "query", limit=200)
    out["queries_prior"] = _query(session, prop, str(prior_start), str(prior_end), "query", limit=200)
    # Paired page+query rows. The UI export cannot produce these, so without this
    # call every "which page owns this query" answer is inferred by topic match.
    out["page_queries"] = _query(session, prop, str(start), str(end), ["page", "query"], limit=1000)

    r = session.get(f"{API}/sites/{prop.replace('/', '%2F').replace(':', '%3A')}/sitemaps", timeout=30)
    out["sitemaps"] = r.json().get("sitemap", []) if r.ok else {"error": r.status_code}
    return out


if __name__ == "__main__":
    print(json.dumps(collect(load_config(), load_env()), indent=2))
