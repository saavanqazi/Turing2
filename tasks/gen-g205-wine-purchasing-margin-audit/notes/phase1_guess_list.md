# Phase 1 — cold-read guess list (mined baseline, commit d3eddf8)

Every judgment call a careful analyst has to make reading `instruction.md` and
`input/margin_policy.md` cold, compared against the gold in `solution/files/`.
Each guess must be closed by a policy clause or a `submission_format.md` line before
hardening adds data that makes it bite.

| # | Guess | Gold's implicit answer | Bites on 6-row data? | Closed by (Phase 2/3) |
|---|---|---|---|---|
| G1 | Order of `\|`-joined codes when a wine has more than one | none — no multi-code wine exists | no | format contract: WM1, WM2, WM3 order |
| G2 | Margin compared raw or rounded at the 20 % boundary (19.95 %) | raw (no boundary row exists) | no | policy: compare the unrounded ratio |
| G3 | Is 20.0 % exactly "below 20"? | not exercised | no | policy: "below" means strictly less than |
| G4 | Supplier comparison: exact string, or trimmed / case-folded? | exact (all names clean) | no | policy: trim, case not significant |
| G5 | A wine in purchases but missing from the manifest | not exercised | no | policy: SUPPLIER_MISMATCH (no expected supplier on file) |
| G6 | `wine_type` values other than `stock` / `futures`, or mixed case | not exercised | no | policy: case not significant; only `futures` is exempt |
| G7 | Vintage bounds inclusive? (2000 and 2025 themselves) | inclusive (2026 flagged, 2025 absent) | no | policy: "2000 through 2025 inclusive" |
| G8 | Does the futures exemption also cover WM2 / WM3? | no — W-2 checked only for margin | no | policy: exemption scoped to WM1 only (already stated) |
| G9 | Same `wine_id` on two lines — which governs, and is `wine_count` rows or wines? | not exercised | no | format contract: one row per wine keyed by `wine_id`; policy: governing line rule |
| G10 | `compliant_count` = wines with no finding (not rows, not codes) | wines with no finding | no | format contract: key definitions |
| G11 | Does a wine with two codes count once in each code's count? | not exercised | no | format contract: each `*_count` counts wines carrying that code |
| G12 | Memo: what must it contain? | table of findings + section naming W-2 | yes (verifier regex on wording) | verifier made value-based (wine ids), incidental tag |

Leakage found in the same read:
- `instruction.md`: "the wine that looks low-margin and is not" — states there is exactly one trap.
- `margin_policy.md` WM1: "This is the commonest false positive in this review…" — explains the trap.
- `wine_purchases.csv`: supplier `WrongCo` names the answer.

Verifier coverage (forward / backward) against the mined `tests/verifier.json`:
- Forward gaps: one-row-per-wine (population), compliant rows, memo "explaining each finding".
- Backward: every mined verifier follows from an instruction ask; three existence checks are
  redundant with content checks on the same file (harmless under core gating).
- Strict phrasing regex: `memo_explains_futures` (`futures…exempt` on one line).
- No `category` field in this schema → "delete secondary verifiers" is N/A.

Baseline evidence (this container has no docker daemon / harbor / GLM key):
- Local engine replay of the gold through the mined grader: 15/15 pass (see phase1 log).
- Harbor oracle (`oracle-g205-baseline`) and GLM battery (`glm-g205-baseline`, -k 4) still
  to be run on a machine with harbor + the LiteLLM key; record rewards here when done.
