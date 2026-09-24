#!/usr/bin/env python3
"""probes.py — replay shortcut answers through the grader.

Each probe is the ledger engine with one shortcut flag set: what a script does when it gets
one clause of WM-STD-1 rev. 3 wrong. Every probe must score 0.0 (a named core failure); the
faithful engine must score 1.0. Probes that oversell stock are marked LOUD (a model would see
the error); SILENT ones produce a plausible wrong answer. Run from the task root:
    python3 tools/probes.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK / "solution"))
from ledger_engine import Opts, evaluate, results_of  # noqa: E402

INPUT = TASK / "environment" / "input"


def score(wines, results):
    ws = Path(tempfile.mkdtemp())
    (ws / "wine_findings.csv").write_text("wine_id,finding\n" + "".join(f"{w.wine_id},{w.finding}\n" for w in wines))
    (ws / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    shutil.copy(TASK / "solution" / "files" / "wine_memo.md", ws / "wine_memo.md")
    proc = subprocess.run([sys.executable, str(TASK / "tests" / "score.py")],
                          env={"HARBOR_TASK_WORKSPACE": str(ws), "PATH": "/usr/local/bin:/usr/bin:/bin"},
                          capture_output=True, text=True)
    d = json.loads(proc.stdout)
    detail = [c["detail"][:90] for c in d["checks"] if not c["passed"]]
    shutil.rmtree(ws, ignore_errors=True)
    return d["reward"], d["core_failures"], detail


PROBES = {
    "faithful engine (must be 1.0)": {},
    "weighted-average cost instead of FIFO": {"costing": "average"},
    "LIFO instead of FIFO": {"costing": "lifo"},
    "ledger processed in listed order, not posted_date": {"file_order": True},
    "opening layers consumed in listed order (newest first)": {"opening_file_order": True},
    "write-offs ignored": {"writeoff_ignored": True},
    "write-offs counted in cost of bottles sold": {"writeoff_in_cogs": True},
    "supplier return taken oldest layer first": {"rts_fifo": True},
    "supplier returns ignored": {"rts_ignored": True},
    "customer returns ignored": {"cr_ignored": True},
    "customer return costed at the sale's first-drawn layer": {"cr_cost_first": True},
    "customer return costed at the sale's average cost": {"cr_cost_avg": True},
    "customer return put back at the front of the queue": {"cr_front": True},
    "customer return reverses revenue but not cost": {"cr_revenue_only": True},
    "freight read per bottle regardless of basis": {"basis_ignored": True},
    "freight ignored": {"no_freight": True},
    "flat 20 pct minimum": {"flat20": True},
    "every futures wine exempt": {"all_futures_exempt": True},
    "no futures exemption": {"no_futures_exempt": True},
    "case-sensitive names": {"case_sensitive": True},
    "margin rounded to 0.1 pct before comparing": {"round1dp": True},
    "alternate supplier accepted on stock wines": {"alt_on_stock": True},
    "NV always invalid": {"nv_invalid": True},
    "futures vintage capped at 2025": {"no_futures_year": True},
}

if __name__ == "__main__":
    bad = 0
    for name, kw in PROBES.items():
        wines = evaluate(INPUT, Opts(**kw))
        loud = "LOUD  " if any(w.oversold for w in wines) else "silent"
        reward, core, detail = score(wines, results_of(wines))
        ok = (reward == 1.0) == name.startswith("faithful")
        bad += not ok
        print(f"{reward:<4} {loud} {name:56s} {core} {detail[:1]}{'' if ok else '   <-- UNEXPECTED'}")
    sys.exit(1 if bad else 0)
