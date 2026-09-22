"""Crawl the live site from its sitemap: statuses, redirect chains, broken internal links,
missing or duplicate metas, canonicals. Stdlib HTML parsing, requests for HTTP."""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from common import load_config, load_env

UA = {"User-Agent": "site-seo-check/1.0 (+https://github.com/david-forer/site-seo-check)"}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.title_tags = 0
        self.description = None
        self.description_tags = 0
        self.canonical = None
        self.canonical_tags = 0
        self.h1_count = 0
        self.links: set[str] = set()
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
            self.title_tags += 1
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "a" and a.get("href"):
            self.links.add(a["href"])
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href")
            self.canonical_tags += 1
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            if name == "description":
                self.description = a.get("content") or ""
                self.description_tags += 1
            if name == "robots" and "noindex" in (a.get("content") or "").lower():
                self.noindex = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _sitemap_urls(session: requests.Session, sitemap_url: str, seen: set[str]) -> list[str]:
    if sitemap_url in seen:
        return []
    seen.add(sitemap_url)
    r = session.get(sitemap_url, timeout=20, headers=UA)
    if not r.ok:
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
    if "<sitemapindex" in r.text:
        urls: list[str] = []
        for child in locs:
            urls.extend(_sitemap_urls(session, child, seen))
        return urls
    return locs


def collect(cfg: dict, env: dict[str, str]) -> dict:
    site = cfg["site_url"].rstrip("/")
    host = urlparse(site).netloc
    max_pages = int(cfg.get("crawl_max_pages", 400))
    max_extra = int(cfg.get("crawl_max_extra_links", 300))
    session = requests.Session()

    sitemap_pages = _sitemap_urls(session, f"{site}/sitemap-index.xml", set()) or _sitemap_urls(
        session, f"{site}/sitemap.xml", set()
    )
    if not sitemap_pages:
        return {"status": "error", "reason": "no sitemap found at /sitemap-index.xml or /sitemap.xml"}
    sitemap_pages = sitemap_pages[:max_pages]

    pages: list[dict] = []
    internal_links: dict[str, str] = {}  # link -> one example source page
    titles: dict[str, list[str]] = {}

    for url in sitemap_pages:
        try:
            r = session.get(url, timeout=20, headers=UA)
        except requests.RequestException as e:
            pages.append({"url": url, "status": "fetch_error", "detail": str(e)[:120]})
            continue
        rec: dict = {"url": url, "http": r.status_code}
        if r.history:
            rec["redirect_chain"] = [h.url for h in r.history] + [r.url]
        if r.ok and "text/html" in r.headers.get("Content-Type", ""):
            p = PageParser()
            try:
                p.feed(r.text)
            except Exception:
                pass
            rec.update(
                {
                    "title": p.title.strip(),
                    "title_len": len(p.title.strip()),
                    "title_tags": p.title_tags,
                    "description_len": len(p.description) if p.description is not None else None,
                    "description_tags": p.description_tags,
                    "canonical": p.canonical,
                    "canonical_tags": p.canonical_tags,
                    "h1_count": p.h1_count,
                    "noindex": p.noindex,
                }
            )
            titles.setdefault(p.title.strip(), []).append(url)
            for href in p.links:
                if href.startswith(("mailto:", "tel:", "#", "javascript:")):
                    continue
                full = urljoin(r.url, href).split("#")[0]
                if urlparse(full).netloc == host:
                    internal_links.setdefault(full, url)
        pages.append(rec)
        time.sleep(0.1)

    sitemap_set = {p["url"].rstrip("/") for p in pages}
    extra = [(l, src) for l, src in internal_links.items() if l.rstrip("/") not in sitemap_set][:max_extra]
    broken: list[dict] = []
    redirected_links: list[dict] = []
    for link, src in extra:
        try:
            r = session.get(link, timeout=20, headers=UA)
        except requests.RequestException as e:
            broken.append({"link": link, "found_on": src, "detail": str(e)[:120]})
            continue
        if r.status_code >= 400:
            broken.append({"link": link, "found_on": src, "http": r.status_code})
        elif r.history:
            redirected_links.append({"link": link, "resolves_to": r.url, "found_on": src})
        time.sleep(0.1)

    issues = {
        "non_200_sitemap_urls": [p for p in pages if p.get("http") != 200],
        "sitemap_urls_redirecting": [p["url"] for p in pages if p.get("redirect_chain")],
        "broken_internal_links": broken,
        "internal_links_via_redirect": redirected_links,
        "missing_title": [p["url"] for p in pages if p.get("title") == ""],
        "multiple_title_tags": [p["url"] for p in pages if (p.get("title_tags") or 0) > 1],
        "multiple_description_tags": [p["url"] for p in pages if (p.get("description_tags") or 0) > 1],
        "multiple_canonical_tags": [p["url"] for p in pages if (p.get("canonical_tags") or 0) > 1],
        "missing_description": [p["url"] for p in pages if p.get("description_len") in (None, 0) and p.get("http") == 200],
        "duplicate_titles": {t: us for t, us in titles.items() if t and len(us) > 1},
        "missing_canonical": [p["url"] for p in pages if p.get("http") == 200 and not p.get("canonical")],
        "multiple_h1": [p["url"] for p in pages if (p.get("h1_count") or 0) > 1],
        "noindex_pages": [p["url"] for p in pages if p.get("noindex")],
    }
    return {
        "status": "ok",
        "pages_crawled": len(pages),
        "internal_links_checked": len(extra),
        "issues": issues,
    }


if __name__ == "__main__":
    print(json.dumps(collect(load_config(), load_env()), indent=2))
