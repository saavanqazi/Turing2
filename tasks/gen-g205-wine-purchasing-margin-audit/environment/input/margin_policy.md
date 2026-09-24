# Wine margin policy WM-STD-1 — review year 2025 (revision 3)

Binding for the 2025 margin review. The review reads four sources: the stock ledger
`stock_movements.csv` (every stock movement posted in 2025), `opening_stock.csv` (the cost
layers on hand at 1 January 2025), the manifest `wine_manifest.csv` and the supplier terms
`supplier_terms.csv`. Where a source and this policy disagree, the policy decides.

## S0 — scope and reading conventions

- Every wine in the manifest is reviewed, once, whether or not it moved in 2025.
- Identifiers, supplier names, movement types, categories and `wine_type` values are compared
  after trimming surrounding whitespace and without regard to letter case. No other
  normalisation is applied: two names that differ in any other character are different names.
- Quantities are bottles. Prices and costs are per bottle; selling prices are net of VAT.
- A wine is a futures (pre-arrival) wine when its manifest `wine_type` is `futures`; every
  other wine is a stock wine.

## S1 — stock is costed first in, first out, at landed cost

- Every `RECEIPT` creates a cost layer holding its bottles at landed cost: its `unit_price`
  plus the freight per bottle of the receipt's supplier from `supplier_terms.csv`, on the
  basis stated there (a `per case` figure is divided by that supplier's `case_size`).
- Every row of `opening_stock.csv` is a cost layer dated its `layer_date`, holding its bottles
  at the `unit_cost` shown, which is already a landed cost.
- Movements take effect in order of `posted_date`. Movements with the same `posted_date` take
  effect in ascending `movement_id` order.
- A `SALE` or a `WRITE_OFF` takes its bottles from stock oldest layer first.
- A `SUPPLIER_RETURN` takes its bottles from the layer created by the receipt named in its
  `reference`, whatever that layer's age.
- A `CUSTOMER_RETURN` brings back bottles from the sale named in its `reference`. The returned
  bottles are the bottles that sale took last. They re-enter stock as a new layer dated the
  return's `posted_date`, at the cost at which they left.

## WM1 — the realized margin must meet the category minimum

- Net revenue of a wine = the bottles of each sale × that sale's `unit_price`, less the
  bottles of each customer return × the `unit_price` of the sale it reverses.
- Cost of bottles sold = the cost of the bottles sales took from stock, less the cost of the
  bottles customer returns brought back. Bottles written off or returned to a supplier leave
  stock at their layer's cost but are not part of the cost of bottles sold.
- Realized margin = (net revenue − cost of bottles sold) ÷ cost of bottles sold, expressed in
  percent and compared unrounded. It exists only for a wine with more bottles sold than
  returned by customers in 2025; a wine without one has no WM1 finding.
- The minimum depends on the wine's manifest category:

  | category | minimum margin |
  |---|---|
  | still | 20 pct |
  | sparkling | 25 pct |
  | fortified | 18 pct |

- A realized margin strictly below the minimum is a `MARGIN_TOO_LOW` finding.
- Exemption: a futures wine whose manifest allocation is `open` is priced for allocation, not
  margin, and is exempt from WM1. A futures wine whose allocation is `closed` is treated as a
  stock wine for WM1. The exemption applies to WM1 only.

## WM2 — every 2025 receipt must carry a valid vintage

- A stock wine's receipts must carry a year from 2000 through 2025 inclusive. A futures wine's
  receipts may additionally carry 2026, since it is sold before bottling.
- `NV` (non-vintage) is valid only where the manifest sets `nv_allowed` to `Y`. Anywhere else
  `NV`, or any value that is not a four-digit year, is invalid.
- A wine with any 2025 receipt failing this rule is a `VINTAGE_INVALID` finding.

## WM3 — every 2025 receipt must come from the manifest's supplier

- A stock wine's receipts must name the manifest's `expected_supplier`.
- A futures wine's receipts may name either the `expected_supplier` or, where one is listed,
  the `alternate_supplier`. The alternate is never acceptable on a stock wine.
- A wine with any 2025 receipt failing this rule is a `SUPPLIER_MISMATCH` finding.

## Finding codes and derived figures

- Codes: `MARGIN_TOO_LOW`, `VINTAGE_INVALID`, `SUPPLIER_MISMATCH`. A wine may carry more than
  one, joined with `|` in that order. A wine with no finding is `compliant`.
- Margin shortfall of a `MARGIN_TOO_LOW` wine = cost of bottles sold × (1 + minimum ÷ 100) −
  net revenue, rounded to the cent, half a cent rounding up. The margin shortfall total is the
  sum of those shortfalls.
- The cost of bottles sold total is the sum of every reviewed wine's cost of bottles sold, to
  the cent.
