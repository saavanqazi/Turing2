# Kestrel — same-day fulfilment terms, and what an offer costs

These terms govern every same-day offer the chain shows. The buyer's order is placed at
14:40 on Friday 25 September 2026 in Phenix City, Alabama, and the delivery address for
any same-day delivery is the buyer's address in Phenix City.

## S1 — the same-day cutoff, and the order instant on a store's clock

Same-day collection and same-day delivery both require the order to be placed before the
store's cutoff in the fulfilling store's own local time, which is the timezone the store
directory gives that store and not the buyer's. The cutoff is 15:00 unless a notice in
`store_notices.md` for that store, that day and that method says otherwise, in which case
the notice's time is the cutoff for that method. A store's own local time is what its
`timezone` column says, whatever time it is where the buyer is standing. The moment the
order is placed, read on that store's clock, is the store's **order instant**; every
clause below that refers to a time at a store means a time on that store's clock.
Collection is ready two hours after the order instant; delivery arrives by 20:00. An
order placed on or after the cutoff cannot be fulfilled same-day by that method, and the
offer is refused `CUTOFF_PASSED`.

## S2 — stock, delivery radius and the delivery fee

The offers file does not carry a stock count. Stock is read from the stores' shared
`stock_ledger.csv`, which the warehouse system writes:

- `item_code` is either the Kestrel `sku` or the supplier's part number as the listings
  give it in `supplier_part`; both refer to the same cord.
- `qty` is always a positive count. The `event` gives the direction: `OPENING`,
  `RECEIPT` and `VOID` add units; `SALE`, `HOLD` and `TRANSFER_OUT` remove them. A `VOID`
  reverses an earlier sale the same day and puts the unit back.
- The `OPENING` line is the store's count at the start of that day's trading and already
  includes everything posted before it. Lines posted before that day's `OPENING` are not
  counted again.
- `posted_local` is the store's own clock.
- Store and item codes are written in whatever case the till sent them; case is not
  significant when matching a ledger line to a store or a cord.

A store's **effective stock** in a cord is the `OPENING` count for the order date plus
every adding line, minus every removing line, for that store and that cord posted after the
`OPENING` and at or before the store's order instant (S1). A line posted after the order
instant on that store's clock has not happened yet and is not counted. Effective stock is
one figure per store and cord, shared by every offer that draws on it.

Either method requires effective stock of at least 1 (`NO_STOCK` where it is not).

A same-day delivery additionally requires the delivery address to be within 10 road
miles of the fulfilling store, measured by the `distance_mi` column (`OUT_OF_RADIUS`
where it is not), and requires effective stock of at least 2, because one unit is held
back as floor stock (`STOCK_RESERVE` where it is not). Collection carries neither of these
two requirements. The delivery fee is USD 9.99; collection is free.

## S3 — suspended stores and paused methods

A store whose `same_day_suspended` flag is set cannot fulfil same-day by either method
while the flag stands. A store whose notice for the day pauses one method cannot fulfil
same-day by that method that day, and the other method is unaffected. Either way the
offer is refused `SAME_DAY_SUSPENDED`.

## S4 — what fits this brick

A cord fits only if all of these hold. Its `connector` must be `C7`, because a `C5`
cloverleaf end will not enter a two-pin inlet (`FIT_CONNECTOR`). It must be
non-polarized, because a keyed C7 will not seat in a non-polarized inlet
(`FIT_POLARIZED`). Its conductor must meet the requirement the brick's data sheet sets
for a cord of its length (`FIT_GAUGE`). Its `length`, as the listings give it, must be no
shorter than the cord it replaces, whose length the data sheet gives, and no longer than
15 feet: the buyer will take any run that reaches the wall without coiling
(`FIT_LENGTH`). Every cord in the
listings is rated at or above the brick's 2.5 A and 125 V, so the rating refuses nothing
here.

## S5 — reason precedence

An offer may fail more than one clause. Report the FIRST that applies, in this order:
`FIT_CONNECTOR`, `FIT_POLARIZED`, `FIT_GAUGE`, `FIT_LENGTH`, `NO_STOCK`, `STOCK_RESERVE`,
`SAME_DAY_SUSPENDED`, `CUTOFF_PASSED`, `OUT_OF_RADIUS`. An offer that fails none of them is
eligible and its reason is `NONE`.

## S6 — landed cost and the choice

An eligible offer's landed cost is the cord's shelf price plus sales tax, rounded to the
cent with a half-cent rounding up, plus the delivery fee where the offer is a delivery.
The fee is not taxed. Which tax rate applies depends on the method: a collection is taxed
at the FULFILLING store's `sales_tax_pct`, and a delivery is taxed at the combined rate
for the DELIVERY ADDRESS's city and state as `tax_jurisdictions.csv` gives it — never at
the store's rate, and never at the buyer's rate for a collection.

Where the store directory carries more than one row for a store, the row in effect is the
one with the latest `effective_from` on or before the order date; earlier rows are history.

Take the eligible offer with the lowest landed cost. Where two offers land at the same
cost, take the one ready earliest; where they are also ready at the same time, take the
nearer store; where they are also equally near, take the lower `offer_id`.

## S7 — what is out of scope

Only the same-day offers listed count. Standard and two-day shipping, back-order,
ship-to-store and transfers between stores do not put the cord in the buyer's hand today
and are never eligible here, whatever they cost.
