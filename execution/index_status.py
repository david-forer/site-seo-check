"""Ask Search Console the live index status of every page on the site.

Why this exists: the only index data this project had was a manual "Crawled -
currently not indexed" export David pulled by hand on 2026-09-08. That export is
a sample, it ages the moment it is written, and it says nothing about the pages
that are fine. The URL Inspection API answers per URL, on demand, for the same
property the rest of the collectors already authenticate against.

What it returns per URL: the coverage verdict, whether Google has it indexed,
the last crawl time, the canonical Google chose against the one the page
declares, and whether the page was found in the sitemap or only by link.

Quota is 2000 URLs a day and 600 a minute per property, so a full site fits
comfortably. The script paces itself and stops on quota rather than hammering.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

from common import ROOT, load_config, load_env, google_session

SCOPES = ["https://www.googleapis.com/auth/webmasters"]
API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def sitemap_urls(session, site_url: str) -> list[str]:
    """Walk the sitemap index and return every page URL it lists."""
    out: list[str] = []
    todo = [f"{site_url.rstrip('/')}/sitemap-index.xml"]
    seen = set()
    while todo:
        sm = todo.pop()
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = session.get(sm, timeout=30)
            if not r.ok:
                continue
            root = ElementTree.fromstring(r.content)
        except Exception:
            continue
        if root.tag.endswith("sitemapindex"):
            todo += [e.text.strip() for e in root.findall(".//s:loc", NS) if e.text]
        else:
            out += [e.text.strip() for e in root.findall(".//s:loc", NS) if e.text]
    return sorted(set(out))


def inspect(session, site: str, url: str) -> dict:
    body = {"inspectionUrl": url, "siteUrl": site}
    try:
        r = session.post(API, json=body, timeout=60)
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}
    if r.status_code == 429:
        return {"url": url, "error": "quota"}
    if not r.ok:
        return {"url": url, "error": f"http {r.status_code}: {r.text[:160]}"}
    idx = (r.json().get("inspectionResult") or {}).get("indexStatusResult") or {}
    return {
        "url": url,
        "verdict": idx.get("verdict"),
        "coverage": idx.get("coverageState"),
        "robots": idx.get("robotsTxtState"),
        "indexing": idx.get("indexingState"),
        "last_crawl": idx.get("lastCrawlTime"),
        "google_canonical": idx.get("googleCanonical"),
        "user_canonical": idx.get("userCanonical"),
        "discovered_via": idx.get("sitemap") or [],
        "referring_urls": len(idx.get("referringUrls") or []),
        "page_fetch": idx.get("pageFetchState"),
    }


def collect(cfg: dict, env: dict[str, str]) -> dict:
    """Entry point for run_all. Inspects every sitemap URL and returns a summary
    plus the full rows, so the weekly compare can diff index state against last
    week and say whether stuck pages recovered."""
    session = google_session(env, SCOPES)
    if session is None:
        return {"status": "skipped", "reason": "GOOGLE_APPLICATION_CREDENTIALS missing, see SETUP.md"}
    if not cfg.get("index_check_enabled", True):
        return {"status": "skipped", "reason": "index_check_enabled is false in config.json"}

    site = cfg["gsc_property"]
    limit = int(cfg.get("index_check_limit", 400))
    urls = sitemap_urls(session, cfg["site_url"])[:limit]
    if not urls:
        return {"status": "error", "error": "no sitemap URLs found"}

    rows, quota = [], False
    for u in urls:
        rec = inspect(session, site, u)
        rows.append(rec)
        if rec.get("error") == "quota":
            quota = True
            break
        time.sleep(0.12)

    ok = [r for r in rows if not r.get("error")]
    indexed = [r for r in ok if r.get("verdict") == "PASS"]
    stuck = [r for r in ok if r.get("verdict") != "PASS"]
    by_state: dict[str, int] = {}
    for r in stuck:
        by_state[r.get("coverage") or "unknown"] = by_state.get(r.get("coverage") or "unknown", 0) + 1
    return {
        "status": "ok",
        "checked": len(rows),
        "quota_hit": quota,
        "indexed": len(indexed),
        "not_indexed": len(stuck),
        "not_indexed_by_state": by_state,
        "not_indexed_urls": sorted(r["url"] for r in stuck),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--only", default="", help="substring filter, e.g. /blog/")
    ap.add_argument("--out", type=Path, default=ROOT / "state" / "index_status.json")
    a = ap.parse_args()

    cfg, env = load_config(), load_env()
    session = google_session(env, SCOPES)
    if session is None:
        sys.exit("No Google credentials. See SETUP.md.")
    site = cfg["gsc_property"]

    urls = sitemap_urls(session, cfg["site_url"])
    if a.only:
        urls = [u for u in urls if a.only in u]
    urls = urls[: a.limit]
    print(f"Inspecting {len(urls)} URLs against {site}. Quota is 2000 a day.")

    rows = []
    for i, u in enumerate(urls, 1):
        rec = inspect(session, site, u)
        rows.append(rec)
        if rec.get("error") == "quota":
            print("  quota reached, stopping early")
            break
        if i % 20 == 0 or i == len(urls):
            done = [r for r in rows if not r.get("error")]
            idx = sum(1 for r in done if r.get("verdict") == "PASS")
            print(f"  {i}/{len(urls)}  indexed {idx}  not indexed {len(done)-idx}")
        time.sleep(0.12)  # stay under 600 a minute

    a.out.write_text(json.dumps({"property": site, "checked": len(rows), "rows": rows},
                                indent=2), encoding="utf-8")
    print(f"\nWrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
