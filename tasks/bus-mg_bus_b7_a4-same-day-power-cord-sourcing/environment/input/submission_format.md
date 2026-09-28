# Submission format

Deliver exactly these files, in your working directory:

- `offer_evaluation.csv` — One row per same-day offer: whether it is eligible, why not where it is not, and what it lands at where it is.
- `results.json` — a JSON object; see below.

## `offer_evaluation.csv`

Header, exactly: `offer_id,decision,reason_code,landed_cost_usd`
One row per record, keyed by `offer_id`.
`decision` takes exactly one of: `ELIGIBLE`, `INELIGIBLE`.
One row per offer in `same_day_offers.csv`, in any order, with `offer_id` as that file writes it: `decision` is `ELIGIBLE` or `INELIGIBLE`, `reason_code` is the first clause of the terms that refuses the offer and `NONE` for an offer nothing refuses, and `landed_cost_usd` is what the buyer pays for an eligible offer, in US dollars to the cent, and is left empty on an offer that is not eligible.
`reason_code` takes exactly one of: `FIT_CONNECTOR`, `FIT_POLARIZED`, `FIT_GAUGE`, `FIT_LENGTH`, `NO_STOCK`, `STOCK_RESERVE`, `SAME_DAY_SUSPENDED`, `CUTOFF_PASSED`, `OUT_OF_RADIUS`, `NONE`.
Write `landed_cost_usd` as plain numbers: no thousands separators, no currency symbols, and no more decimal places than the source data carries (`6` or `6.0`, never `6,000` or `$6`).

Example (placeholder values):

```
offer_id,decision,reason_code,landed_cost_usd
OF-00,ELIGIBLE,NONE,1.00
```

## `results.json`

A JSON object with exactly these keys and nothing else:

- `eligible_offer_count` — number
- `chosen_offer_id` — string
- `chosen_total_usd` — number

Shape example (placeholder values):

```json
{
  "eligible_offer_count": 0,
  "chosen_offer_id": "",
  "chosen_total_usd": 0
}
```

`eligible_offer_count` is the number of offers the terms leave eligible; `chosen_offer_id` is the `offer_id` of the eligible offer the terms say to take; `chosen_total_usd` is that offer's landed cost in US dollars to the cent.
