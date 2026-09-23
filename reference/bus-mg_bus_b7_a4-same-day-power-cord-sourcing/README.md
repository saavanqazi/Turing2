# Change summary — same-day replacement cord sourcing

> Fill the bracketed figures after the GLM batteries. Everything else is final.

## What the task asks

A buyer in Phenix City, AL needs a 10-ft figure-8 (IEC C7, non-polarized) AC cord the
same Friday afternoon. Twenty Kestrel same-day offers across six stores must each be ruled
eligible or not under the chain's terms, with the first refusing clause and the landed
cost, then the eligible count, the offer to take and its total.

## From the mined baseline to this version

**Baseline (as mined).** 11 offers, 5 stores, 8 SKUs, an `on_hand` count in the offers
file, tax always at the fulfilling store. One real trap (two stores across the Eastern
state line are past the 15:00 cutoff). The `STOCK_RESERVE` clause and every tie-break rule
were dead: no row ever exercised them. Oracle was 1.0 (shipped mined evidence, 2026-09-20,
and a local engine replay). GLM-5.2 baseline battery: 4/4 passing, rewards 1.0, 1.0, 1.0,
1.0 (harbor job glm-b7a4-baseline, terminus-2, on the untouched package at commit b41f537).

**Round 1 (measured 4/4, rewards 1.0, 1.0, 1.0, 1.0; oracle 1.0; harbor jobs
oracle-b7a4-r1, glm-b7a4-r1; terminus-2 harness).**
Stock ledger in store-local time with `NO_STOCK`, destination-based tax on delivery,
a same-cent tie resolved by distance, precedence edge rows. GLM-5.2 scripted every
explicit rule correctly, so round 1 was not enough.

**Round 2 (local battery 3/4; platform battery 4/4, rejected as too easy).** The rules stay explicit, but the data and the sources now punish
the shortcuts a one-pass script takes. Each is governed by a clause in
`same_day_terms.md` so a careful analyst lands on exactly one answer:

1. **Stock is a ledger, not a snapshot.** `stock_ledger.csv` holds opening counts and
   timestamped movements in each store's local time. Effective stock is the sum of lines
   at or before the order instant *on that store's clock*, so the stock figure depends on
   the same timezone reasoning as the cutoff: a 15:20 sale at an Eastern store has
   already happened (OF-01 is `NO_STOCK`, not `CUTOFF_PASSED`), a 15:05 hold at a Central
   store has not (OF-09 keeps its two units). A pickup and a delivery on the same
   store/SKU share one figure. New reason `NO_STOCK`; `STOCK_RESERVE` now fires (OF-13,
   OF-19, the latter ahead of `OUT_OF_RADIUS` by precedence).
2. **Destination-based tax on delivery.** Collection is taxed at the store's rate;
   delivery at the delivery address's rate from `tax_jurisdictions.csv`. OF-17 lands at
   25.89, not the 25.86 a store-rate reading gives.
3. **A real tie.** OF-04 (Opelika) and OF-12 (Smiths Station) both land at 18.62. Both
   are collections ready at the same time, so the nearer store decides: OF-12. Picking
   the first or lowest-id minimum gives OF-04.
4. **Precedence edge rows.** 8-ft 20 AWG (`FIT_GAUGE` before `FIT_LENGTH`), 6-ft 20 AWG
   (gauge rule does not apply at 6 ft, so `FIT_LENGTH`), 20-ft 18 AWG (`FIT_LENGTH`), a
   14:40 sale that counts under "at or before".
5. **Warehouse-shaped ledger.** `qty` is unsigned and the event type gives the
   direction; a `VOID` puts a sold unit back (OF-20 is eligible, not `NO_STOCK`); some
   lines carry the supplier part number instead of the SKU (OF-12's opening count is
   under `VLX-F8-12`, so a missed alias turns the chosen offer into `NO_STOCK`); lines
   posted the day before are already inside Friday's `OPENING` (counting them makes
   OF-13 deliverable).
6. **Store notices override the tables.** Columbus accepts collection until 16:00
   today (OF-18 eligible although 15:40 is past the default cutoff); Smiths Station's
   delivery is paused (OF-17 `SAME_DAY_SUSPENDED` though the directory flag is N).
7. **A stale duplicate store row.** Opelika appears twice with `effective_from`; the
   newer 9.50% row is listed first, so a last-row-wins dictionary keeps the old 9.00%
   and prices OF-04 at 18.53 instead of 18.62.
8. **Money rounding.** PC-1012 at 17.00 with 9.5% tax is exactly 18.615. The terms say
   half-cent rounds up (18.62); `round()` on a float gives 18.61 on the chosen offer.

Round 2 result: 7 eligible of 20, chosen OF-12 at USD 18.62. Locally GLM-5.2 passed 3/4
(job glm-b7a4-r2); the QC platform's own four runs passed 4/4 and the version was
rejected. Every scripted run handled every explicit rule, so round 3 moves the difficulty
into the data.

**Round 3 (current).** The stated rules do not change; the data stops matching the
assumptions a rule-to-code script makes. Each point is unambiguous to a reader of the file:

