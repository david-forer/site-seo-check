"""Check which of the site's top queries carry a Google AI Overview, whether the
site is cited in it, and what vocabulary Google treats as adjacent to the query.

Why this exists: Search Console reports position and impressions and says nothing
about what sits above the organic results. On 2026-09-22 the two highest-impression
commercial queries both carried a full AI Overview citing seven and eleven
competitor domains, with davidjforer.com in neither. Under a block that size,
position is a poor proxy for opportunity, so the weekly report needs to separate
"we rank badly" from "the click was taken before the organic list started".

The related searches, People Also Ask questions and top organic URLs all arrive in
the same response as the AI Overview. Reading them costs nothing extra, and they
answer a question Search Console cannot: GSC only reports queries the site already
surfaces for, so a page at position 38 shows a handful of terms while the pages
above it surface for hundreds. These blocks are Google stating its own topical
neighbourhood for the query, which is better evidence than guessing at synonyms.

Cost: DataForSEO v3/serp/google/organic/live/advanced, roughly $0.002 per query.
Default cap is 10 queries per run, about two cents a week. The cap is a hard stop,
not a suggestion: a runaway loop here spends real money.

Credentials: DATAFORSEO_API_KEY from this project's .env, then from the file named
by SHARED_ENV_FILE in .env if one is set. Never printed or written out.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from common import ROOT, load_config, load_env, shared_env_file

ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"


def _credential(env: dict[str, str], cfg: dict | None = None) -> str | None:
    """Basic auth token. Accepts login:password or a pre-encoded base64 string,
    because DATAFORSEO_API_KEY is used for both in the wild."""
    raw = (env.get("DATAFORSEO_API_KEY") or "").strip()
    shared = shared_env_file(env)
    if not raw and shared is not None:
        for line in shared.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATAFORSEO_API_KEY") and "=" in line:
                raw = line.split("=", 1)[1].strip()
                break
    if not raw:
        return None
    return base64.b64encode(raw.encode()).decode() if ":" in raw else raw


def _pick_queries(gsc: dict, limit: int, min_impressions: int, brand: str = "") -> list[str]:
    """Top queries by impressions from this run's GSC pull. Brand and site:
    operators are dropped, they never carry a commercial AI Overview.

    `brand` is the domain's own name, derived from site_url by the caller, so a
    navigational search for the site itself is not paid for."""
    rows = gsc.get("queries") or []
    out = []
    for r in sorted(rows, key=lambda x: -x.get("impressions", 0)):
        q = (r.get("query") or "").strip()
        if not q or r.get("impressions", 0) < min_impressions:
            continue
        if q.startswith("site:"):
            continue
        if brand and brand in q.replace(" ", "").lower():
            continue
        out.append(q)
        if len(out) >= limit:
            break
    return out


def _aio_from_items(items: list, host: str) -> dict:
    """Pull the AI Overview block out of a SERP item list and read its citations."""
    found = {"ai_overview": False, "cited": None, "citations": [],
             "citations_available": False, "organic_rank": None,
             "related_searches": [], "paa": [], "top_organic": []}
    for item in items or []:
        t = item.get("type")
        if t == "related_searches":
            found["related_searches"] = [x for x in (item.get("items") or []) if isinstance(x, str)]
        elif t == "people_also_ask":
            found["paa"] = [
                q.get("title") for q in (item.get("items") or []) if q.get("title")
            ]
        if t == "ai_overview":
            found["ai_overview"] = True
            refs = list(item.get("references") or [])
            # Some payloads nest the references one level down inside items.
            if not refs:
                for sub in item.get("items") or []:
                    refs.extend(sub.get("references") or [])
            seen = []
            for ref in refs:
                d = (ref.get("domain") or "").lower().removeprefix("www.")
                if d and d not in seen:
                    seen.append(d)
            found["citations"] = seen
            # An asynchronous AI Overview comes back as a stub with no references.
            # Absent citations mean "not measured", never "not cited". Reporting
            # unknown as false would overstate how badly the site is doing.
            if seen:
                found["citations_available"] = True
                found["cited"] = any(host in d or d in host for d in seen)
            else:
                found["async_stub"] = bool(item.get("asynchronous_ai_overview"))
        elif t == "organic":
            u = item.get("url") or ""
            if found["organic_rank"] is None and host in urlparse(u).netloc.lower():
                found["organic_rank"] = item.get("rank_absolute")
            # Keep the top of the organic list so serp_gap.py can run its free
            # local term analysis against real competitors without paying twice.
            if len(found["top_organic"]) < 10 and u:
                found["top_organic"].append(u)
    return found


def collect(cfg: dict, env: dict[str, str], gsc: dict | None = None) -> dict:
    token = _credential(env, cfg)
    if token is None:
        return {"status": "skipped", "reason": "DATAFORSEO_API_KEY missing, see SETUP.md"}
    if not cfg.get("aio_enabled", True):
        return {"status": "skipped", "reason": "aio_enabled is false in config.json"}

    if gsc is None:
        snap = sorted((ROOT / "state" / "snapshots").glob("*/gsc.json"))
        if not snap:
            return {"status": "skipped", "reason": "no gsc snapshot to take queries from"}
        gsc = json.loads(snap[-1].read_text(encoding="utf-8"))
    if gsc.get("status") != "ok":
        return {"status": "skipped", "reason": "gsc did not run, no queries to check"}

    limit = int(cfg.get("aio_max_queries", 10))
    host = urlparse(cfg["site_url"]).netloc.lower().removeprefix("www.")
    brand = host.split(".")[0]
    queries = _pick_queries(gsc, limit, int(cfg.get("aio_min_impressions", 20)), brand)
    if not queries:
        return {"status": "ok", "checked": 0, "results": [], "note": "no query cleared the impression floor"}

    import requests

    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    results = []
    for q in queries:
        body = [{"keyword": q, "location_code": int(cfg.get("aio_location", 2840)),
                 "language_code": cfg.get("aio_language", "en"), "depth": 20}]
        rec: dict = {"query": q}
        try:
            r = requests.post(ENDPOINT, headers=headers, json=body, timeout=60)
            if not r.ok:
                rec["error"] = f"http {r.status_code}"
                results.append(rec)
                continue
            payload = r.json()
            task = (payload.get("tasks") or [{}])[0]
            if task.get("status_code") != 20000:
                rec["error"] = f"task {task.get('status_code')}: {task.get('status_message')}"
                results.append(rec)
                continue
            items = ((task.get("result") or [{}])[0] or {}).get("items") or []
            rec.update(_aio_from_items(items, host))
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        results.append(rec)
        time.sleep(0.3)

    ok = [r for r in results if "error" not in r]
    with_aio = [r for r in ok if r.get("ai_overview")]
    measured = [r for r in with_aio if r.get("citations_available")]
    return {
        "status": "ok",
        "checked": len(results),
        "estimated_cost_usd": round(len(results) * 0.002, 4),
        "with_ai_overview": len(with_aio),
        "citations_readable": len(measured),
        "citations_unreadable": len(with_aio) - len(measured),
        "cited_in_ai_overview": len([r for r in measured if r.get("cited")]),
        "organic_depth": 20,
        # Deliberately no site-wide vocabulary pile. Pooling related searches
        # across queries mixes neighbourhoods and produces nonsense: "founder
        # bottleneck" drags in the Ray Kroc film, "seo accelerator" drags in
        # beginner SEO courses. The per-query lists below are the usable form.
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(collect(load_config(), load_env()), indent=2))
