"""Shared helpers: config, .env loading, snapshot paths."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def resolve(path_like: str) -> Path | None:
    """Resolve a configured path, treating a relative one as repo-relative.

    Relying on the working directory would mean `secrets/service-account.json`
    resolved differently depending on where the script was launched from, and
    the scheduled run does not launch from the repo root.
    """
    raw = (path_like or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)


def google_session(env: dict[str, str], scopes: list[str]):
    """Return an AuthorizedSession or None if credentials are not configured."""
    creds = resolve(env.get("GOOGLE_APPLICATION_CREDENTIALS", ""))
    if creds is None or not creds.exists():
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    c = service_account.Credentials.from_service_account_file(str(creds), scopes=scopes)
    return AuthorizedSession(c)

def site_repo(env: dict) -> Path | None:
    """Path to the website's source repo, or None if not configured.

    Set SITE_REPO in .env. It is a filesystem path, so it lives there rather
    than in config.json, which is committed. Only the tools that read the
    site's own markdown need it, and every caller copes with None.
    """
    p = resolve(env.get("SITE_REPO", ""))
    return p if p is not None and p.exists() else None


def shared_env_file(env: dict) -> Path | None:
    """Optional second .env to read a shared API key from, or None.

    Lets one credential live in a single place across several projects without
    copying it. This project's own .env always wins.
    """
    p = resolve(env.get("SHARED_ENV_FILE", ""))
    return p if p is not None and p.exists() else None
