# Submission format

Deliver exactly these files, in your working directory:

- `wine_findings.csv` — one row per wine with its finding.
- `wine_memo.md` — a Markdown memo on the findings and on any wine that looks low-margin but is not a finding.
- `results.json` — a JSON object; see below.

## `wine_findings.csv`

Header, exactly: `wine_id,finding`
One row per wine, keyed by `wine_id` as `wine_purchases.csv` writes it, in any order. A wine
that appears on more than one line of the purchases file is still one wine and one row.
`finding` is `compliant` for a wine with no finding, otherwise the finding codes the wine
carries, joined with `|` and written in policy order: `MARGIN_TOO_LOW`, then
`VINTAGE_INVALID`, then `SUPPLIER_MISMATCH` (for example `MARGIN_TOO_LOW|SUPPLIER_MISMATCH`).
`finding` takes only those codes and `compliant`; write nothing else in the cell.

Example (placeholder values):

```
wine_id,finding
W-00,compliant
```

## `wine_memo.md`

Free-form Markdown. Name every wine that carries a finding, with the code and the reason, and
name every wine whose margin is below the minimum but which the policy leaves compliant, with
the clause that exempts it.

## `results.json`

A JSON object with exactly these keys and nothing else:

- `wine_count` — number: distinct wines reviewed (one per `wine_id`)
- `margin_too_low_count` — number: wines carrying `MARGIN_TOO_LOW`
- `vintage_invalid_count` — number: wines carrying `VINTAGE_INVALID`
- `supplier_mismatch_count` — number: wines carrying `SUPPLIER_MISMATCH`
- `compliant_count` — number: wines carrying no finding

A wine carrying two codes counts once under each code. The three code counts therefore need
not sum to `wine_count - compliant_count`.

Shape example (placeholder values):

```json
{
  "wine_count": 0,
  "margin_too_low_count": 0,
  "vintage_invalid_count": 0,
  "supplier_mismatch_count": 0,
  "compliant_count": 0
}
```
