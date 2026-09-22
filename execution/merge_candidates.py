"""For every page Google crawled and declined, find the indexed page that already
covers the same ground.

The point of this is to settle one question per URL rather than by instinct:
does removing this page cost the site any topical coverage?

  If an indexed page covers the same subject, merging costs nothing. The
  vocabulary and the angle move onto a URL Google actually reads, and the thin
  one stops competing with it.

  If nothing indexed covers it, the page is a real gap in the map. Fix it, do
  not remove it. That is the case where keeping the page is right.

Overlap is measured three ways and reported separately, because any single
measure is easy to fool. Slug token overlap catches the near-duplicate pairs
published minutes apart. Title overlap catches rewrites under a different slug.
Body trigram similarity catches the rest. A human still reads the shortlist.

Input is state\\index_status.json from index_status.py plus the markdown in the
site's own repo, located via SITE_REPO in .env. Nothing here calls a
paid API and nothing writes to the site repo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import ROOT, load_env, site_repo
STOP = {
    "the", "a", "an", "and", "or", "for", "to", "in", "of", "is", "it", "with",
    "your", "you", "how", "what", "why", "when", "small", "business", "businesses",
    "ai", "guide", "best", "that", "this", "on", "at", "by", "from", "as", "be",
}
WORD = re.compile(r"[a-z0-9]+")


def slug_of(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def tokens(s: str) -> set[str]:
    return {w for w in WORD.findall(s.lower()) if w not in STOP and len(w) > 2}


def frontmatter(p: Path) -> tuple[str, str]:
    """Return (title, body) without reading the file twice."""
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", ""
    title = ""
    m = re.search(r'^title:\s*"?(.*?)"?\s*$', raw, re.M)
    if m:
        title = m.group(1)
    parts = raw.split("---", 2)
    body = parts[2] if len(parts) > 2 else raw
    return title, body


def trigrams(s: str) -> set[str]:
    w = [x for x in WORD.findall(s.lower()) if x not in STOP]
    return {" ".join(w[i:i + 3]) for i in range(max(0, len(w) - 2))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", type=Path, default=ROOT / "state" / "index_status.json")
    ap.add_argument("--out", type=Path, default=ROOT / "state" / "merge_candidates.json")
    ap.add_argument("--top", type=int, default=3, help="candidates to report per unindexed page")
    a = ap.parse_args()

    repo = site_repo(load_env())
    if repo is None:
        sys.exit(
            "This tool reads the site's markdown to compare pages against each other.\n"
            "Set SITE_REPO in .env to the checkout of the website, for example\n"
            "  SITE_REPO=C:/code/my-site\n"
            "It lives in .env because it is a filesystem path, not a setting."
        )
    blog = repo / "src" / "content" / "blog"
    if not blog.is_dir():
        sys.exit(f"No blog collection at {blog}. Check SITE_REPO in .env.")

    data = json.loads(a.status.read_text(encoding="utf-8"))
    rows = [r for r in data["rows"] if not r.get("error")]
    indexed = [r for r in rows if r.get("verdict") == "PASS"]
    stuck = [r for r in rows if r.get("verdict") != "PASS"]
    print(f"{len(rows)} URLs inspected: {len(indexed)} indexed, {len(stuck)} not.")

    # Load the markdown once for every slug we know about.
    docs: dict[str, dict] = {}
    for p in blog.glob("*.md"):
        t, b = frontmatter(p)
        docs[p.stem] = {"title": t, "tok": tokens(t + " " + p.stem), "tri": trigrams(b), "words": len(b.split())}

    idx_slugs = [s for s in (slug_of(r["url"]) for r in indexed) if s in docs]
    out = []
    for r in stuck:
        s = slug_of(r["url"])
        d = docs.get(s)
        rec = {
            "url": r["url"], "slug": s,
            "coverage": r.get("coverage"), "last_crawl": r.get("last_crawl"),
            "google_canonical": r.get("google_canonical"), "user_canonical": r.get("user_canonical"),
            "words": d["words"] if d else None, "title": d["title"] if d else "",
            "candidates": [],
        }
        if d:
            scored = []
            for o in idx_slugs:
                if o == s:
                    continue
                od = docs[o]
                scored.append({
                    "slug": o, "title": od["title"],
                    "slug_title_overlap": round(jaccard(d["tok"], od["tok"]), 3),
                    "body_similarity": round(jaccard(d["tri"], od["tri"]), 3),
                })
            scored.sort(key=lambda x: -(x["slug_title_overlap"] * 2 + x["body_similarity"]))
            rec["candidates"] = scored[: a.top]
            best = scored[0] if scored else None
            if best and (best["slug_title_overlap"] >= 0.30 or best["body_similarity"] >= 0.12):
                rec["verdict"] = "MERGE, an indexed page already covers this"
            elif best and best["slug_title_overlap"] >= 0.18:
                rec["verdict"] = "REVIEW, partial overlap, read both before deciding"
            else:
                rec["verdict"] = "GAP, nothing indexed covers it, fix the page rather than remove it"
        else:
            rec["verdict"] = "not a blog post, check separately"
        out.append(rec)

    a.out.write_text(json.dumps({"indexed": len(indexed), "not_indexed": len(stuck),
                                 "rows": out}, indent=2), encoding="utf-8")
    from collections import Counter
    print(Counter(r.get("verdict", "") for r in out))
    print(f"Wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
