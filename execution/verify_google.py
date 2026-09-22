"""Diagnose the Google credential chain for site-seo-check.

Checks each link in order and stops at the first real blocker, naming the exact
next action. Never prints private key material. The service account email is
printed on purpose: it is the value you paste into Search Console and GA4.

Usage, from the repo root:
    py execution/verify_google.py
    py execution/verify_google.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import ROOT, load_config, load_env

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
GSC_API = "https://searchconsole.googleapis.com/webmasters/v3"
GA4_API = "https://analyticsdata.googleapis.com/v1beta"
PSI_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

OK, WARN, FAIL = "ok", "warn", "fail"


def _r(checks: list[dict], name: str, status: str, detail: str, action: str = "") -> None:
    checks.append({"check": name, "status": status, "detail": detail, "action": action})


def check_env(checks: list[dict]) -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        _r(checks, ".env file", FAIL, f"not found at {env_path}",
           "Copy .env.example to .env and fill it in. See SETUP.md step 3.")
        return {}
    env = load_env()
    _r(checks, ".env file", OK, f"found at {env_path}")
    return env


def check_creds(checks: list[dict], env: dict[str, str]):
    path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not path:
        _r(checks, "service account key", FAIL, "GOOGLE_APPLICATION_CREDENTIALS is empty in .env",
           "Set it to secrets/service-account.json, relative to the repo root. SETUP.md steps 1.4 and 3.")
        return None, None
    p = Path(path)
    if not p.exists():
        _r(checks, "service account key", FAIL, f"file does not exist: {path}",
           "Download the JSON key from Google Cloud and save it at that exact path. SETUP.md step 1.4.")
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _r(checks, "service account key", FAIL, f"file is not valid JSON ({type(e).__name__})",
           "Re-download the key from Google Cloud. Do not edit it by hand.")
        return None, None
    email = data.get("client_email", "")
    if not email:
        _r(checks, "service account key", FAIL, "no client_email in the JSON",
           "This is not a service account key. Create one under Credentials > Service account.")
        return None, None
    _r(checks, "service account key", OK, f"valid, project {data.get('project_id', 'unknown')}")
    _r(checks, "service account email", OK, email,
       "This is the address to grant access to in Search Console and GA4.")
    return str(p), email


def _session(creds_path: str, scopes: list[str]):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    return AuthorizedSession(creds)


def check_gsc(checks: list[dict], cfg: dict, creds_path: str, email: str) -> None:
    want = cfg["gsc_property"]
    try:
        session = _session(creds_path, GSC_SCOPES)
        r = session.get(f"{GSC_API}/sites", timeout=30)
    except Exception as e:
        _r(checks, "search console auth", FAIL, f"{type(e).__name__}: {e}",
           "Check that the Search Console API is enabled in Google Cloud. SETUP.md step 1.2.")
        return

    if r.status_code == 403:
        _r(checks, "search console auth", FAIL, "403 from the sites list",
           "Enable the Google Search Console API for the project. SETUP.md step 1.2.")
        return
    if not r.ok:
        _r(checks, "search console auth", FAIL, f"HTTP {r.status_code}", "See the response above.")
        return

    sites = [s.get("siteUrl", "") for s in r.json().get("siteEntry", [])]
    if not sites:
        _r(checks, "search console access", FAIL, "the service account can see 0 properties",
           f"In Search Console > Settings > Users and permissions, add {email} with Full permission. SETUP.md step 2.1.")
        return

    _r(checks, "search console access", OK, f"can see {len(sites)} property: {', '.join(sites)}"
       if len(sites) == 1 else f"can see {len(sites)} properties: {', '.join(sites)}")

    if want in sites:
        _r(checks, "configured property", OK, want)
    else:
        suggestion = sites[0]
        _r(checks, "configured property", FAIL,
           f"config.json wants {want}, which is not in the visible list",
           f'Set "gsc_property" in config.json to {suggestion}, or grant access to {want}.')
        return

    body = {"startDate": "2026-01-01", "endDate": "2026-01-07", "dimensions": ["query"], "rowLimit": 1}
    prop = want.replace("/", "%2F").replace(":", "%3A")
    q = session.post(f"{GSC_API}/sites/{prop}/searchAnalytics/query", json=body, timeout=60)
    if q.ok:
        _r(checks, "search console query", OK, f"query call succeeded, {len(q.json().get('rows', []))} sample rows")
    else:
        _r(checks, "search console query", FAIL, f"HTTP {q.status_code}",
           "Access exists but the query was refused. Confirm the permission level is Full, not Restricted.")


def check_ga4(checks: list[dict], env: dict[str, str], creds_path: str, email: str) -> None:
    pid = env.get("GA4_PROPERTY_ID", "").strip()
    if not pid:
        _r(checks, "ga4 property id", WARN, "GA4_PROPERTY_ID is empty in .env",
           "Copy the numeric Property ID from GA4 Admin > Property details. SETUP.md step 2.3.")
        return
    try:
        session = _session(creds_path, GA4_SCOPES)
        body = {"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
                "metrics": [{"name": "sessions"}]}
        r = session.post(f"{GA4_API}/properties/{pid}:runReport", json=body, timeout=60)
    except Exception as e:
        _r(checks, "ga4 access", FAIL, f"{type(e).__name__}: {e}",
           "Check that the Google Analytics Data API is enabled. SETUP.md step 1.2.")
        return
    if r.ok:
        _r(checks, "ga4 access", OK, f"property {pid} responded")
    elif r.status_code == 403:
        _r(checks, "ga4 access", FAIL, f"403 on property {pid}",
           f"In GA4 Admin > Property access management, add {email} as Viewer. SETUP.md step 2.2.")
    else:
        _r(checks, "ga4 access", FAIL, f"HTTP {r.status_code} on property {pid}",
           "Confirm the Property ID is the numeric one, not the measurement ID starting with G-.")


def check_psi(checks: list[dict], cfg: dict, env: dict[str, str]) -> None:
    key = env.get("PSI_API_KEY", "").strip()
    if not key:
        _r(checks, "pagespeed key", WARN, "PSI_API_KEY is empty in .env",
           "Without a key the API is rate limited and returns 429. SETUP.md step 1.5.")
        return
    import requests
    url = cfg["psi_pages"][0]
    try:
        r = requests.get(PSI_API, params={"url": url, "key": key, "strategy": "mobile"}, timeout=90)
    except Exception as e:
        _r(checks, "pagespeed key", FAIL, f"{type(e).__name__}: {e}", "Network or timeout issue, retry.")
        return
    if r.ok:
        _r(checks, "pagespeed key", OK, "key accepted")
    elif r.status_code == 429:
        _r(checks, "pagespeed key", WARN, "429 quota exceeded even with a key",
           "Wait and retry. If it persists, confirm the key is unrestricted or allows the PageSpeed Insights API.")
    else:
        _r(checks, "pagespeed key", FAIL, f"HTTP {r.status_code}",
           "Confirm the PageSpeed Insights API is enabled and the key has no referrer restriction.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose Google credentials for site-seo-check.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    checks: list[dict] = []

    env = check_env(checks)
    creds_path = email = None
    if env:
        creds_path, email = check_creds(checks, env)
    if creds_path and email:
        check_gsc(checks, cfg, creds_path, email)
        check_ga4(checks, env, creds_path, email)
        check_psi(checks, cfg, env)

    failed = [c for c in checks if c["status"] == FAIL]

    if args.json:
        print(json.dumps({"checks": checks, "blocked": bool(failed)}, indent=2))
    else:
        width = max(len(c["check"]) for c in checks)
        for c in checks:
            mark = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}[c["status"]]
            print(f"  [{mark}] {c['check']:<{width}}  {c['detail']}")
            if c["action"] and c["status"] != OK:
                print(f"         -> {c['action']}")
        print()
        if failed:
            print("NEXT ACTION: " + failed[0]["action"])
        else:
            warns = [c for c in checks if c["status"] == WARN]
            if warns:
                print("GSC is ready. Remaining optional: " + warns[0]["action"])
            else:
                print("All Google sources are ready. Run: py execution\\run_all.py")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
