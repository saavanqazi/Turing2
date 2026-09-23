# Wine margin policy WM-STD-1 — binding for `wine_purchases.csv`

Where a wine and this policy (or the manifest) disagree, the policy decides. The manifest
in `wine_manifest.csv` names each wine's expected supplier.

## WM1 — the margin must meet the minimum

A wine whose margin (`selling_price - purchase_price` over `purchase_price`) is below
20 pct is a `MARGIN_TOO_LOW` finding. **Futures (pre-arrival) wines
(`wine_type = futures`) are exempt**: futures are priced for allocation, not margin, so a
low margin on a futures wine is not a finding. This is the commonest false positive in this
review, and the margin rule is scoped to stock wines for exactly this reason.

## WM2 — the vintage must be a valid year

A wine whose `vintage` is outside 2000-2025 is a
`VINTAGE_INVALID` finding, independent of the margin and supplier.

## WM3 — the supplier must match the manifest

A wine whose `supplier` differs from the manifest's expected supplier is a
`SUPPLIER_MISMATCH` finding, independent of the margin and vintage.

## Finding codes

`MARGIN_TOO_LOW`, `VINTAGE_INVALID`, `SUPPLIER_MISMATCH`. A wine may carry more than one,
joined with `|`. A wine with no finding is `compliant`.
