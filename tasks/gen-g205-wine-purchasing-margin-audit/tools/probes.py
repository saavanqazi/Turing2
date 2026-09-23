#!/usr/bin/env python3
"""probes.py — replay shortcut answers through the grader.

Each probe is an independent, deliberately flawed solver: the shortcut a rule-to-code script
takes when it misses one clause of the policy. Every probe must score 0.0 (a named core
failure); the faithful solver must score 1.0. Run from the task root:
    python3 tools/probes.py
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
MIN_MARGIN = {"still": Decimal("20"), "sparkling": Decimal("25"), "fortified": Decimal("18")}


def rows(name):
    with (INPUT / name).open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def solve(*, flat20=False, no_freight=False, expected_freight=False, first_wins=False, last_wins=False,
          per_line=False, all_futures_exempt=False, alt_on_stock=False, round1dp=False, nv_invalid=False,
          case_sensitive=False, no_futures_year=False, count_lines=False, float_round=False,
          strip_only=False, pack_ignored=False, basis_ignored=False, manifest_last=False,
          manifest_first=False, future_row_honoured=False):
    n = (lambda s: (s or "").strip()) if (case_sensitive or strip_only) else (lambda s: (s or "").strip().casefold())
    manifest = {}
    for r in rows("wine_manifest.csv"):
        k = n(r["wine_id"])
        if manifest_last:
            manifest[k] = r
        elif manifest_first:
            manifest.setdefault(k, r)
        else:
            if r["effective_from"] > "2025-12-31" and not future_row_honoured:
                continue
            if k not in manifest or r["effective_from"] > manifest[k]["effective_from"]:
                manifest[k] = r
    freight = {}
    for r in rows("supplier_terms.csv"):
        f = Decimal(r["freight"])
        if n(r["freight_basis"]) == "per case" and not basis_ignored:
            f = f / Decimal(r["case_size"])
        freight[n(r["supplier"])] = f
    lines = rows("wine_purchases.csv")
    if per_line:
        chosen = [(r["wine_id"].strip(), r) for r in lines if n(r["po_status"]) == "active"]
    else:
        gov, order = {}, []
        for r in lines:
            k = n(r["wine_id"])
            if k not in order:
                order.append(k)
            if first_wins:
                gov.setdefault(k, r)
            elif last_wins:
                gov[k] = r
            elif n(r["po_status"]) == "active":
                gov[k] = r
        chosen = [(gov[k]["wine_id"].strip(), gov[k]) for k in order]
    out, counts, short = [], {"MARGIN_TOO_LOW": 0, "VINTAGE_INVALID": 0, "SUPPLIER_MISMATCH": 0}, Decimal("0")
    for wid, r in chosen:
        m = manifest.get(n(r["wine_id"]))
        bottles = Decimal(1) if pack_ignored else Decimal(re.fullmatch(r"(\d+)x\d+cl", r["pack"].strip()).group(1))
        purchase, selling = Decimal(r["purchase_price"]) / bottles, Decimal(r["selling_price"]) / bottles
        sup = n(r["supplier"])
        futures = n(r["wine_type"]) == "futures"
        cat = n(m["category"]) if m else ""
        fr = Decimal("0") if no_freight else freight.get(n(m["expected_supplier"]) if (expected_freight and m) else sup, Decimal("0"))
        landed = purchase + fr
        margin = (selling - landed) / landed * 100
        if round1dp:
            margin = margin.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        minimum = Decimal("20") if flat20 else (MIN_MARGIN[cat] if cat else None)
        exempt = futures and (True if all_futures_exempt else (m is not None and n(m["allocation"]) == "open"))
        low = minimum is not None and margin < minimum
        v = r["vintage"].strip()
        if v.upper() == "NV":
            vok = (not nv_invalid) and m is not None and n(m["nv_allowed"]) == "y"
        elif re.fullmatch(r"\d{4}", v):
            vok = 2000 <= int(v) <= 2025 + (0 if no_futures_year else (1 if futures else 0))
        else:
            vok = False
        if m is None:
            sok = False
        else:
            acc = {n(m["expected_supplier"])}
            if (futures or alt_on_stock) and n(m["alternate_supplier"]):
                acc.add(n(m["alternate_supplier"]))
            sok = sup in acc
        f = []
        if low and not exempt:
            f.append("MARGIN_TOO_LOW")
            s = landed * (1 + minimum / 100) - selling
            short += Decimal(str(round(float(s), 2))) if float_round else s.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not vok:
            f.append("VINTAGE_INVALID")
        if not sok:
            f.append("SUPPLIER_MISMATCH")
        for c in f:
            counts[c] += 1
        out.append((wid, "|".join(f) or "compliant"))
    wine_count = len(lines) if count_lines else len(out)
    results = {"wine_count": wine_count, "margin_too_low_count": counts["MARGIN_TOO_LOW"],
               "vintage_invalid_count": counts["VINTAGE_INVALID"], "supplier_mismatch_count": counts["SUPPLIER_MISMATCH"],
               "compliant_count": sum(1 for _, x in out if x == "compliant"), "margin_shortfall_total": float(short)}
    return out, results


def score(out, results):
    ws = Path(tempfile.mkdtemp())
    (ws / "wine_findings.csv").write_text("wine_id,finding\n" + "".join(f"{w},{f}\n" for w, f in out))
    (ws / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    shutil.copy(TASK / "solution" / "files" / "wine_memo.md", ws / "wine_memo.md")
    proc = subprocess.run([sys.executable, str(TASK / "tests" / "score.py")],
                          env={"HARBOR_TASK_WORKSPACE": str(ws), "PATH": "/usr/local/bin:/usr/bin:/bin"},
                          capture_output=True, text=True)
    d = json.loads(proc.stdout)
    detail = [c["detail"][:90] for c in d["checks"] if not c["passed"]]
    return d["reward"], d["core_failures"], detail


PROBES = {
    "faithful solver (must be 1.0)": {},
    "flat 20 pct minimum for every category": {"flat20": True},
    "margin on purchase price, freight ignored": {"no_freight": True},
    "freight of the manifest's expected supplier": {"expected_freight": True},
    "first line per wine wins (status ignored)": {"first_wins": True},
    "last line per wine wins (status ignored)": {"last_wins": True},
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
}

if __name__ == "__main__":
    bad = 0
    for name, kw in PROBES.items():
        reward, core, detail = score(*solve(**kw))
        flag = "" if (reward == 1.0) == (name.startswith("faithful")) else "   <-- UNEXPECTED"
        bad += bool(flag)
        print(f"{reward:<5} {name:52s} {core} {detail[:1]}{flag}")
    sys.exit(1 if bad else 0)
