# Phase 3 — hardening round 1 ("r1")

Baseline (d3eddf8) was 4/4 GLM-5.2: six wines, three single-source rules, one trap the
policy itself explained, `WrongCo` as the wrong supplier. This round moves correctness into
coupled reasoning across three input files and into data shapes a rule-to-code script
mishandles. Every element is governed by a clause in `margin_policy.md` S0/WM1–WM3 or a line
of `submission_format.md`, so a careful reader lands on exactly one answer.

Answer: 40 wines, 14 MARGIN_TOO_LOW, 6 VINTAGE_INVALID, 6 SUPPLIER_MISMATCH, 17 compliant,
shortfall total 14.37. Exempt low-margin wines: W-02, W-29, W-30.

## What changed

| File | Change |
|---|---|
| `environment/input/wine_purchases.csv` | 6 → 44 lines / 40 wines. New columns `po_date`, `po_status`. Repeated line (W-08), superseded lines listed before (W-12) and after (W-13) the active one, a void line (W-26), lower-case supplier (W-20), capitalised `Futures` (W-30), trailing space in a supplier (W-24), near-name suppliers (W-05, W-23), NV vintages, boundary margins. |
| `environment/input/wine_manifest.csv` | New columns `category`, `alternate_supplier`, `allocation`, `nv_allowed`. W-16 deliberately absent. |
| `environment/input/supplier_terms.csv` | New: freight per bottle per supplier (every invoiced supplier present, including the wrong ones). |
| `environment/input/margin_policy.md` | Rewritten: S0 reading conventions (active line governs, repeated line is one line, trim + case-insensitive), WM1 on landed cost with a category table and the open-allocation futures exemption, WM2 with the futures +1 year and NV rule, WM3 with the futures-only alternate, code order, shortfall definition. The "commonest false positive" paragraph is gone. |
| `environment/input/submission_format.md` | Adds `margin_shortfall_total`; memo asks for every exempt low-margin wine (plural). |
| `instruction.md` | Rewritten, 128 words, colleague voice; names the three inputs; no paths, method, counts or trap hint. Asks for "the wines a plain margin check would flag that the policy does not". |
| `solution/compute_gold.py` | Implements S0/WM1–WM3 and the shortfall; writes gold, memo, trajectory, manifest expected values. |
| `tests/verifier.json` + `manifest.json` | `results_figures` gains `margin_shortfall_total` (tolerance 0.01); justifications re-quoted from the new instruction/contract. Still 10 verifiers, 7 core, 3 incidental. |
| `tools/probes.py` | New: 14 shortcut solvers replayed through `score.py`. |

## Design of the data (why each row is there)

