"""Build the evidence for one topic cluster, for a cluster whose demand no
keyword tool can see.

The usual method does not work here. `serp_competitors` and `ranked_keywords`
both read DataForSEO's keyword database, and the founder-bottleneck terms are
not in it: 288 of this site's 383 real Search Console queries have no database
entry at all, and that half carries 42 percent of its impressions. Asking a
keyword tool to expand an invisible cluster returns nothing.

So this expands from Google itself instead. Each SERP call returns the People
Also Ask questions, the related searches, the AI Overview and its citations, and
the organic list. Those PAA and related terms become the next round of seeds.
Two rounds is usually enough to find the edges of a cluster.

What comes out, per query: whether an AI Overview sits above the results, which
domains it cites, who ranks organically, and how weak or strong the field is.
That is the proof needed to decide which pages to build, keep or cut.

Cost: roughly $0.002 a query. Round 1 is the seed list, round 2 is capped. The
script prints its spend as it goes and refuses to exceed --max-calls.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from aio_check import _credential
from common import ROOT, load_config, load_env

SERP = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

# Not peers, whatever they rank. Kept explicit so the peer list stays readable.
PLATFORM = {
    "youtube.com", "linkedin.com", "facebook.com", "reddit.com", "medium.com",
    "substack.com", "quora.com", "x.com", "twitter.com", "instagram.com",
    "tiktok.com", "pinterest.com", "amazon.com", "imdb.com", "wikipedia.org",
    "trustpilot.com", "skool.com", "glassdoor.com", "indeed.com", "upwork.com",
}
# Big publishers and enterprise. Real rankers, not lookalikes.
MAJOR = {
    "forbes.com", "hbr.org", "inc.com", "entrepreneur.com", "fastcompany.com",
    "businessinsider.com", "cio.com", "mckinsey.com", "bain.com", "deloitte.com",
    "pwc.com", "kpmg.com", "ey.com", "accenture.com", "gartner.com", "ibm.com",
    "microsoft.com", "salesforce.com", "oracle.com", "sap.com", "hubspot.com",
    "atlassian.com", "asana.com", "monday.com", "clickup.com", "miro.com",
    "notion.so", "zapier.com", "smartsheet.com", "nvidia.com",
}
# Queries that look like the cluster but are a different subject entirely.
# "founder effect" is population genetics. Anything naming the Ray Kroc film is
# the film. Template, PDF and definition modifiers are a different intent.
OFF_TOPIC = re.compile(
    r"founder effect|genetic|biology|\bmovie\b|\bfilm\b|ray kroc|mcdonald|justwatch|"
    r"disney|netflix|\bimdb\b|where can i watch|\btrailer\b|\bpdf\b|\btemplate\b|"
    r"\bmeaning\b|\bdefinition\b|stand for|is sop a word|infotech|inftech|research centre",
    re.I,
)
# An expansion candidate has to still be about this cluster. Google's related
# searches drift fast: "sop dashboard" returns SOP definitions and "when should a
# founder ceo stop..." returns the Ray Kroc film. Without this gate two weak
# seeds pull a whole round off topic, which is what happened on the first run.
ON_CLUSTER = re.compile(
    r"bottleneck|founder|owner|\bceo\b|delegat|scal|operational|operations|"
    r"process|workflow|\bsop\b|\bsops\b|hiring|team|growth|growing|capacity|"
    r"decision|dependen|constraint",
    re.I,
)


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip().lower()


def fetch(token: str, kw: str, loc: int, lang: str, depth: int, tries: int = 3, host: str = "") -> dict | None:
    """One SERP call. Retries transient task failures, which this endpoint
    returns intermittently for queries that succeed on a second attempt."""
    for attempt in range(1, tries + 1):
        rec = _fetch_once(token, kw, loc, lang, depth, host)
        if not rec.get("error"):
            return rec
        if attempt < tries:
            time.sleep(1.5 * attempt)
    return rec


def _fetch_once(token: str, kw: str, loc: int, lang: str, depth: int, host: str = "") -> dict:
    import requests

    try:
        r = requests.post(
            SERP,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            json=[{"keyword": kw, "location_code": loc, "language_code": lang, "depth": depth}],
            timeout=90,
        )
        if not r.ok:
            return {"query": kw, "error": f"http {r.status_code}"}
        task = (r.json().get("tasks") or [{}])[0]
        if task.get("status_code") != 20000:
            return {"query": kw, "error": f"task {task.get('status_code')}"}
        res = (task.get("result") or [{}])[0] or {}
    except Exception as e:
        return {"query": kw, "error": f"{type(e).__name__}: {e}"}

    items = res.get("items") or []
    rec: dict = {
        "query": kw,
        "results_count": res.get("se_results_count"),
        "item_types": sorted({i.get("type") for i in items if i.get("type")}),
        "ai_overview": False, "aio_citations": [], "aio_citations_readable": False,
        "paa": [], "related": [], "organic": [], "our_rank": None,
    }
    for it in items:
        t = it.get("type")
        if t == "ai_overview":
            rec["ai_overview"] = True
            refs = list(it.get("references") or [])
            if not refs:
                for sub in it.get("items") or []:
                    refs.extend(sub.get("references") or [])
            doms = []
            for ref in refs:
                d = (ref.get("domain") or "").lower().removeprefix("www.")
                if d and d not in doms:
                    doms.append(d)
            rec["aio_citations"] = doms
            rec["aio_citations_readable"] = bool(doms)
        elif t == "people_also_ask":
            rec["paa"] = [q.get("title") for q in (it.get("items") or []) if q.get("title")]
        elif t == "related_searches":
            rec["related"] = [x for x in (it.get("items") or []) if isinstance(x, str)]
        elif t == "organic":
            d = (it.get("domain") or "").lower().removeprefix("www.")
            if len(rec["organic"]) < 10:
                rec["organic"].append({
                    "rank": it.get("rank_absolute"), "domain": d,
                    "title": (it.get("title") or "")[:120], "url": it.get("url"),
                })
            if rec["our_rank"] is None and host and host in d:
                rec["our_rank"] = it.get("rank_absolute")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=Path, default=ROOT / "state" / "cluster_seeds.json")
    ap.add_argument("--out", type=Path, default=ROOT / "state" / "cluster_map.json")
    ap.add_argument("--round2", type=int, default=14, help="expansion queries from PAA and related")
    ap.add_argument("--max-calls", type=int, default=35, help="hard spend ceiling")
    ap.add_argument("--depth", type=int, default=15)
    a = ap.parse_args()

    cfg, env = load_config(), load_env()
    token = _credential(env, cfg)
    if token is None:
        sys.exit("No DATAFORSEO_API_KEY. See SETUP.md step 5.")
    loc, lang = int(cfg.get("aio_location", 2840)), cfg.get("aio_language", "en")
    host = urlparse(cfg["site_url"]).netloc.lower().removeprefix("www.")

    seeds = [_norm(s) for s in json.loads(a.seeds.read_text(encoding="utf-8"))]
    seeds = [s for s in seeds if not OFF_TOPIC.search(s)]
    budget = a.max_calls
    if len(seeds) > budget:
        seeds = seeds[:budget]

    records: list[dict] = []
    print(f"Round 1: {len(seeds)} seed queries, ceiling {budget} calls total.")
    for i, s in enumerate(seeds, 1):
        rec = fetch(token, s, loc, lang, a.depth, host=host)
        records.append(rec)
        budget -= 1
        flag = "err" if rec.get("error") else (
            f"AIO {len(rec['aio_citations'])} cites" if rec["ai_overview"] else "no AIO")
        print(f"  {i:>2}/{len(seeds)} {flag:>14}  rank {str(rec.get('our_rank')):>4}  {s[:60]}")
        time.sleep(0.3)

    # Round 2 seeds come from Google's own expansion, not from us.
    seen = {_norm(r["query"]) for r in records}
    pool: list[str] = []
    for r in records:
        if r.get("error") or not ON_CLUSTER.search(r["query"]):
            continue  # a weak seed's neighbourhood is not this cluster's
        for q in (r.get("related") or []) + (r.get("paa") or []):
            n = _norm(q)
            if not n or n in seen or n in pool:
                continue
            if OFF_TOPIC.search(n) or not ON_CLUSTER.search(n):
                continue
            pool.append(n)
    take = min(a.round2, budget, len(pool))
    print(f"\nRound 2: {len(pool)} expansion candidates from PAA and related, taking {take}.")
    for i, q in enumerate(pool[:take], 1):
        rec = fetch(token, q, loc, lang, a.depth, host=host)
        rec["from_expansion"] = True
        records.append(rec)
        flag = "err" if rec.get("error") else (
            f"AIO {len(rec['aio_citations'])} cites" if rec["ai_overview"] else "no AIO")
        print(f"  {i:>2}/{take} {flag:>14}  rank {str(rec.get('our_rank')):>4}  {q[:60]}")
        time.sleep(0.3)

    ok = [r for r in records if not r.get("error")]
    calls = len(records)
    out = {
        "calls": calls,
        "estimated_cost_usd": round(calls * 0.002, 3),
        "queries": records,
        "unused_expansion_pool": pool[take:],
    }
    a.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{len(ok)}/{calls} queries collected. Spend about ${out['estimated_cost_usd']}.")
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