9. **Lengths carry their unit.** The catalogue's `length` column reads `12 ft` or `3.7 m`
   (two EU-import SKUs). 3.7 m is 12.14 ft and fits; 3.0 m is 9.84 ft and fails
   `FIT_LENGTH`. The 3.7 m cord at Phenix City (OF-21) lands at 18.62, a three-way tie with
   OF-04 and OF-12, and is the nearest store, so it is the chosen offer. Treating metres as
   feet, or skipping the metric rows, moves the chosen offer and the count.
10. **A repeated export line.** `same_day_offers.csv` lists OF-08 twice, identically. The
    sheet is one row per offer, keyed by `offer_id`; a script that emits a row per input
    line fails the population lock.
11. **Codes in the till's case.** Two ledger lines are lower-case (`st-52,vlx-f8-12` is
    OF-12's opening count; `st-44,pc-1012` is the VOID behind OF-20). The terms say case is
    not significant; an exact-string join zeroes both offers.
12. **Conductor tiers.** The data sheet now requires 16 AWG over 15 ft. PC-1020 (20 ft,
    18 AWG) fails `FIT_GAUGE`, which precedes `FIT_LENGTH` (OF-16).

Result: 8 eligible of 22, chosen OF-21 at USD 18.62.

**Also changed.** `solution/compute_gold.py` derives the gold files, the golden
trajectory and the manifest's expected values from the inputs in one run, so the three
copies of the answer can no longer drift. `tests/manifest.json` was added as an identical
mirror of `tests/verifier.json`, which the graders load; the generator writes both. The mined `evaluations/oracle` and
`evaluations/nop` folders were removed. The mining pipeline's `consistency/` metadata was
dropped from the bundle because it describes the pre-hardening gold.

## Why it is hard now

Correctness is no longer eight independent rule checks. The stock figure depends on the
clock conversion, the tax rate depends on the method, the choice depends on a tie-break
chain that only decides once the other rules are right, and several rows fail two clauses
so the precedence order is load-bearing. A per-rule script that treats each clause on its
own misclassifies OF-09, OF-12, OF-13, OF-14, OF-15, OF-16, OF-17, OF-18, OF-19, OF-20, OF-21,
OF-22 or the chosen offer. Twelve such shortcuts (float rounding, notices ignored, stale store
row, missed alias, VOID ignored, prior-day lines counted, metres read as feet, metric rows
skipped, duplicate line kept, case-sensitive joins, gauge tier missed, lowest-id tie-break)
were replayed against the verifier and each scores 0.0.

## Scoring shape

Every verifier is core and the reward is core-gated (`tests/score.py`), so a run scores
exactly 1.0 or 0.0. A spread like 0.2–0.35 cannot occur on this task by design; the
difficulty signal is the pass count.

**Final battery (harbor job glm-b7a4-r3, terminus-2, GLM-5.2, 8 runs at -k 8): 4 of 8 passed;
rewards in start order 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0.** Eight runs were made so the
pass rate could be estimated before spending a platform upload. The four shipped rollouts are
the first four by start time, a mechanical choice, and score 2 of 4:

| rollout | harbor trial | started | reward | outcome |
|---|---|---|---|---|
| difficulty/r1 | task__FmDxrr6 | 19:28:19 | 0.0 | MODEL: emitted 23 rows (OF-08 twice) and counted 9 eligible; every other cell correct |
| difficulty/r2 | task__jWv5Q6m | 19:28:18 | 1.0 | noticed the repeated OF-08 line and collapsed it; Decimal, alias, case and unit handling all correct |
| difficulty/r3 | task__pBuguR7 | 19:28:18 | 0.0 | MODEL: same as r1, 23 rows and count 9 |
| difficulty/r4 | task__srLMHNj | 19:28:18 | 1.0 | re-checked the offer count, found the duplicate row, deduplicated |

The four unshipped runs (nGHPxnn 0.0, n28RbZm 1.0, bchiKAg 0.0, JYmka6D 1.0) fail or pass for
the identical reason. All eight runs handled the metric lengths, the till-case codes, the
conductor tiers and every round 2 element; the one discriminating behaviour is whether the
run checks its input for a repeated key before treating each line as an offer. Zero
exceptions, every run wrote both deliverables.

Oracle on this package: 1.0 (harbor job oracle-b7a4-r3, digest-pinned image).
`evaluations/solvability/r1` is a copy of difficulty/r2, trial task__jWv5Q6m (a GLM-5.2 run, not the oracle).

**Evidence format note.** This harbor build writes `verifier/reward.txt` and
`verifier/score.json`; the bundle's `verifier/reward.json` and
`verifier/verifier_summary.json` were derived from those two files by
`tools/annotate_rollout.py` (a format conversion, no new facts), which also added
`model`, `overall_pass`, `final_answer`, `reward` and `judge` to each `result.json`.

**PreQC round 1 fixes.** The base image is pinned to its immutable digest
(`python@sha256:392307d2…`, the digest `python:3.12-slim-bookworm` resolved to) and every
script ships with LF endings (`.gitattributes` in the repo, normalisation in the zip script).

## QC flags left as-is

- R3 stability evidence: Turing runs stability; no `stability/` folder is shipped.
