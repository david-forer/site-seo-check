"""Core Web Vitals via the PageSpeed Insights API for the configured key pages."""
from __future__ import annotations

import json

import requests

from common import load_config, load_env

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _check(url: str, key: str) -> dict:
    params = {"url": url, "strategy": "mobile"}
    if key:
        params["key"] = key
    r = requests.get(API, params=params, timeout=120)
    if not r.ok:
        return {"url": url, "status": "error", "http": r.status_code, "detail": r.text[:200]}
    data = r.json()
    out: dict = {"url": url, "status": "ok"}
    lh = data.get("lighthouseResult", {})
    perf = lh.get("categories", {}).get("performance", {}).get("score")
    out["performance_score"] = round(perf * 100) if perf is not None else None
    audits = lh.get("audits", {})
    for k, label in [
        ("largest-contentful-paint", "lcp"),
        ("cumulative-layout-shift", "cls"),
        ("total-blocking-time", "tbt"),
    ]:
        a = audits.get(k, {})
        out[label] = a.get("displayValue")
    crux = data.get("loadingExperience", {})
    out["crux_category"] = crux.get("overall_category")
    return out


def collect(cfg: dict, env: dict[str, str]) -> dict:
    key = env.get("PSI_API_KEY", "").strip()
    results = [_check(u, key) for u in cfg.get("psi_pages", [])]
    return {"status": "ok", "results": results}


if __name__ == "__main__":
    print(json.dumps(collect(load_config(), load_env()), indent=2))