| Wine | Shape / edge | Governing clause | Naive result |
|---|---|---|---|
| W-02, W-30 | futures, open allocation, low margin | WM1 exemption | flagged by a plain margin check; W-30 has `Futures` capitalised |
| W-29 | futures, open, negative margin, vintage 2027 | WM1 exemption + WM2 futures cap 2026 | VINTAGE_INVALID only |
| W-17 | futures, **closed** allocation, low margin | WM1 "closed → stock wine" | exempt-all-futures misses it |
| W-18 / W-19 | alternate supplier on futures / on stock | WM3 alternate futures-only | W-19 must be SUPPLIER_MISMATCH |
| W-07, W-20, W-35 | sparkling at 22–24 % | WM1 category table (25) | flat-20 misses all three |
| W-09, W-14 | fortified at 18.1–18.7 % | WM1 table (18) | flat-20 flags them |
| W-10, W-40 | margin ≥ 20 on purchase price, < 20 on landed | WM1 landed cost | freight-ignored misses them |
| W-38 | wrong supplier with lower freight; ok on invoiced freight, low on expected freight | WM1 "invoiced supplier's freight" | freight-from-manifest adds MARGIN_TOO_LOW |
| W-11, W-27, W-37 | exactly at the minimum | WM1 "strictly below" | ≤ flags them |
| W-21 | 19.96 % | WM1 "compared unrounded" | 1-dp rounding misses it |
| W-08 | repeated identical line | S0 | per-line output fails the population lock; count 44 |
| W-12 | superseded line first, active second | S0 | first-wins flags it |
| W-13 | active first, superseded second | S0 | last-wins misses it |
| W-26 | void line first | S0 | first-wins flags it |
| W-05, W-23 | near-name suppliers (`Napa Valley Co`, `Rioja Direct SL`) | S0 "any character differs" | fuzzy match misses them |
| W-20, W-24, W-30 | case / trailing-space variants | S0 trim + casefold | exact-string join flags them |
| W-16 | absent from manifest | WM3 no entry | must be SUPPLIER_MISMATCH; margin 31 % so no category reading flags WM1 |
| W-09, W-15, W-28, W-36 | NV with and without `nv_allowed` | WM2 NV rule | NV-always-invalid flags W-09/W-28/W-36 |
| W-04 / W-02, W-39 | vintage 2026 on stock / on futures | WM2 +1 for futures | cap-at-2025 flags W-02, W-39 |
| W-14, W-25, W-33, W-34 | 1997, 1999, 2000, 2025 | WM2 inclusive bounds | off-by-one errors |
| W-22, W-23, W-25 | two- and three-code wines | code order | join order |
| shortfall | 14 shortfalls, half-cent up | derived-figure clause | float `round()` gives 14.35 |

## Local replay

- `compute_gold.py`: 40 wines; `score.py` on gold 1.0 (10/10); `test_outputs.py` 20 lanes green.
- `tools/probes.py`: faithful solver 1.0; all 14 shortcuts 0.0, each naming the row or key it breaks
  (flat 20 → W-07/W-09; no freight → W-07/W-10; expected-supplier freight → W-22/W-38; first-wins →
  W-12/W-26; last-wins → W-13; per-line → duplicate W-08; line count → 44; all-futures-exempt → W-17;
  alternate on stock → W-19; 1-dp rounding → W-21; NV invalid → W-09/W-28/W-36; case-sensitive → W-30;
  futures year cap → W-02; float round → 14.35).

## Guess list status

G1–G11 from `phase1_guess_list.md` are each closed by a named clause (S0, WM1 "strictly below" /
"unrounded", WM2 inclusive bounds and NV, WM3 no-entry rule, format contract order and counts).
New conventions introduced this round and pinned: governing line (S0), repeated line (S0),
invoiced-supplier freight (WM1), closed allocation (WM1), futures vintage +1 (WM2), alternate
futures-only (WM3), shortfall rounding (derived figures).

## Harbor runs for this round (to run on the Windows host)

```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r1 -y
for /d %d in (jobs\oracle-g205-r1\*) do @type "%d\verifier\reward.txt"
docker ps --format "{{.Names}}"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 2 --env-file glm.env -o jobs --job-name glm-g205-r1 -y
for /d %d in (jobs\glm-g205-r1\*) do @type "%d\verifier\reward.txt"
```
Results (2026-09-23): `glm-g205-r1`, terminus-2, -k 8 -n 4: **8/8 passed, all 1.0**, 0 exceptions, 27 m 32 s. Too easy. Oracle r1 not yet run (run it before r2 only if r2 is delayed; r2 supersedes it).

Read: every data shape in r1 is announced by a policy sentence (repeated line, superseded lines, case, invoiced freight, closed allocation), so the model codes each clause as a rule. Round 2 must keep the rules unambiguous but stop announcing the shapes, and add shapes that only reading the files reveals.

---

# Phase 3 — hardening round 2 ("r2")

What the eight r1 trajectories showed: every run printed all inputs in two steps, transcribed
each policy clause into one script (Decimal, casefold, active-line filter, futures-only
alternate, HALF_UP), self-checked its output and finished in 6–8 steps. Four of eight built
the manifest with a last-wins dictionary. No run had to reason about the data, because every
shape was announced by a sentence in the policy and every number was already per bottle.

