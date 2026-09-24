# Task

I am refreshing the wine purchasing and margin dashboard ahead of the 2025 review and I need the list of wines that do not meet the margin policy. The purchasing export is `wine_purchases.csv`; I have put the policy (WM-STD-1), the wine manifest, the supplier freight terms and my running notes from the year alongside it. Please review every wine against the policy, treating it as the authority on margin, vintage and supplier and reading the manifest, the terms and my notes the way the policy says to. Give me `wine_findings.csv`, one row per wine with its finding, and `wine_memo.md` explaining each finding and calling out the wines a plain margin check would flag that the policy does not. Then put the headline figures in `results.json`. File layout is in `input/submission_format.md`.

---
Save your deliverables into your current working directory using exactly these filenames:
    - `wine_findings.csv` — One row per wine with its finding
    - `wine_memo.md` — Markdown memo on the findings and the exemptions
    - `results.json` — a JSON object with the keys `wine_count`, `margin_too_low_count`, `vintage_invalid_count`, `supplier_mismatch_count`, `compliant_count`, `margin_shortfall_total`
- The exact headers, key sets, allowed values and worked examples are specified in `input/submission_format.md` — follow it precisely.
- Writing those files is the required deliverable and must be your final action; confirm each one exists before you answer.

---

## Working environment

- Your current working directory is `/app`, and it is writable.
- The read-only attachments referred to as `input/` are at `/app/input`.
- Write every deliverable into `/app`, at the exact filenames listed above.
