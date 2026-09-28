#!/usr/bin/env python3
"""probes.py — replay contested readings and shortcut answers through the grader.

Each probe starts from the gold deliverables and applies one alternative reading. Two kinds:

  CONTESTED  a reading the Final QC pipeline (28 Sep, batch d90c0b8adabf4a37) or a picky
             reviewer could defend from the visible text of the rejected version. After the
             v4 wording fix, each one must score 0.0 AND the sentence that excludes it must
             be present in the agent-visible inputs (checked here, so a later edit that drops
             the sentence re-opens the finding loudly).
  SHORTCUT   a plain error against a rule the text states. Must score 0.0.

The gold must score exactly 1.0. Run from anywhere:
    python3 tools/probes.py            (needs the grader deps: see tools/local_check.sh)
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
GOLD = TASK / "solution" / "files"
INPUT = TASK / "environment" / "input"
VISIBLE = "\n".join([(TASK / "instruction.md").read_text(encoding="utf-8")]
                    + [p.read_text(encoding="utf-8") for p in sorted(INPUT.iterdir())])
VISIBLE = " ".join(VISIBLE.split())  # the input files wrap lines; match on words, not layout


def gold():
    rows = list(csv.DictReader(io.StringIO((GOLD / "offer_evaluation.csv").read_text(encoding="utf-8"))))
    return rows, json.loads((GOLD / "results.json").read_text(encoding="utf-8"))


def score(rows, results):
    ws = Path(tempfile.mkdtemp())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["offer_id", "decision", "reason_code", "landed_cost_usd"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    (ws / "offer_evaluation.csv").write_text(buf.getvalue(), encoding="utf-8")
    (ws / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TASK / "tests" / "score.py")],
                          env={"HARBOR_TASK_WORKSPACE": str(ws), "HARBOR_AGENT_LOGS_DIR": str(ws / "none"),
                               "PATH": "/usr/local/bin:/usr/bin:/bin"},
                          capture_output=True, text=True)
    shutil.rmtree(ws, ignore_errors=True)
    d = json.loads(proc.stdout)
    return d["reward"], [c["detail"][:110] for c in d["checks"] if not c["passed"]]


def setcell(rows, oid, **kw):
    for r in rows:
        if r["offer_id"] == oid:
            r.update(kw)
    return rows


def p_duplicate_kept(rows, res):
    rows.append(dict(next(r for r in rows if r["offer_id"] == "OF-08")))
    res["eligible_offer_count"] += 1
    return rows, res


def p_document_order(rows, res):
    # "first clause of the terms" read as document order: S1 (CUTOFF) before S3 (SUSPENDED),
    # and within S2 the radius sentence before the reserve clause.
    setcell(rows, "OF-05", reason_code="CUTOFF_PASSED")
    setcell(rows, "OF-19", reason_code="OUT_OF_RADIUS")
    return rows, res


def p_stock_depleted_across_offers(rows, res):
    # OF-08 (pickup) and OF-09 (delivery) share ST-44/PC-1015 = 2 units; reading "shared" as
    # "the pickup uses one up" leaves the delivery at 1 < 2.
    setcell(rows, "OF-09", decision="INELIGIBLE", reason_code="STOCK_RESERVE", landed_cost_usd="")
    res["eligible_offer_count"] -= 1
    return rows, res


def p_tie_lowest_id(rows, res):
    res["chosen_offer_id"] = "OF-04"
    return rows, res


def p_float_round(rows, res):
    # round(17.00 * 1.095, 2) on a float gives 18.61 (half-cent must round up: 18.62)
    for oid in ("OF-04", "OF-12"):
        setcell(rows, oid, landed_cost_usd="18.61")
    return rows, res


def p_metres_as_feet(rows, res):
    # '3.7 m' read as 3.7 ft -> FIT_LENGTH; the chosen offer moves to OF-12 (next nearest at 18.62)
    setcell(rows, "OF-21", decision="INELIGIBLE", reason_code="FIT_LENGTH", landed_cost_usd="")
    res["eligible_offer_count"] -= 1
    res["chosen_offer_id"] = "OF-12"
    return rows, res


def p_notice_ignored(rows, res):
    # Columbus 16:00 collection notice ignored -> OF-18 CUTOFF_PASSED
    setcell(rows, "OF-18", decision="INELIGIBLE", reason_code="CUTOFF_PASSED", landed_cost_usd="")
    res["eligible_offer_count"] -= 1
    return rows, res


PROBES = [
    # name, kind, mutation, sentence(s) in the visible text that exclude the reading
    ("duplicate_line_kept", "CONTESTED", p_duplicate_kept,
     ["each distinct `offer_id` appears in the sheet exactly once",
      "number of distinct offers (counted by `offer_id`)"]),
    ("precedence_in_document_order", "CONTESTED", p_document_order,
     ["S5's order, not the order the clauses appear in the terms",
      "whichever section of these terms states it"]),
    ("stock_used_up_across_offers", "CONTESTED", p_stock_depleted_across_offers,
     ["no offer uses up another's units"]),
    ("tie_to_lowest_offer_id", "SHORTCUT", p_tie_lowest_id, []),
    ("float_rounding_half_cent", "SHORTCUT", p_float_round, []),
    ("metres_read_as_feet", "SHORTCUT", p_metres_as_feet, []),
    ("store_notice_ignored", "SHORTCUT", p_notice_ignored, []),
]


def main() -> int:
    ok = True
    rows, res = gold()
    r, fails = score(rows, res)
    print(f"{'gold':32s} reward={r}  {'OK' if r == 1.0 else 'FAIL'}")
    ok &= r == 1.0
    for name, kind, fn, sentences in PROBES:
        rows, res = gold()
        r, fails = score(*fn(rows, res))
        missing = [s for s in sentences if s not in VISIBLE]
        good = r == 0.0 and not missing
        ok &= good
        print(f"{name:32s} {kind:9s} reward={r}  {'OK' if good else 'FAIL'}"
              + (f"  MISSING TEXT: {missing}" if missing else "") + f"\n{'':44s}{fails[0] if fails else ''}")
    # the instruction must not ask for a cord of exactly ten feet (S4 takes 10-15 ft)
    ten = "ten-foot replacement" not in VISIBLE and "at least as long as the ten-foot original" in VISIBLE
    print(f"{'instruction_length_wording':32s} {'CONTESTED':9s} {'OK' if ten else 'FAIL'}")
    ok &= ten
    print("\nALL PROBES OK" if ok else "\nPROBE FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
