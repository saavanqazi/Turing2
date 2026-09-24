#!/usr/bin/env python3
"""probes.py — replay shortcut answers through the grader.

Each probe is the policy engine with one shortcut flag set: what a rule-to-code script does
when it misses one clause. Every probe must score 0.0 (a named core failure); the faithful
engine must score 1.0. Run from the task root:  python3 tools/probes.py
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
from policy_engine import Opts, evaluate, results_of  # noqa: E402

INPUT = TASK / "environment" / "input"


def score(wines, results):
    ws = Path(tempfile.mkdtemp())
    real = [w for w in wines if w.wine_id != "__lines__"]
    (ws / "wine_findings.csv").write_text("wine_id,finding\n" + "".join(f"{w.wine_id},{w.finding}\n" for w in real))
    (ws / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    shutil.copy(TASK / "solution" / "files" / "wine_memo.md", ws / "wine_memo.md")
    proc = subprocess.run([sys.executable, str(TASK / "tests" / "score.py")],
                          env={"HARBOR_TASK_WORKSPACE": str(ws), "PATH": "/usr/local/bin:/usr/bin:/bin"},
                          capture_output=True, text=True)
    d = json.loads(proc.stdout)
    detail = [c["detail"][:100] for c in d["checks"] if not c["passed"]]
    shutil.rmtree(ws, ignore_errors=True)
    return d["reward"], d["core_failures"], detail


PROBES = {
    "faithful engine (must be 1.0)": {},
    "flat 20 pct minimum for every category": {"flat20": True},
    "margin on purchase price, freight ignored": {"no_freight": True},
    "freight of the manifest's expected supplier": {"expected_freight": True},
    "first line per wine wins (status ignored)": {"first_line_wins": True},
    "last line per wine wins (status ignored)": {"last_line_wins": True},
    "one output row per active line (repeated line kept)": {"per_line": True},
    "wine_count = number of input lines": {"count_lines": True},
    "every futures wine exempt (allocation ignored)": {"all_futures_exempt": True},
    "alternate supplier accepted on stock wines": {"alt_on_stock": True},
    "margin rounded to 0.1 pct before comparing": {"round1dp": True},
    "NV always invalid": {"nv_invalid": True},
    "case-sensitive supplier/type comparison": {"case_sensitive": True},
    "futures vintage capped at the review year": {"no_futures_year": True},
    "shortfall rounded with float round()": {"float_round": True},
    "pack prices treated as per-bottle prices": {"pack_ignored": True},
    "freight read as per bottle regardless of basis": {"basis_ignored": True},
    "manifest: last row per wine wins (dict overwrite)": {"manifest_last": True},
    "manifest: first row per wine wins": {"manifest_first": True},
    "manifest: row dated after the review year honoured": {"future_row_honoured": True},
    "buyer's notes ignored": {"notes_ignored": True},
    "buyer's notes applied to every line regardless of date": {"notes_global": True},
    "only the latest note per subject applied (windows ignored)": {"notes_latest_only": True},
    "two active lines: first listed wins instead of latest po_date": {"active_first_wins": True},
    "wines with no active line still listed": {"unreviewed_listed": True},
    "note dates read against the wine's latest line of any status": {"inactive_lines_dated": True},
}

if __name__ == "__main__":
    bad = 0
    for name, kw in PROBES.items():
        wines = evaluate(INPUT, Opts(**kw))
        reward, core, detail = score(wines, results_of(wines))
        ok = (reward == 1.0) == name.startswith("faithful")
        bad += not ok
        print(f"{reward:<5} {name:62s} {core} {detail[:1]}{'' if ok else '   <-- UNEXPECTED'}")
    sys.exit(1 if bad else 0)
