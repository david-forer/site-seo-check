"""Run every collector, snapshot results to state\\snapshots\\YYYY-MM-DD\\, print a status summary."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aio_check
import crawl_check
import ga4_pull
import gsc_pull
import index_status
import psi_check
from common import ROOT, load_config, load_env


def main() -> int:
    cfg = load_config()
    env = load_env()
    today = datetime.date.today().isoformat()
    snapdir = ROOT / "state" / "snapshots" / today
    snapdir.mkdir(parents=True, exist_ok=True)

    modules = {"gsc": gsc_pull, "ga4": ga4_pull, "psi": psi_check, "crawl": crawl_check}
    status: dict[str, str] = {}
    results: dict[str, dict] = {}
    for name, mod in modules.items():
        try:
            result = mod.collect(cfg, env)
        except Exception as e:  # one source failing must not kill the run
            result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        (snapdir / f"{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        status[name] = result.get("status", "unknown")
        results[name] = result

    # Index status: the only source that says whether Google actually has each
    # page. Runs after the cheap collectors because it is roughly 1 API call per
    # URL and takes several minutes on a full site.
    try:
        idx = index_status.collect(cfg, env)
    except Exception as e:
        idx = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    (snapdir / "index.json").write_text(json.dumps(idx, indent=2), encoding="utf-8")
    status["index"] = idx.get("status", "unknown")

    # AI Overview check runs last: it takes its query list from this run's GSC
    # pull, and it is the only collector that spends money.
    try:
        aio = aio_check.collect(cfg, env, results.get("gsc"))
    except Exception as e:
        aio = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    (snapdir / "aio.json").write_text(json.dumps(aio, indent=2), encoding="utf-8")
    status["aio"] = aio.get("status", "unknown")

    (snapdir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps({"snapshot": str(snapdir), "status": status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
