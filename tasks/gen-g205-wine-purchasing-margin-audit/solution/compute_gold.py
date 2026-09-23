#!/usr/bin/env python3
"""compute_gold.py — derive the gold deliverables for this task from environment/input/.

Single source of truth for the answer. Run it after ANY change to the inputs or the
policy; it rewrites
  solution/files/wine_findings.csv
  solution/files/wine_memo.md
  solution/files/results.json
  solution/golden_trajectory.json      (heredoc replay of the gold files)
  tests/verifier.json + tests/manifest.json   (expected rows / row_set / results keys only)
and prints the derivation per wine so the answer can be checked by hand.

Rules implemented are margin_policy.md WM1–WM3 verbatim. Nothing here is heuristic.
Stdlib only: the task image carries no pandas.
"""
from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
FILES = TASK / "solution" / "files"
# The graders load tests/verifier.json; the delivery format also wants tests/manifest.json.
# Both are written with identical content (the accepted bundles ship both).
SPEC_PATH = TASK / "tests" / "verifier.json"
MIRROR_PATH = TASK / "tests" / "manifest.json"

# ---- policy constants (margin_policy.md) -----------------------------------------
MIN_MARGIN_PCT = Decimal("20")          # WM1: margin below this is MARGIN_TOO_LOW
EXEMPT_TYPES = {"futures"}              # WM1: futures (pre-arrival) wines are exempt
VINTAGE_MIN, VINTAGE_MAX = 2000, 2025   # WM2: inclusive
CODES = ["MARGIN_TOO_LOW", "VINTAGE_INVALID", "SUPPLIER_MISMATCH"]  # join order WM1, WM2, WM3
COUNT_KEYS = {"MARGIN_TOO_LOW": "margin_too_low_count",
              "VINTAGE_INVALID": "vintage_invalid_count",
              "SUPPLIER_MISMATCH": "supplier_mismatch_count"}
TRAP_IDS: list[str] = []  # filled at run time: wines below the minimum that the policy leaves compliant


def rows(name: str) -> list[dict[str, str]]:
    with (INPUT / name).open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def margin_pct(purchase: Decimal, selling: Decimal) -> Decimal:
    return (selling - purchase) / purchase * 100