Round 2 keeps every rule and stops announcing the shapes. The data now disagrees with a
clause-by-clause transcription in three places a reader of the files sees at once:

| Change | File | Governing clause | Transcription that breaks |
|---|---|---|---|
| `pack` column; six lines priced per pack (`6x75cl`, `12x75cl`, `3x75cl`) with identical per-bottle values | `wine_purchases.csv` | S0 "every rule is per bottle; a line gives its prices for the pack on that line" | pack price + per-bottle freight: W-21, W-32, W-40 flip to compliant |
| freight on a stated basis: `freight`, `freight_basis` (`per bottle` / `per case`), `case_size`; Bordeaux Negoce, Tuscan Vines, Barossa Exports quoted per case | `supplier_terms.csv` | S0 "freight is given on the basis the terms state", WM1 "freight per bottle" | 26.40 read as per bottle: W-11, W-12 and every Tuscan/Bordeaux/Barossa wine flagged |
| `effective_from` column; W-05 newer row first (expected supplier now `Napa Valley Co` → compliant), W-20 older `still` row first, newer `sparkling` second; W-24 a 2026-dated row last | `wine_manifest.csv` | S0 "latest effective_from on or before 31 Dec 2025 governs" | last-wins: W-05, W-24 wrong; first-wins: W-20 wrong; future row honoured: W-24 wrong |
| removed "an export may repeat a line" and "whether or not that supplier is the one the manifest expects" | `margin_policy.md` | S0 "one wine per wine_id", WM1 "supplier named on the governing line" still pin both | the repeated W-08 line and the invoiced-freight rule are now found in the data, not read in the policy |

Answer r2: 40 wines, 14 MARGIN_TOO_LOW, 6 VINTAGE_INVALID, 5 SUPPLIER_MISMATCH, 18 compliant,
shortfall total 14.37 (unchanged: every per-bottle value is the same as r1). Exempt low-margin
wines still W-02, W-29, W-30.

Local replay: `score.py` on gold 1.0 (10/10); `test_outputs.py` 20 lanes green;
`tools/probes.py` faithful 1.0 and all 19 shortcuts 0.0 (the five new ones: pack ignored →
W-21/W-32/W-40; basis ignored → W-11/W-12…; manifest last-wins → W-05/W-24; first-wins → W-20;
future row honoured → W-24).

Harbor runs for r2:
```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r2 -y
for /d %d in (jobs\oracle-g205-r2\*) do @type "%d\verifier\reward.txt"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 4 --env-file glm.env -o jobs --job-name glm-g205-r2 -y
for /d %d in (jobs\glm-g205-r2\*) do @type "%d\verifier\reward.txt"
```
Results (2026-09-24): `oracle-g205-r2` 1.0; `glm-g205-r2` terminus-2 -k 8 -n 4: **8/8, all 1.0**, 22 m 20 s. Still too easy.
Read: pack, freight basis and dated manifest rows are all visible as columns, and a column is
something GLM parses as readily as a clause. The lever that discriminated in the accepted
bundle was prose overriding the tables; r3 adds that.

---

# Phase 3 — hardening round 3 ("r3")

Round 3 changes the *kind* of information. A buyer's notes file in dated prose amends the
manifest and the supplier terms from each note's date, so a wine's answer now depends on its
own `po_date` against the note, per mechanism. The policy makes the notes binding in one S0
clause; the notes themselves are the only place the changes appear.

