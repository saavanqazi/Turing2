# Task

I am closing the 2025 margin review for the wine purchasing and margin dashboard and need to know which wines fall short of the margin policy on what we actually earned this year. I have exported the year's stock ledger and the cost layers we carried in from 2024, and put them next to the wine manifest, the supplier freight terms and policy WM-STD-1. Please review every wine in the manifest against the policy, costing stock the way the policy says. Give me `wine_findings.csv`, one row per wine with its finding, and `wine_memo.md` explaining each finding and calling out the wines whose realized margin is under the minimum but which the policy still leaves compliant. Then put the headline figures in `results.json`. File layout is in `input/submission_format.md`.

---
Save your deliverables into your current working directory using exactly these filenames:
    - `wine_findings.csv` — One row per wine with its finding
    - `wine_memo.md` — Markdown memo on the findings and the exemptions
    - `results.json` — a JSON object with the keys `wine_count`, `margin_too_low_count`, `vintage_invalid_count`, `supplier_mismatch_count`, `compliant_count`, `margin_shortfall_total`, `cost_of_bottles_sold_total`
- The exact headers, key sets, allowed values and worked examples are specified in `input/submission_format.md` — follow it precisely.
- Writing those files is the required deliverable and must be your final action; confirm each one exists before you answer.

---

## Working environment

- Your current working directory is `/app`, and it is writable.
- The read-only attachments referred to as `input/` are at `/app/input`.
- Write every deliverable into `/app`, at the exact filenames listed above.