def main() -> int:
    manifest = {r["wine_id"].strip(): r for r in rows("wine_manifest.csv")}
    purchases = rows("wine_purchases.csv")

    # one wine = one wine_id (a repeated line is still one wine); first line governs today
    wines: dict[str, dict[str, str]] = {}
    for r in purchases:
        wid = r["wine_id"].strip()
        wines.setdefault(wid, r)

    out, counts = [], {k: 0 for k in COUNT_KEYS.values()}
    memo_rows, trap_rows = [], []
    for wid, r in wines.items():
        purchase, selling = Decimal(r["purchase_price"]), Decimal(r["selling_price"])
        m = margin_pct(purchase, selling)
        wtype = r["wine_type"].strip().lower()
        vintage = int(r["vintage"])
        supplier = r["supplier"].strip()
        expected = manifest.get(wid, {}).get("expected_supplier", "").strip()

        findings = []
        low = m < MIN_MARGIN_PCT
        if low and wtype not in EXEMPT_TYPES:
            findings.append("MARGIN_TOO_LOW")
        if not (VINTAGE_MIN <= vintage <= VINTAGE_MAX):
            findings.append("VINTAGE_INVALID")
        if supplier.casefold() != expected.casefold():
            findings.append("SUPPLIER_MISMATCH")
        finding = "|".join(findings) if findings else "compliant"
        for code in findings:
            counts[COUNT_KEYS[code]] += 1
        out.append({"wine_id": wid, "finding": finding})

        reasons = []
        if "MARGIN_TOO_LOW" in findings:
            reasons.append(f"{r['wine_name']} at {m:.1f} pct margin, below the {MIN_MARGIN_PCT} pct minimum")
        if "VINTAGE_INVALID" in findings:
            reasons.append(f"{vintage} vintage outside {VINTAGE_MIN}-{VINTAGE_MAX}")
        if "SUPPLIER_MISMATCH" in findings:
            reasons.append(f"supplier {supplier} where the manifest expects {expected or 'no supplier on file'}")
        if findings:
            memo_rows.append((wid, finding, "; ".join(reasons)))
        if low and wtype in EXEMPT_TYPES:
            trap_rows.append((wid, r["wine_name"], m))
        print(f"{wid}: {r['wine_name']:10s} margin={m:.2f}% type={wtype:7s} vintage={vintage} "
              f"supplier={supplier}/{expected} -> {finding}")

    TRAP_IDS[:] = [t[0] for t in trap_rows]
    wine_count = len(out)
    compliant = sum(1 for r in out if r["finding"] == "compliant")
    results = {"wine_count": wine_count, **counts, "compliant_count": compliant}
    print(f"\n{results}\ntrap wines (low margin, exempt): {TRAP_IDS}")

    # ---- write gold files ------------------------------------------------------------
    FILES.mkdir(parents=True, exist_ok=True)
    header = ["wine_id", "finding"]
    csv_text = ",".join(header) + "\n" + "".join(",".join(r[h] for h in header) + "\n" for r in out)
    (FILES / "wine_findings.csv").write_text(csv_text, encoding="utf-8")
    json_text = json.dumps(results, indent=2) + "\n"
    (FILES / "results.json").write_text(json_text, encoding="utf-8")

    memo = ["# Wine purchasing and margin audit", "",
            f"{wine_count} wines reviewed against WM-STD-1. {wine_count - compliant} carry a finding "
            f"and {compliant} are compliant.", "",
            "| Wine | Code | Why |", "|---|---|---|"]
    memo += [f"| `{w}` | {c} | {why} |" for w, c, why in memo_rows]
    memo += ["", "## Wines that look low-margin and are not findings", ""]
    for wid, name, m in trap_rows:
        memo.append(f"`{wid}` ({name}) is a futures wine at {m:.1f} pct margin. WM1 exempts futures "
                    f"(pre-arrival) wines from the margin minimum, so it is compliant. Reporting it "
                    f"would overstate the finding count.")
    memo_text = "\n".join(memo) + "\n"
    (FILES / "wine_memo.md").write_text(memo_text, encoding="utf-8")

    # ---- golden trajectory (heredoc replay) ------------------------------------------
    reads = ["wine_purchases.csv", "wine_manifest.csv", "margin_policy.md", "submission_format.md"]
    steps = [{"name": "bash", "server": "local", "arguments": {"command": f"cat input/{f}"}} for f in reads]
    for fname, text, tag in (("wine_findings.csv", csv_text, "FINDINGSEOF"),
                             ("wine_memo.md", memo_text, "MEMOEOF"),
                             ("results.json", json_text, "RESULTSEOF")):
        steps.append({"name": "bash", "server": "local",
                      "arguments": {"command": f"cat > {fname} << '{tag}'\n{text}{tag}"}})
    steps.append({"name": "bash", "server": "local",
                  "arguments": {"command": "ls -la wine_findings.csv wine_memo.md results.json"}})
    (TASK / "solution" / "golden_trajectory.json").write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")

    # ---- verifier expected values ----------------------------------------------------
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    by_id = {r["wine_id"]: {"finding": r["finding"]} for r in out}
    all_ids = [r["wine_id"] for r in out]
    flagged = [w for w, _, _ in memo_rows]
    for v in spec["verifiers"]:
        exp = v["assertion"].get("expected")
        if v["name"] == "findings_table_trap":
            exp["rows"] = {i: by_id[i] for i in TRAP_IDS}
        elif v["name"] == "findings_table":
            exp["rows"] = {i: by_id[i] for i in all_ids if i not in TRAP_IDS}
            exp["row_set"] = all_ids
            v["metadata"]["how_justification"] = re.sub(
                r"every graded cell of \d+ rows plus the full \d+-row population",
                f"every graded cell of {len(all_ids) - len(TRAP_IDS)} rows plus the full {len(all_ids)}-row population",
                v["metadata"]["how_justification"])
        elif v["name"] == "results_figures":
            for key, value in results.items():
                exp["keys"][key]["value"] = value
        elif v["name"] == "memo_names_trap":
            v["assertion"]["expected"] = "(?s)" + "".join(rf"(?=.*\b{re.escape(i)}\b)" for i in TRAP_IDS)
        elif v["name"] == "memo_names_findings":
            v["assertion"]["expected"] = "(?s)" + "".join(rf"(?=.*\b{re.escape(i)}\b)" for i in flagged)
    text = json.dumps(spec, indent=2) + "\n"
    SPEC_PATH.write_text(text, encoding="utf-8")
    MIRROR_PATH.write_text(text, encoding="utf-8")
    print(f"\nwrote {FILES}, golden_trajectory.json, {SPEC_PATH.name} + {MIRROR_PATH.name} (identical)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
