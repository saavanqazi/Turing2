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
