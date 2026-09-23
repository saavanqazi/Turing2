# Task

We are running the wine purchasing and margin dashboard and I want to know which wines do not meet the margin policy. Go through `wine_purchases.csv` against WM-STD-1 and the manifest in `wine_manifest.csv`, using the policy as the authority for margin, vintage and supplier. Save `wine_findings.csv` with the columns `wine_id,finding`, one row per wine, where `finding` is `compliant`, `MARGIN_TOO_LOW`, `VINTAGE_INVALID` or `SUPPLIER_MISMATCH` (joined with `|` if more than one). Then write `wine_memo.md` explaining each finding and the wine that looks low-margin and is not. File layout is in `input/submission_format.md`.

---
Save your deliverables into your current working directory using exactly these filenames:
    - `wine_findings.csv` — One row per wine with its finding
    - `wine_memo.md` — Markdown memo on the findings and the trap
    - `results.json` — a JSON object with the keys `wine_count`, `margin_too_low_count`, `vintage_invalid_count`, `supplier_mismatch_count`, `compliant_count`
- The exact headers, key sets, allowed values and worked examples are specified in `input/submission_format.md` — follow it precisely.
- Writing those files is the required deliverable and must be your final action; confirm each one exists before you answer.

---

## Working environment

- Your current working directory is `/app`, and it is writable.
- The read-only attachments referred to as `input/` are at `/app/input`.
- Write every deliverable into `/app`, at the exact filenames listed above.