| Note (date) | Mechanism amended | Exercised by | Apply globally → | Ignore → |
|---|---|---|---|---|
| 2 Feb: Reims Cellars freight per case of 6 at 15.60 | freight basis | W-04 (2.60 either way) | no change | no change (a red herring that must be read and found harmless) |
| 15 Mar: W-17 allocation reopened | WM1 exemption | W-17 (po 9 Jun) → exempt, compliant | — | W-17 MARGIN_TOO_LOW |
| 20 Apr: Rioja Direct SL = Rioja Direct from today; earlier invoices stand | WM3 alias | W-23 (po 24 Mar) stays SUPPLIER_MISMATCH; W-42 (po 14 May) supplier ok | W-23 un-flagged | W-42 SUPPLIER_MISMATCH |
| 1 Jun: Tuscan Vines 6-bottle cases, case freight unchanged | freight per bottle 1.80 → 3.60 | W-41 (po 20 Jun) 22.9 % → 6.6 % MARGIN_TOO_LOW; W-13, W-07 after; W-11, W-35, W-32 before | W-11 flagged | W-41 compliant |
| 8 Jul: Left Bank Brokers no longer an alternate | WM3 alternate | W-43 (po 11 Aug) SUPPLIER_MISMATCH; W-18 (po 26 May) still fine | W-18 flagged | W-43 compliant |

Other changes: three wines added (W-41 Vermentino, W-42 Garnacha, W-43 Saint-Julien 6x75cl
futures); instruction names "my running notes from the year"; policy header and S0 gained the
notes clause; gold generator mirrors the notes as constants (as the accepted bundle did for its
store notices); probes gained "notes ignored" and "notes applied regardless of date".

Answer r3: 43 wines, 15 MARGIN_TOO_LOW, 6 VINTAGE_INVALID, 6 SUPPLIER_MISMATCH, 19 compliant,
shortfall total 16.14. Exempt low-margin wines: W-02, W-17, W-29, W-30.

Local replay: `score.py` on gold 1.0 (10/10); `test_outputs.py` 20 lanes green; `tools/probes.py`
faithful 1.0, all 21 shortcuts 0.0.

Harbor runs for r3:
```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r3 -y
for /d %d in (jobs\oracle-g205-r3\*) do @type "%d\verifier\reward.txt"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 4 --env-file glm.env -o jobs --job-name glm-g205-r3 -y
for /d %d in (jobs\glm-g205-r3\*) do @type "%d\verifier\reward.txt"
```
Results (2026-09-24): `glm-g205-r3` terminus-2 -k 8 -n 4: **8/8, all 1.0**, 36 m 38 s. Still too easy.

---

# Phase 3 — hardening round 4 ("r4"): scale, not more rules

What r3's eight trajectories showed: zero semantic errors in 24 GLM runs across r1–r3. Every
stumble was mechanical (a syntax error, a Decimal-to-JSON crash, a truncated terminal) and each
was caught by the run's own validation loop, which checks structure, never correctness. Several
runs did a manual pass first and then confirmed it in code. With 43 wines the answer can be
eyeballed; with three dated notes the windows can be held in the head.

Round 4 therefore changes scale rather than rules. The rules are those of r3 plus two S0 lines
that scale makes necessary (several active lines → latest `po_date` governs; no active line →
outside the review, not listed, not counted). The data is generated by `tools/gen_inputs.py`
(seeded), keeping W-01..W-43 verbatim and adding ~90 wines whose margins cluster within ±3
points of their category minimum, so any freight, pack, note-window or dedupe error flips
several rows, and the model can no longer verify by inspection.

| Element | Count / shape |
|---|---|
| purchasing lines / reviewed wines | 157 / 131 (W-129, W-130 have no active line → outside the review) |
| manifest rows | 138 incl. 5 stale-duplicate wines (both orders) and 2 future-dated rows; W-16, W-131, W-132 absent |
| suppliers | 18, four quoted per case; two trading-name aliases |
| dated notes | 9: two freight-basis changes on Tuscan Vines that open and close a window (1 Jun–31 Aug), a Douro rate rise, two allocation changes (open W-17, close W-120), one alternate withdrawn, two aliases with "earlier invoices stand", one no-op (Reims) |
| line shapes | repeated lines, superseded/void lines before and after, two different active lines per wine with the other line priced on the wrong side of the minimum (listed before or after), inactive lines dated after the active one across a note date (W-122, W-133), lines dated exactly on note dates (W-121, W-127, W-128) |
| packs | 1x, 3x, 6x, 12x75cl |

