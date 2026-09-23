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

Rules implemented are margin_policy.md S0, WM1–WM3 and the derived-figure clause verbatim.
Nothing here is heuristic. Stdlib only: the task image carries no pandas.
"""
from __future__ import annotations

import csv
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
FILES = TASK / "solution" / "files"
# The graders load tests/verifier.json; the delivery format also wants tests/manifest.json.
# Both are written with identical content (the accepted bundles ship both).
SPEC_PATH = TASK / "tests" / "verifier.json"
MIRROR_PATH = TASK / "tests" / "manifest.json"

# ---- policy constants (margin_policy.md) -----------------------------------------
REVIEW_YEAR = 2025
VINTAGE_MIN = 2000
MIN_MARGIN = {"still": Decimal("20"), "sparkling": Decimal("25"), "fortified": Decimal("18")}  # WM1 table
CODES = ["MARGIN_TOO_LOW", "VINTAGE_INVALID", "SUPPLIER_MISMATCH"]  # join order WM1, WM2, WM3
COUNT_KEYS = {"MARGIN_TOO_LOW": "margin_too_low_count",
              "VINTAGE_INVALID": "vintage_invalid_count",
              "SUPPLIER_MISMATCH": "supplier_mismatch_count"}
CENT = Decimal("0.01")


def norm(value: str | None) -> str:
    """S0: trim surrounding whitespace, ignore letter case. Nothing else."""
    return (value or "").strip().casefold()


def rows(name: str) -> list[dict[str, str]]:
    with (INPUT / name).open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def main() -> int:
    # S0: the manifest entry with the latest effective_from on or before 31 Dec of the review year governs
    manifest: dict[str, dict[str, str]] = {}
    for r in rows("wine_manifest.csv"):
        if r["effective_from"].strip() > f"{REVIEW_YEAR}-12-31":
            continue
        key = norm(r["wine_id"])
        if key not in manifest or r["effective_from"].strip() > manifest[key]["effective_from"].strip():
            manifest[key] = r
    # S0/WM1: freight per bottle on the basis the terms state
    freight: dict[str, Decimal] = {}
    for r in rows("supplier_terms.csv"):
        basis = norm(r["freight_basis"])
        per_bottle = Decimal(r["freight"]) / (Decimal(r["case_size"]) if basis == "per case" else 1)
        assert basis in ("per bottle", "per case"), r
        freight[norm(r["supplier"])] = per_bottle

    # S0: one wine per wine_id; the active line governs; superseded/void lines are not read;
    # a repeated line is the same line. Output ids are written as the purchases file writes them.
    governing: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for r in rows("wine_purchases.csv"):
        key = norm(r["wine_id"])
        if key not in order:
            order.append(key)
        if norm(r["po_status"]) == "active":
            assert key not in governing or governing[key] == r, f"two distinct active lines for {r['wine_id']}"
            governing[key] = r
    assert set(order) == set(governing), f"wines with no active line: {set(order) - set(governing)}"

    out, counts = [], {k: 0 for k in COUNT_KEYS.values()}
    memo_rows, trap_rows = [], []
    shortfall_total = Decimal("0")
    for key in order:
        r = governing[key]
        wid = r["wine_id"].strip()
        m = manifest.get(key)
        bottles = Decimal(re.fullmatch(r"(\d+)x\d+cl", r["pack"].strip()).group(1))  # S0: prices are per pack
        purchase, selling = Decimal(r["purchase_price"]) / bottles, Decimal(r["selling_price"]) / bottles
        supplier = norm(r["supplier"])
        futures = norm(r["wine_type"]) == "futures"
        category = norm(m["category"]) if m else ""
        allocation = norm(m["allocation"]) if m else ""

        # WM1 — landed cost on the invoiced supplier's freight; category minimum; open-futures exempt
        landed = purchase + freight[supplier]
        margin = (selling - landed) / landed * 100
        minimum = MIN_MARGIN[category] if category else None
        exempt = futures and allocation == "open"
        low = minimum is not None and margin < minimum

        # WM2 — 2000..review year for stock, +1 for futures; NV only where nv_allowed = Y
        vintage_text = r["vintage"].strip()
        if vintage_text.upper() == "NV":
            vintage_ok = bool(m) and norm(m["nv_allowed"]) == "y"
        elif re.fullmatch(r"\d{4}", vintage_text):
            vintage_ok = VINTAGE_MIN <= int(vintage_text) <= REVIEW_YEAR + (1 if futures else 0)
        else:
            vintage_ok = False

        # WM3 — expected supplier; alternate acceptable on futures only; no manifest entry = mismatch
        if m is None:
            supplier_ok = False
        else:
            acceptable = {norm(m["expected_supplier"])}
            if futures and norm(m["alternate_supplier"]):
                acceptable.add(norm(m["alternate_supplier"]))
            supplier_ok = supplier in acceptable

        findings = []
        if low and not exempt:
            findings.append("MARGIN_TOO_LOW")
        if not vintage_ok:
            findings.append("VINTAGE_INVALID")
        if not supplier_ok:
            findings.append("SUPPLIER_MISMATCH")
        finding = "|".join(findings) if findings else "compliant"
        for code in findings:
            counts[COUNT_KEYS[code]] += 1
        out.append({"wine_id": wid, "finding": finding})

        shortfall = Decimal("0")
        reasons = []
        if "MARGIN_TOO_LOW" in findings:
            shortfall = cents(landed * (1 + minimum / 100) - selling)
            shortfall_total += shortfall
            reasons.append(f"{margin:.2f} pct on a landed cost of {landed:.2f} ({category} minimum {minimum} pct), "
                           f"shortfall {shortfall:.2f}")
        if "VINTAGE_INVALID" in findings:
            limit = REVIEW_YEAR + (1 if futures else 0)
            reasons.append(f"vintage {vintage_text} outside {VINTAGE_MIN}-{limit}" if vintage_text.upper() != "NV"
                           else "NV where the manifest does not allow non-vintage")
        if "SUPPLIER_MISMATCH" in findings:
            reasons.append(f"supplier {r['supplier'].strip()} where the manifest "
                           + (f"expects {m['expected_supplier']}" if m else "has no entry for this wine"))
        if findings:
            memo_rows.append((wid, finding, "; ".join(reasons)))
        if low and exempt:
            trap_rows.append((wid, r["wine_name"], margin, minimum))
        print(f"{wid}: {r['wine_name'][:22]:22s} {category:9s} landed={landed:6.2f} margin={margin:7.2f}% "
              f"min={minimum} type={'futures' if futures else 'stock':7s} alloc={allocation or '-':6s} "
              f"vint={vintage_text:4s} sup={r['supplier'].strip()} -> {finding}")

    trap_ids = [t[0] for t in trap_rows]
    wine_count = len(out)
    compliant = sum(1 for r in out if r["finding"] == "compliant")
    results = {"wine_count": wine_count, **counts, "compliant_count": compliant,
               "margin_shortfall_total": float(shortfall_total)}
    print(f"\n{results}\nexempt low-margin wines: {trap_ids}")

    # ---- write gold files ------------------------------------------------------------
    FILES.mkdir(parents=True, exist_ok=True)
    header = ["wine_id", "finding"]
    csv_text = ",".join(header) + "\n" + "".join(",".join(r[h] for h in header) + "\n" for r in out)
    (FILES / "wine_findings.csv").write_text(csv_text, encoding="utf-8")
    json_text = json.dumps(results, indent=2) + "\n"
    (FILES / "results.json").write_text(json_text, encoding="utf-8")

    memo = ["# Wine purchasing and margin audit — review year 2025", "",
            f"{wine_count} wines reviewed against WM-STD-1 (one per wine_id; only the active purchasing line "
            f"read). {wine_count - compliant} carry a finding and {compliant} are compliant. Margin shortfall "
            f"total {shortfall_total:.2f}.", "",
            "| Wine | Finding | Why |", "|---|---|---|"]
    memo += [f"| `{w}` | {c} | {why} |" for w, c, why in memo_rows]
    memo += ["", "## Wines a plain margin check would flag that the policy does not", ""]
    for wid, name, margin, minimum in trap_rows:
        memo.append(f"- `{wid}` ({name}): {margin:.1f} pct against a {minimum} pct minimum, but it is a futures "
                    f"(pre-arrival) wine with an open allocation, which WM1 exempts from the margin minimum. "
                    f"The exemption covers WM1 only.")
    memo_text = "\n".join(memo) + "\n"
    (FILES / "wine_memo.md").write_text(memo_text, encoding="utf-8")

    # ---- golden trajectory (heredoc replay) ------------------------------------------
    reads = ["margin_policy.md", "wine_purchases.csv", "wine_manifest.csv", "supplier_terms.csv",
             "submission_format.md"]
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
            exp["rows"] = {i: by_id[i] for i in trap_ids}
        elif v["name"] == "findings_table":
            exp["rows"] = {i: by_id[i] for i in all_ids if i not in trap_ids}
            exp["row_set"] = all_ids
            v["metadata"]["how_justification"] = re.sub(
                r"every graded cell of \d+ rows plus the full \d+-row population",
                f"every graded cell of {len(all_ids) - len(trap_ids)} rows plus the full {len(all_ids)}-row population",
                v["metadata"]["how_justification"])
        elif v["name"] == "results_figures":
            for key, value in results.items():
                exp["keys"][key]["value"] = value
        elif v["name"] == "memo_names_trap":
            v["assertion"]["expected"] = "(?s)" + "".join(rf"(?=.*\b{re.escape(i)}\b)" for i in trap_ids)
        elif v["name"] == "memo_names_findings":
            v["assertion"]["expected"] = "(?s)" + "".join(rf"(?=.*\b{re.escape(i)}\b)" for i in flagged)
    text = json.dumps(spec, indent=2) + "\n"
    SPEC_PATH.write_text(text, encoding="utf-8")
    MIRROR_PATH.write_text(text, encoding="utf-8")
    print(f"\nwrote {FILES}, golden_trajectory.json, {SPEC_PATH.name} + {MIRROR_PATH.name} (identical)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
