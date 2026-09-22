"""Find the small sites that rank for our seed terms, and separate them from the
large ones we cannot beat.

Two groups come out of one SERP set and conflating them wastes money:

  SERP competitors are whoever ranks. For davidjforer.com that includes RSM US,
  Salesforce, Microsoft, AWS and Forbes. Not beatable, not lookalikes, and their
  keyword sets are a list of terms this site cannot win.

  Peer competitors are small sites that rank anyway. What Google rewards them for
  is what it might reward a site this size for. Only these are worth mining in
  step 4.

Step 2 is serp_competitors: every domain ranking across the seed set, with how
many of the seeds it covers and its average position. Step 3 is
domain_rank_overview on the survivors, which gives the authority number that
splits the two groups.

Cost: serp_competitors is one call for the whole seed list. domain_rank_overview
is one call per domain, batched 100 at a time. Both are fractions of a cent.
The script prints its own spend estimate before it calls anything.

Credentials: DATAFORSEO_API_KEY, same resolution order as aio_check.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aio_check import _credential
from common import ROOT, load_config, load_env

LABS = "https://api.dataforseo.com/v3/dataforseo_labs/google"

# Domains that rank for these terms but are not in the business this site is in.
# Kept as a named list rather than an authority cutoff, because a low-authority
# job board or forum is still not a peer consultancy.
NOT_A_PEER = {
    "linkedin.com", "youtube.com", "reddit.com", "amazon.com", "facebook.com",
    "x.com", "twitter.com", "medium.com", "substack.com", "quora.com",
    "indeed.com", "glassdoor.com", "coursera.org", "udemy.com", "wikipedia.org",
    "forbes.com", "hbr.org", "inc.com", "entrepreneur.com", "techtarget.com",
    "gartner.com", "mckinsey.com", "deloitte.com", "pwc.com", "kpmg.com",
    "ey.com", "accenture.com", "ibm.com", "microsoft.com", "google.com",
    "salesforce.com", "oracle.com", "sap.com", "aws.amazon.com", "adobe.com",
    "hubspot.com", "zapier.com", "atlassian.com", "notion.so", "slack.com",
    "clickup.com", "miro.com", "monday.com", "asana.com", "smartsheet.com",
    "uipath.com", "automationanywhere.com", "make.com", "n8n.io", "airtable.com",
}


def _post(token: str, path: str, body: list) -> dict:
    import requests

    r = requests.post(
        f"{LABS}{path}",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    r.raise_for_status()
    payload = r.json()
    task = (payload.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise RuntimeError(f"task {task.get('status_code')}: {task.get('status_message')}")
    return task


def serp_competitors(token: str, seeds: list[str], loc: int, lang: str, limit: int) -> list[dict]:
    task = _post(token, "/serp_competitors/live", [{
        "keywords": seeds, "location_code": loc, "language_code": lang, "limit": limit,
    }])
    return (task.get("result") or [{}])[0].get("items") or []


def domain_overview(token: str, domains: list[str], loc: int, lang: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in domains:
        try:
            task = _post(token, "/domain_rank_overview/live", [{
                "target": d, "location_code": loc, "language_code": lang,
            }])
            items = (task.get("result") or [{}])[0].get("items") or []
            m = (items[0].get("metrics", {}) if items else {}).get("organic", {}) or {}
            out[d] = {
                "organic_keywords": m.get("count"),
                "organic_etv": round(m.get("etv") or 0, 1),
                "pos_1_3": (m.get("pos_1") or 0) + (m.get("pos_2_3") or 0),
            }
        except Exception as e:
            out[d] = {"error": f"{type(e).__name__}: {e}"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=Path, default=ROOT / "state" / "seeds.json")
    ap.add_argument("--limit", type=int, default=100, help="domains to return from step 2")
    ap.add_argument("--overview-top", type=int, default=40, help="how many domains get a step 3 call")
    ap.add_argument("--out", type=Path, default=ROOT / "state" / "peers.json")
    a = ap.parse_args()

    cfg, env = load_config(), load_env()
    token = _credential(env, cfg)
    if token is None:
        sys.exit("No DATAFORSEO_API_KEY. See SETUP.md step 5.")
    seeds = json.loads(a.seeds.read_text(encoding="utf-8"))
    loc, lang = int(cfg.get("aio_location", 2840)), cfg.get("aio_language", "en")

    print(f"Step 2: serp_competitors across {len(seeds)} seeds, 1 call.")
    items = serp_competitors(token, seeds, loc, lang, a.limit)
    print(f"  {len(items)} domains rank for at least one seed.")

    rows = []
    for it in items:
        d = (it.get("domain") or "").lower().removeprefix("www.")
        if not d:
            continue
        m = it.get("avg_position")
        rows.append({
            "domain": d,
            "seeds_covered": it.get("keywords_count") or it.get("visibility") or 0,
            "avg_position": round(m, 1) if isinstance(m, (int, float)) else None,
            "etv": round(it.get("etv") or 0, 1),
            "excluded": d in NOT_A_PEER,
        })
    rows.sort(key=lambda r: -(r["seeds_covered"] or 0))

    peers = [r for r in rows if not r["excluded"]][: a.overview_top]
    print(f"Step 3: domain_rank_overview on {len(peers)} candidate peers, {len(peers)} calls.")
    ov = domain_overview(token, [r["domain"] for r in peers], loc, lang)
    for r in peers:
        r.update(ov.get(r["domain"], {}))

    out = {
        "seeds": seeds,
        "calls": 1 + len(peers),
        "estimated_cost_usd": round(0.02 + len(peers) * 0.011, 3),
        "all_domains": rows,
        "peers": peers,
    }
    a.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {a.out}. Estimated spend ${out['estimated_cost_usd']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
