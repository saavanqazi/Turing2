#!/usr/bin/env python3
"""compute_gold.py — derive the gold deliverables for this task from environment/input/.

Single source of truth for the answer (the rules live in ledger_engine.py). Run it after ANY
change to the inputs or the policy; it rewrites
  solution/files/wine_findings.csv
  solution/files/wine_memo.md
  solution/files/results.json
  solution/golden_trajectory.json      (heredoc replay of the gold files)
  tests/verifier.json + tests/manifest.json   (expected rows / row_set / results keys only)
and prints the derivation per wine so the answer can be checked by hand. Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_engine import evaluate, results_of  # noqa: E402

TASK = Path(__file__).resolve().parents[1]
INPUT = TASK / "environment" / "input"
FILES = TASK / "solution" / "files"
# The graders load tests/verifier.json; the delivery format also wants tests/manifest.json.
# Both are written with identical content (the accepted bundles ship both).
SPEC_PATH = TASK / "tests" / "verifier.json"
MIRROR_PATH = TASK / "tests" / "manifest.json"


def main() -> int:
    wines = evaluate(INPUT)
    results = results_of(wines)
    for w in wines:
        m = f"{w.margin:7.2f}%" if w.margin is not None else "    n/a"
        print(f"{w.wine_id}: {w.name[:26]:26s} {w.category:9s} rev={w.revenue:9.2f} cogs={w.cogs:9.2f} "
              f"margin={m} min={w.minimum} {'futures' if w.futures else 'stock':7s} "
              f"{'exempt' if w.exempt else '':6s} -> {w.finding}")
        assert w.oversold == 0, w.wine_id
    trap_ids = [w.wine_id for w in wines if w.low and w.exempt]
    flagged = [w.wine_id for w in wines if w.findings]
    print(f"\n{results}\nexempt low-margin wines: {trap_ids}")

    # ---- gold files ----------------------------------------------------------------------
    FILES.mkdir(parents=True, exist_ok=True)
    header = ["wine_id", "finding"]
    csv_text = "wine_id,finding\n" + "".join(f"{w.wine_id},{w.finding}\n" for w in wines)
    (FILES / "wine_findings.csv").write_text(csv_text, encoding="utf-8")
    json_text = json.dumps(results, indent=2) + "\n"
    (FILES / "results.json").write_text(json_text, encoding="utf-8")

    n, c = results["wine_count"], results["compliant_count"]
    memo = ["# Wine margin review — 2025 (WM-STD-1 rev. 3)", "",
            f"{n} manifest wines reviewed. Stock costed first in, first out at landed cost from the 2024 opening "
            f"layers and the 2025 ledger taken in posted-date order. {n - c} wines carry a finding and {c} are "
            f"compliant. Cost of bottles sold {results['cost_of_bottles_sold_total']:.2f}; margin shortfall total "
            f"{results['margin_shortfall_total']:.2f}.", "",
            "## Findings", ""]
    memo += [f"- `{w.wine_id}` ({w.name}) — {w.finding}: {'; '.join(w.reasons)}" for w in wines if w.findings]
    memo += ["", "## Wines under their minimum that the policy leaves compliant", ""]
    for w in wines:
        if w.low and w.exempt:
            memo.append(f"- `{w.wine_id}` ({w.name}): realized margin {w.margin:.2f} pct against a {w.minimum} pct "
                        f"minimum, but it is a futures (pre-arrival) wine with an open allocation, which WM1 exempts "
                        f"from the margin minimum. The exemption covers WM1 only.")
    memo_text = "\n".join(memo) + "\n"
    (FILES / "wine_memo.md").write_text(memo_text, encoding="utf-8")

    # ---- golden trajectory (heredoc replay) ----------------------------------------------
    reads = ["margin_policy.md", "submission_format.md", "wine_manifest.csv", "supplier_terms.csv",
             "opening_stock.csv", "stock_movements.csv"]
    steps = [{"name": "bash", "server": "local", "arguments": {"command": f"cat input/{f}"}} for f in reads]
    for fname, text, tag in (("wine_findings.csv", csv_text, "FINDINGSEOF"),
                             ("wine_memo.md", memo_text, "MEMOEOF"),
                             ("results.json", json_text, "RESULTSEOF")):
        steps.append({"name": "bash", "server": "local",
                      "arguments": {"command": f"cat > {fname} << '{tag}'\n{text}{tag}"}})
    steps.append({"name": "bash", "server": "local",
                  "arguments": {"command": "ls -la wine_findings.csv wine_memo.md results.json"}})
    (TASK / "solution" / "golden_trajectory.json").write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")

    # ---- verifier expected values --------------------------------------------------------
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    by_id = {w.wine_id: {"finding": w.finding} for w in wines}
    all_ids = [w.wine_id for w in wines]
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
