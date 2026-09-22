"""Read a manual Search Console export from inbox/ and turn it into topic demand.

Covers the case where the Search Console API is not connected. Export
Performance > Export > CSV from the Search Console UI and drop the zip into
inbox/. This reads it, filters noise, groups near-duplicate query variants,
checks each opportunity against the live blog, and prints backlog rows.

Usage:
    py execution\\gsc_inbox.py                 report only
    py execution\\gsc_inbox.py --rows          also print backlog.md rows
    py execution\\gsc_inbox.py --archive       move the export to inbox/processed
    py execution\\gsc_inbox.py --json

Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from common import load_env, site_repo

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
# The website checkout, from SITE_REPO in .env. Optional, and every reader
# copes with it being absent.
_REPO = site_repo(load_env())
BLOG = (_REPO / "src" / "content" / "blog") if _REPO else None
PAGES = (_REPO / "src" / "pages") if _REPO else None

# Queries that are never content topics: other people's domains, and pure
# navigational searches for David himself.
NOISE = re.compile(
    r"(\b[\w-]+\.(com|co|io|net|org|ai)\b)"      # any domain
    r"|(\b[\w-]+\s+(com|co\.uk)\b)"              # a domain with the dot lost in export
    r"|(^david\s+forer)"                          # navigational, already ours
    r"|(\bseaccelerator\b)",                      # another site's brand
    re.I)

STOP = {"the", "and", "for", "with", "your", "you", "that", "this", "how", "what",
        "when", "why", "who", "are", "not", "from", "into", "does", "can", "will",
        "should", "small", "business", "businesses", "ai", "best", "top"}

# Searchers and slugs use different words for the same thing. Without this,
# "ai consulting pricing" looks uncovered while ai-consulting-cost exists.
SYNONYMS = {
    "pricing": "cost", "price": "cost", "prices": "cost", "fees": "cost",
    "rates": "cost", "costs": "cost",
    "consulting": "consultant", "consultancy": "consultant", "consultants": "consultant",
    "versus": "vs",
    "automating": "automation", "automate": "automation", "automations": "automation",
    "bottlenecks": "bottleneck",
    "founders": "founder", "ceo": "founder", "owner": "founder",
    "agents": "agent", "tools": "tool",
    "adopt": "adoption", "adopting": "adoption",
    # Searchers say workflow, the slugs say process. Same for vendor and tool.
    "workflow": "process", "workflows": "process", "processes": "process",
    "mapping": "map", "maps": "map",
    "vendor": "tool", "vendors": "tool", "software": "tool", "platform": "tool",
    "switching": "switch", "switched": "switch",
    "aligning": "align", "alignment": "align", "aligned": "align",
    "investments": "investment", "investing": "investment",
    "audits": "audit", "auditing": "audit",
    "accelerated": "accelerator", "acceleration": "accelerator",
    "accelerate": "accelerator", "accelerators": "accelerator",
}


def norm(w: str) -> str:
    w = SYNONYMS.get(w, w)
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = SYNONYMS.get(w[:-1], w[:-1])
    return w


def words(text: str) -> set[str]:
    return {norm(w) for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) > 2 and w not in STOP}


def find_export(explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.exists() else None
    cands = [p for p in INBOX.iterdir()
             if p.is_file() and (p.suffix.lower() == ".zip"
                                 or p.name.lower().startswith("queries"))]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def read_queries(path: Path) -> tuple[list[dict], dict]:
    """Return (query rows, meta) from a GSC zip or a loose Queries.csv."""
    meta: dict = {"source": path.name}
    if path.suffix.lower() == ".zip":
        z = zipfile.ZipFile(path)
        names = {n.lower(): n for n in z.namelist()}
        if "queries.csv" not in names:
            return [], meta
        text = z.read(names["queries.csv"]).decode("utf-8-sig", errors="replace")
        if "filters.csv" in names:
            f = list(csv.reader(io.StringIO(
                z.read(names["filters.csv"]).decode("utf-8-sig", errors="replace"))))
            meta["filters"] = {r[0]: r[1] for r in f[1:] if len(r) >= 2}
    else:
        text = path.read_text(encoding="utf-8-sig", errors="replace")

    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        key = next((k for k in r if k and "quer" in k.lower()), None)
        if not key or not r.get(key):
            continue
        try:
            rows.append({
                "query": r[key].strip(),
                "clicks": int(float(r.get("Clicks", 0) or 0)),
                "impressions": int(float(r.get("Impressions", 0) or 0)),
                "position": float(r.get("Position", 0) or 0),
            })
        except ValueError:
            continue
    return rows, meta


def load_blog() -> list[dict]:
    """Every page that can already answer a query: blog posts and landing pages."""
    posts = []
    if BLOG is not None and BLOG.exists():
        for p in list(BLOG.glob("*.md")) + list(BLOG.glob("*.mdx")):
            head = p.read_text(encoding="utf-8", errors="replace")[:600]
            m = re.search(r'^title:\s*"?(.+?)"?\s*$', head, re.M)
            title = m.group(1) if m else p.stem
            posts.append({"slug": p.stem, "kind": "post",
                          "words": words(p.stem + " " + title)})
    if PAGES is not None and PAGES.exists():
        for p in PAGES.glob("*.astro"):
            if p.stem in {"index", "404", "[...slug]"}:
                continue
            head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            m = re.search(r'title\s*=\s*["\'](.+?)["\']', head)
            title = m.group(1) if m else p.stem
            posts.append({"slug": p.stem, "kind": "page",
                          "words": words(p.stem + " " + title)})
    return posts


def covering_post(query: str, posts: list[dict]) -> str | None:
    """The existing post that best covers this query, if any is close enough."""
    qw = words(query)
    if not qw:
        return None
    best, best_key = None, (0.0, 0.0)
    for p in posts:
        shared = qw & p["words"]
        if not shared:
            continue
        # How much of the query the page covers, then how tightly the page is
        # about only that. The second value breaks ties toward the specific page
        # rather than whichever sorted first.
        coverage = len(shared) / len(qw)
        tightness = len(shared) / len(qw | p["words"])
        key = (coverage, tightness)
        if key > best_key:
            label = p["slug"] if p["kind"] == "post" else p["slug"] + " (page)"
            best, best_key = label, key
    return best if best_key[0] >= 0.6 else None


def group(rows: list[dict]) -> list[dict]:
    """Merge near-duplicate query variants into one theme, keeping the best example."""
    groups: list[dict] = []
    for r in sorted(rows, key=lambda r: -r["impressions"]):
        qw = words(r["query"])
        hit = None
        for g in groups:
            shared = qw & g["words"]
            if shared and len(shared) / max(1, min(len(qw), len(g["words"]))) >= 0.7:
                hit = g
                break
        if hit:
            hit["impressions"] += r["impressions"]
            hit["clicks"] += r["clicks"]
            hit["variants"].append(r["query"])
            hit["position"] = min(hit["position"], r["position"])
        else:
            groups.append({"query": r["query"], "words": qw,
                           "impressions": r["impressions"], "clicks": r["clicks"],
                           "position": r["position"], "variants": [r["query"]]})
    return groups


def analyse(path: Path, pos_min: float, pos_max: float, min_impr: int) -> dict:
    rows, meta = read_queries(path)
    posts = load_blog()

    kept = [r for r in rows if not NOISE.search(r["query"])]
    dropped = len(rows) - len(kept)

    band = [r for r in kept
            if pos_min <= r["position"] <= pos_max and r["impressions"] >= min_impr]

    opportunities = []
    for g in group(band):
        g["covered_by"] = covering_post(g["query"], posts)
        g["variant_count"] = len(g["variants"])
        g.pop("words", None)
        opportunities.append(g)

    opportunities.sort(key=lambda g: (g["covered_by"] is not None, -g["impressions"]))
    return {
        "source": meta.get("source"),
        "filters": meta.get("filters", {}),
        "queries_total": len(rows),
        "queries_after_noise_filter": len(kept),
        "noise_dropped": dropped,
        "band": {"position": [pos_min, pos_max], "min_impressions": min_impr},
        "in_band": len(band),
        "posts_scanned": len(posts),
        "opportunities": opportunities,
    }


def slugify(q: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
    return "-".join(s.split("-")[:6])


def main() -> None:
    ap = argparse.ArgumentParser(description="Turn a manual GSC export into topic demand.")
    ap.add_argument("--file", type=Path, help="specific export, default is newest in inbox")
    ap.add_argument("--pos-min", type=float, default=5.0)
    ap.add_argument("--pos-max", type=float, default=20.0)
    ap.add_argument("--min-impressions", type=int, default=20)
    ap.add_argument("--rows", action="store_true", help="print backlog.md rows")
    ap.add_argument("--archive", action="store_true", help="move export to inbox/processed")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = find_export(args.file)
    if not path:
        print("No Search Console export found in inbox.")
        print("Export it from Search Console > Performance > Export > CSV,")
        print(f"then drop the file into {INBOX}")
        sys.exit(1)

    res = analyse(path, args.pos_min, args.pos_max, args.min_impressions)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"SOURCE: {res['source']}")
        if res["filters"]:
            print("        " + ", ".join(f"{k}: {v}" for k, v in res["filters"].items()))
        print(f"{res['queries_total']} queries, {res['noise_dropped']} filtered as noise, "
              f"{res['in_band']} in position {args.pos_min:.0f}-{args.pos_max:.0f} "
              f"with {args.min_impressions}+ impressions")
        print(f"{res['posts_scanned']} existing posts checked")
        print()

        gaps = [o for o in res["opportunities"] if not o["covered_by"]]
        covered = [o for o in res["opportunities"] if o["covered_by"]]

        if gaps:
            print("DEMAND WITH NO DEDICATED POST:")
            for o in gaps:
                extra = f"  (+{o['variant_count'] - 1} variants)" if o["variant_count"] > 1 else ""
                print(f"  {o['impressions']:>6} imp  best pos {o['position']:>5.1f}  {o['query']}{extra}")
        else:
            print("No uncovered demand in this band.")

        if covered:
            print()
            print("DEMAND ALREADY COVERED (consider a refresh, not a new post):")
            for o in covered:
                print(f"  {o['impressions']:>6} imp  best pos {o['position']:>5.1f}  "
                      f"{o['query']}  ->  {o['covered_by']}")

        if args.rows and gaps:
            print()
            print("BACKLOG ROWS (review before pasting, slugs are suggestions):")
            for o in gaps:
                print(f"| {slugify(o['query'])} | TBD | queued | gsc {o['impressions']} imp "
                      f"pos {o['position']:.0f} |")

    if args.archive:
        dest = INBOX / "processed" / path.name
        dest.parent.mkdir(exist_ok=True)
        shutil.move(str(path), str(dest))
        print(f"\nArchived to {dest}")

    sys.exit(0)


if __name__ == "__main__":
    main()
