# Wine margin policy WM-STD-1 — review year 2025

Binding for the purchasing table `wine_purchases.csv`. Where a wine line, the manifest
(`wine_manifest.csv`) or the supplier terms (`supplier_terms.csv`) and this policy disagree,
the policy decides. The buyer's notes (`purchasing_notes.md`) record changes to the manifest
or to a supplier's terms made during the review year. The manifest names each wine's category, its expected supplier and, where
one is approved, an alternate supplier; for a futures wine it also records whether the
allocation is `open` or `closed`. The supplier terms give each supplier's freight.

## S0 — scope and reading conventions

- The review covers one wine per `wine_id`. A wine may appear on several lines of the
  purchasing table: only the line whose `po_status` is `active` governs the wine. Lines
  marked `superseded` or `void` are not read for any rule.
- A manifest entry is in force from its `effective_from` date. Where a wine has more than one
  manifest entry, the entry with the latest `effective_from` on or before the last day of the
  review year (31 December 2025) governs; an entry dated after that day is not in force for
  this review.
- A note in `purchasing_notes.md` takes effect for purchasing lines dated (`po_date`) on or
  after the note's date, and for those lines it prevails over the manifest and the supplier
  terms. Lines dated before the note are read against the manifest and the terms as they stand.
- Every rule in this policy is stated per bottle. A purchasing line gives its prices for the
  pack on that line (`pack`, bottles × bottle size); supplier freight is given on the basis the
  terms state.
- Identifiers, supplier names, categories, status values and `wine_type` values are compared
  after trimming surrounding whitespace and without regard to letter case. No other
  normalisation is applied: two names that differ in any character are different names.
- A wine is a futures (pre-arrival) wine when its `wine_type` is `futures`; every other
  wine is a stock wine.

## WM1 — the margin must meet the category minimum

- Landed cost per bottle = purchase price per bottle + the freight per bottle of the supplier
  named on the wine's governing line, taken from `supplier_terms.csv`.
- Margin = (selling price per bottle − landed cost) ÷ landed cost, expressed in percent and
  compared unrounded.
- The minimum depends on the wine's category in the manifest:

  | category | minimum margin |
  |---|---|
  | still | 20 pct |
  | sparkling | 25 pct |
  | fortified | 18 pct |

- A wine whose margin is strictly below its minimum is a `MARGIN_TOO_LOW` finding. A margin
  exactly at the minimum is not a finding.
- Exemption: a futures wine whose manifest allocation is `open` is priced for allocation, not
  margin, and is exempt from WM1. A futures wine whose allocation is `closed` is treated as a
  stock wine for WM1. The exemption applies to WM1 only.

## WM2 — the vintage must be a valid year

- A stock wine's `vintage` must be a year from 2000 through the review year (2025)
  inclusive. A futures wine may additionally carry the year after the review year (2026),
  since it is sold before bottling.
- `NV` (non-vintage) is valid only where the manifest sets `nv_allowed` to `Y`. Anywhere
  else `NV`, or any value that is not a four-digit year, is invalid.
- A wine failing this rule is a `VINTAGE_INVALID` finding, independent of the margin and
  supplier.

## WM3 — the supplier must match the manifest

- A stock wine's `supplier` must equal the manifest's `expected_supplier`.
- A futures wine's `supplier` may equal either the `expected_supplier` or, where one is
  listed, the `alternate_supplier`. The alternate is never acceptable on a stock wine.
- A wine with no manifest entry has no expected supplier and is a `SUPPLIER_MISMATCH`.
- A wine failing this rule is a `SUPPLIER_MISMATCH` finding, independent of the margin and
  vintage.

## Finding codes and derived figures

- Codes: `MARGIN_TOO_LOW`, `VINTAGE_INVALID`, `SUPPLIER_MISMATCH`. A wine may carry more than
  one, joined with `|` in that order. A wine with no finding is `compliant`.
- Margin shortfall of a `MARGIN_TOO_LOW` wine = landed cost per bottle × (1 + minimum ÷ 100) −
  selling price per bottle, rounded to the cent, half a cent rounding up. The margin shortfall
  total is the sum of the shortfalls of every `MARGIN_TOO_LOW` wine.