Answer r4: 131 wines, 41 MARGIN_TOO_LOW, 15 VINTAGE_INVALID, 14 SUPPLIER_MISMATCH, 67 compliant, shortfall total 27.79 (see `solution/files/results.json`). Exempt
low-margin wines: W-02, W-17, W-29, W-30.

Engine: `solution/policy_engine.py` now holds the rules once; `compute_gold.py` uses it for the
gold and `tools/probes.py` runs it with one shortcut flag at a time (25 probes). Local replay:
gold 1.0 (10/10), 20 lanes green, faithful 1.0, all 25 shortcuts 0.0 — including the three new
ones (two active lines first-listed wins → W-77; unreviewed wines listed → population; note
dates read against the latest line of any status → W-122/W-133).

Harbor runs for r4:
```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r4 -y
for /d %d in (jobs\oracle-g205-r4\*) do @type "%d\verifier\reward.txt"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 4 --env-file glm.env -o jobs --job-name glm-g205-r4 -y
for /d %d in (jobs\glm-g205-r4\*) do @type "%d\verifier\reward.txt"
```
Results (2026-09-24): `oracle-g205-r4` 1.0; `glm-g205-r4` terminus-2 -k 8 -n 4: **8/8, all 1.0**, 37 m 17 s. Still too easy.

---

# Phase 3 — round 5 ("r5"): remove the scaffolding that defused the one proven discriminator

Re-read of the evidence after 32/32 GLM passes across r1–r4: every rule ever written into
this task was coded correctly by every run. The accepted bundle's difficulty never came from a
rule; its four failing runs all made one modelling mistake, treating each export line as a
record, and nothing in its policy mentioned duplicates. This task carried the same shape (a
repeated line) from r1, but the superseded/void/active-line mechanics made every script group
by wine before doing anything else, which defused it. The trap was built and then disarmed by
the rule beside it.

r5 removes that scaffolding and keeps the coupled reasoning:

| Removed | Kept |
|---|---|
| `po_status` column; superseded, void and second-active lines; the S0 clauses about active lines and wines outside the review; the format-contract sentence "a wine that appears on more than one line is one wine and one row" | dated notes with windows, dated manifest rows, pack prices, freight basis, aliases with "earlier invoices stand", allocation changes, per-category minimums, boundary margins |

S0 now reads: "The review covers one wine per `wine_id`, on the purchasing line that carries
it." The format contract: "One row per wine, keyed by `wine_id` … in any order." That is the
same wording the accepted bundle shipped and was reviewed as fair. Five wines' lines are
repeated verbatim by the export (four adjacent, one displaced); no document mentions it.

Data: 136 lines / 131 wines, 136 manifest rows, 18 suppliers, 9 notes. Answer: see
`solution/files/results.json` (131 wines, shortfall 28.84). Exempt low-margin wines unchanged.

Local replay: gold 1.0 (10/10), 20 lanes green, faithful 1.0, all 20 shortcuts 0.0
(per-line output → five duplicate ids + population; line count → 136 vs 131).

Harbor runs for r5:
```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r5 -y
for /d %d in (jobs\oracle-g205-r5\*) do @type "%d\verifier\reward.txt"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 4 --env-file glm.env -o jobs --job-name glm-g205-r5 -y
for /d %d in (jobs\glm-g205-r5\*) do @type "%d\verifier\reward.txt"
```
Results (2026-09-24): `oracle-g205-r5` 1.0; `glm-g205-r5` terminus-2 -k 8 -n 4: **8/8, all 1.0**, 35 m 43 s. All eight runs printed diagnostics (duplicate ids, missing manifest rows, spelling variants) before coding and found the repeated lines deliberately.

---

# Redesign ("r6"): realized margin from a FIFO stock ledger

## Why five rounds failed

40 of 40 GLM-5.2 runs passed r1–r5. The r5 trajectories show the model's method: print every
file, run diagnostics for duplicate ids, missing keys and spelling variants, then transcribe
each policy clause into one script. Every task version so far was a per-wine classification:
each wine's answer was a short independent computation over one row plus lookups. Diagnostics
catch every visible anomaly; transcription catches every stated rule. Adding rules or data
shapes to that structure cannot produce failures.

## What changed

The margin is now the margin actually realized in 2025, computed from a stock ledger costed
first in, first out at landed cost ("state across steps" in the hardening guide). A wine's
cost of bottles sold depends on the whole ordered history of its stock: which layers exist,
in what order they are consumed, and what returns and write-offs did to them. A costing error
yields a plausible wrong number that no structural diagnostic can reveal.

| Source | Content |
|---|---|
| `stock_movements.csv` | 403 movements posted in 2025, listed in entry order (movement_id), not posted order: RECEIPT, SALE, WRITE_OFF, SUPPLIER_RETURN (references a receipt), CUSTOMER_RETURN (references a sale) |
| `opening_stock.csv` | 54 cost layers carried in from 2024, listed newest first as a stock system exports them |
| `wine_manifest.csv` | 36 wines: category, stock/futures, allocation, expected and alternate supplier, NV allowed |
| `supplier_terms.csv` | 14 suppliers, freight per bottle or per case |
| `margin_policy.md` rev. 3 | S1 FIFO costing (posted-date order, oldest layer first, supplier returns from their receipt's layer, customer returns re-enter as a new layer at the cost of the bottles the sale took last); WM1 realized margin with write-offs and supplier returns outside cost of bottles sold; WM2/WM3 on 2025 receipts; futures open-allocation exemption kept |

`results.json` gains `cost_of_bottles_sold_total`, the one figure every costing step feeds.
The deliverable shapes and the three finding codes are unchanged. `wine_purchases.csv` and
`purchasing_notes.md` are removed. `solution/ledger_engine.py` replaces `policy_engine.py`.

Answer: 36 wines, 12 MARGIN_TOO_LOW, 4 VINTAGE_INVALID, 3 SUPPLIER_MISMATCH, 19 compliant,
shortfall 570.40, cost of bottles sold 83892.03. Exempt under-minimum wines: W-02, W-27, W-34.

## Fairness

Every rule is stated once in the policy; nothing is left to a default. The generator guarantees
that no customer return spans two cost layers, that no supplier return exceeds its receipt's
remaining bottles, that no wine is ever oversold in posted-date order, and that no two movements
of one wine share a posted date. The engine asserts no overselling on the gold.

## Local replay

- gold 1.0 (10/10); `test_outputs.py` 21 lanes green
- `tools/probes.py`: faithful 1.0; all 23 shortcuts 0.0, 21 of them silent (no overselling, no
  crash): listed order instead of posted order, opening layers newest-first, write-offs ignored
  or counted in cost, supplier return FIFO or ignored, customer return ignored / costed at first
  layer / costed at sale average / put at the front of the queue / revenue-only, freight basis,
  freight ignored, flat 20, futures all/none exempt, case-sensitive names, margin rounded
  (W-16 at 19.97 %), alternate on stock, NV invalid, futures vintage cap. Average cost and LIFO
  are loud (they cannot place a supplier return).
- Removed probe: float rounding of the shortfall moves the total by one cent, inside the fair
  0.01 tolerance.

Harbor runs:
```
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a oracle -k 1 -n 1 --env-file glm.env -o jobs --job-name oracle-g205-r6 -y
for /d %d in (jobs\oracle-g205-r6\*) do @type "%d\verifier\reward.txt"
harbor run -p tasks\gen-g205-wine-purchasing-margin-audit -a terminus-2 -m openai/glm-5.2 -k 8 -n 4 --env-file glm.env -o jobs --job-name glm-g205-r6 -y
for /d %d in (jobs\glm-g205-r6\*) do @type "%d\verifier\reward.txt"
```
Results: (pending)
