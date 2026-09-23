# Roadmap — gen-g205-wine-purchasing-margin-audit

QC / hardening plan for the mined task `obi/gen-g205-wine-purchasing-margin-audit`
(non-connector, offline, file-deliverable), modelled on the accepted bundle
`bus-mg_bus_b7_a4-same-day-power-cord-sourcing` (2/4 GLM, oracle 1.0, accepted).

Goal: Oracle exactly 1.0 on repeat, GLM-5.2 fully passing 1–3 of 4 (target 2/4), and the
difficulty coming from coupled reasoning and data shape, never from ambiguity.

---

## 0. What the mined package is (cold read)

| Piece | State as mined |
|---|---|
| `instruction.md` | ~90 words. Asks for `wine_findings.csv` (`wine_id,finding`), `wine_memo.md`, `results.json` (5 count keys). Names the trap explicitly: "the wine that looks low-margin and is not". |
| `environment/input/` | `wine_purchases.csv` (**6 rows**), `wine_manifest.csv` (6 rows), `margin_policy.md` (WM1 margin ≥ 20 %, futures exempt; WM2 vintage 2000–2025; WM3 supplier = manifest). No format-contract file. |
| `solution/` | `solve.sh` copies pre-baked `solution/files/`. **No `golden_trajectory.json`**, no gold generator. |
| `tests/` | `verifier.json` (15 deterministic verifiers, regex-on-raw-text style), `test_outputs.py` (positive lane only), `test.sh` (reward = pytest exit code), **old** vendored `rl_world_verifiers/` engine (no `table_equals`, `object_equals`, `csv.read_rows`, no `tag`-based scoring). **No `manifest.json`, no `score.py`.** |
| `environment/_app/` | Absent — correct for a non-connector task; no mirror, no sync script. |
| `Dockerfile` | `FROM python:3.12-slim-bookworm` (mutable tag → PreQC QC1-2 finding), no `USER root` (E2B uid-1000 issue), no document libs. |
| `task.toml` | Sane. `mcp_servers = []`, `network_mode = "public"`. |

Gold recomputed independently from the raw inputs — matches `solution/files/` exactly:

```
W-1 50.0% stock   compliant
W-2 10.0% futures compliant          <- the trap (futures exempt from WM1)
W-3 13.3% stock   MARGIN_TOO_LOW
W-4 50.0% stock   VINTAGE_INVALID    (vintage 2026)
W-5 50.0% stock   SUPPLIER_MISMATCH  (supplier "WrongCo" vs manifest NapaCo)
W-6 38.9% stock   compliant
counts: wine=6, margin=1, vintage=1, supplier=1, compliant=3
```

### Defects and weaknesses

1. **Far too easy.** Six rows, three independent single-source rules, one trap that the
   policy explains in a paragraph ("commonest false positive"). Wrong supplier is literally
   `WrongCo`. Expect 4/4.
2. **Leakage.** Instruction says there is exactly one exempt low-margin wine; policy says
   which rule it hides in.
3. **Grader is the old engine.** Regex-on-raw-text row checks, no population lock (a
   duplicated or missing row is not caught), no closed key set on `results.json`, no
   per-cell table comparison. The accepted bundle grades with `table_equals` +
   `object_equals` on the newer engine.
4. **Strict regex.** `memo_explains_futures` demands `futures…exempt` on one line; a memo
   saying "futures wines are excluded from the margin rule" fails a correct answer.
5. **Scoring.** `test.sh` writes reward from the pytest exit code. The accepted pattern is
   `tests/score.py`: core checks gate (any core failure → 0.0), incidental checks
   (prose) carry weight and can never zero a substantively correct answer.
6. **Coverage gaps.** "One row per wine" (row count / population) unverified; compliant
   rows never checked; memo checked only for the trap, not "each finding".
7. **Unpinned conventions.** Order of `|`-joined codes; rounded vs raw margin at the
   boundary; wines missing from the manifest; supplier case/whitespace. None bite on 6
   rows; all will after hardening. The accepted bundle pins these in a
   `submission_format.md` + policy clauses, not in the instruction.
8. **Missing evidence assets.** No `golden_trajectory.json`; no ATIF trajectory emit in
   `solve.sh`; no `tools/annotate_rollout.py` to add `model/overall_pass/final_answer/
   reward/judge` to `result.json` and derive `reward.json` + `verifier_summary.json`.
9. **No `category` field in this schema** — "delete secondary verifiers" is N/A; the
   equivalent lever is `metadata.tag` (`core` / `incidental`).
10. **Container has no pandas** (python slim + engine deps). Gold generator must be
    stdlib-only (`csv`, `json`, `decimal`).

---

## 1. What the accepted reference bundle establishes (copy these, do not reinvent)

| Item | Reference pattern |
|---|---|
| Engine | Newer `tests/rl_world_verifiers/` with `csv.read_rows`, `table_equals`, `object_equals`, `tag` in metadata, `sources/code.py`, `html.py`, etc. **Vendor it wholesale** into g205 (replace the old copy). |
| Scoring | `tests/test.sh` runs pytest (grid + CTRF) **then** `tests/score.py`, which decides the reward: core-gated, incidental-weighted. `reward.txt` from `score.json`. |
| Test lanes | `tests/test_outputs.py` has four lanes: positive, deleted-deliverable, corrupted `results.json` figure, injected extra key. Generic, driven by the spec — copy as-is. |
| Verifier shape | One `table_equals` on the CSV with `id_column`, every graded row, `row_set` population lock, `cell_types` (`finding: "id"`), optionally `columns` closed. A second `table_equals` isolating the trap row(s). One `object_equals` on `results.json` with `closed: true` and per-key tolerance. Existence checks tagged core. Header regex only, case/quote tolerant. |
| Manifest | `tests/manifest.json` is an **identical mirror** of `tests/verifier.json`; graders load `verifier.json`; both written by the gold generator. |
| Gold | `solution/compute_gold.py` derives `solution/files/*`, `solution/golden_trajectory.json` and the manifest expected values from `environment/input/` in one run. `solve.sh` installs files and emits an ATIF trajectory to `/logs/agent/trajectory.json` (non-fatal). |
| Golden trajectory | JSON list of `{name: "bash", server: "local", arguments: {command}}` steps: `cat` each input, heredoc-write each deliverable, `ls -la` the deliverables. |
| Format contract | `environment/input/submission_format.md` pins header, allowed values, one-row-per-record keyed by id, number rendering, closed key set, placeholder examples. Instruction says "File layout is in `input/submission_format.md`". |
| Dockerfile | `FROM python@sha256:392307d2…` (digest the slim tag resolved to), `USER root`, engine pins, plus document-format libs so lazy engine imports never ERROR. LF endings enforced. |
| Evidence per trial | `agent/trajectory.json` (ATIF from terminus-2), `config.json` (trial-level), `result.json` (+ `model: "GLM-5.2"`, `overall_pass`, `final_answer`, `reward`, `judge: {type: "deterministic file_check", judge_model: null}`), `verifier/{reward.txt, score.json, ctrf.json, test-stdout.txt, reward.json ({"reward": x}), verifier_summary.json (derived from score.json, with `items[]`)}`. |
| Difficulty read | 8 runs at `-k 8`, ship the first four by start time (mechanical choice), report all rewards. |
| Review/README | `review.csv`: 5-col header, 12 owned rows, specific job names, trial ids, commit ids, probe replays. Stability/Calibration rows: "Turing runs this." README: baseline → rounds → why hard → scoring shape → battery table → evidence-format note → flags left as-is. |
| Harness | terminus-2 accepted; model recorded as `openai/glm-5.2` in config, `GLM-5.2` in `result.json`. |

---

## Phase 1 — Baseline (nothing changed yet)

1. `source ~/.config/harbor/env`; confirm `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `JUDGE_MODEL`.
2. Put the task under git; commit the untouched mined package (baseline commit id goes in
   `review.csv`).
3. Write the **guess list** from a cold read of `instruction.md` + `margin_policy.md`
   (item 7 above). Every guess becomes a policy clause or format-contract line in Phase 3.
4. `docker ps` budget check → baseline Oracle (`-a oracle`, job `oracle-g205-baseline`).
   Expect 1.0. If not, grader defect: read `verifier/test-stdout.txt`.
5. Baseline GLM battery (`glm-harbor-config.json`, `-k 4`, job `glm-g205-baseline`).
   Expect 4/4. Record the four rewards.

Exit: baseline oracle 1.0 and GLM 4/4 recorded with job names and commit id.

---

## Phase 2 — Port the accepted grading skeleton (before hardening, so all rounds measure alike)

1. **Vendor the newer engine**: replace `tests/rl_world_verifiers/` with the reference copy.
2. **Copy `tests/test.sh`, `tests/score.py`, `tests/test_outputs.py`** from the reference
   (they are spec-driven; only the docstrings mention the other task).
3. **Copy the reference Dockerfile** (digest-pinned base, `USER root`, engine + document
   libs, `COPY input/`, `chmod a-w`). Add `.gitattributes` forcing LF.
4. **Rewrite `tests/verifier.json`** in the accepted shape, tagged:
   - `findings_exists` (core), `findings_header` (core, tolerant regex on `wine_id,finding`).
   - `findings_table` (core): `csv.read_rows` + `table_equals`, `id_column: wine_id`,
     `rows` = every wine's `finding`, `row_set` = all wine ids (population lock, catches
     duplicates and omissions), `columns: [wine_id, finding]`, `cell_types: {finding: id}`.
   - `findings_table_trap_*` (core): the trap rows isolated so a failure names them.
   - `results_exists` (core), `results_figures` (core): `object_equals`, all keys, `closed: true`.
   - `memo_exists` (core); `memo_names_trap` / `memo_names_findings` (**incidental**,
     `contains` on wine ids, no phrasing regex). Drop `memo_explains_futures` as written;
     if kept, make it `(?is)` and value-based (`futures` anywhere), tagged incidental.
   - Every verifier's `why_justification` ends with "Entailed by: <quoted instruction or
     format-contract sentence>" (the 1:1 coverage proof the client reads).
5. Write `tests/manifest.json` as an identical mirror.
6. Replay gold locally through `score.py` (expect 1.0) and `test_outputs.py` (all lanes green),
   then Oracle → 1.0.

Note on `|`-joined codes: `cell_types: finding: id` compares after strip only, so the
policy must fix the join order (WM1, WM2, WM3) and the format contract must state it.
That is a pinned convention, not a trap — order-free grading is not available for a
single id cell, so the order must be unambiguous in the inputs.

Exit: new grader in place, gold scores 1.0 through every lane, oracle job `oracle-g205-r0` = 1.0.

---

## Phase 3 — Hardening (coupled reasoning + data shape, pinned by policy and format contract)

Levers in order: instruction → inputs → verifier. Each item must be **unambiguous to a
reader of the file** and governed by a clause in `margin_policy.md` or
`submission_format.md`. Nothing is spelled out as a recipe in the instruction.

### 3a. Add `environment/input/submission_format.md` (contract, not recipe)
Header exactly `wine_id,finding`; one row per wine keyed by `wine_id` as the purchases file
writes it, any order; `finding` is `compliant` or the codes joined with `|` in the order
WM1, WM2, WM3; `results.json` exactly the listed keys and nothing else; numbers plain.
Instruction points to it: "File layout is in `input/submission_format.md`."

### 3b. Data scale and shape (`wine_purchases.csv` → ~40–60 lines, ~10 suppliers)
- Realistic supplier names; mismatches are plausible near-names. Policy: supplier
  comparison is exact after trimming and ignoring case (so a near-name IS a mismatch).
- **Repeated export line** (same row twice, identical) → one row per wine; population
  lock fails a per-line dump. (This single shape discriminated 4/8 in the reference.)
- **Superseded lines**: same `wine_id` with `po_status` (`active`/`superseded`/`void`)
  or a later `po_date`; policy says which line governs. `wine_count` = distinct governing
  wines.
- Codes in the till's case: a couple of `w-17` ids and lower-case `Futures`; policy says
  case is not significant.
- Boundary rows: exactly 20.0 % (compliant, rule is "below"), 19.95 % (policy: compare
  the unrounded ratio), a high-margin futures wine (exemption not needed).
- Multi-code wines (two and three codes) so the join and its order are exercised.

### 3c. Chained derivation (threshold is not a literal)
- Margin on **landed cost** = purchase price + per-bottle freight from a new
  `supplier_terms.csv` (supplier → freight, incoterm); a wine's freight depends on the
  supplier actually invoiced, not the manifest's expected one.
- Minimum margin per **category** from a table in the policy (still 20 %, sparkling 25 %,
  fortified 18 %); category lives in the manifest. Three files per WM1 decision.

### 3d. Cross-source exceptions (the trap becomes a family; instruction stops naming it)
- WM1: futures exempt only while `wine_type = futures` **and** the manifest's allocation
  is open; closed allocation → treated as stock.
- WM2: window is 2000 up to the policy year in the policy header, **+1 for futures**
  (en primeur); `NV` allowed only where the manifest flags non-vintage.
- WM3: manifest gains `alternate_supplier`, acceptable **only on futures** lines.
- Remove "commonest false positive" paragraph; state each exemption once, plainly.
- Instruction asks for "the wines a plain margin check would flag but the policy does not"
  (plural, unnumbered).

### 3e. One chained figure in `results.json`
`margin_shortfall_total` = Σ over MARGIN_TOO_LOW wines of (required selling price at the
category minimum on landed cost − actual selling price), half-cent up, per a policy
clause. Wrong dedupe, freight, category or exemption all move this one number.
Grade with `object_equals` tolerance 0.01.

### 3f. Instruction rewrite (90–150 words, colleague voice)
Context, the asks, the deliverable names, "layout in `input/submission_format.md`".
No paths, no column dumps, no method, no counts, no trap hint.

### 3g. Gold generator `solution/compute_gold.py` (stdlib only)
Implements the policy clauses verbatim; writes `solution/files/*`,
`solution/golden_trajectory.json` (cat inputs, heredoc deliverables, `ls -la`),
and the expected values in `tests/verifier.json` + `tests/manifest.json`. Prints the
per-wine derivation for hand-checking. `solve.sh` = reference `solve.sh` (install files +
ATIF emit).

### 3h. Wrong-answer probes
Script ~10 shortcut answers (per-line dump, superseded line kept, case-sensitive join,
freight ignored, flat 20 %, futures exemption over-applied, alternate supplier on stock,
float rounding, join order reversed, NV treated as invalid) and replay each through
`score.py` → each must be 0.0. Record in README and `review.csv`.

Exit: guess list closed; gold generated; probes all 0.0; oracle 1.0.

---

## Phase 4 — Measure, classify, iterate (expect 2–3 rounds)

Per round: Oracle (twice) → `docker ps` → 1 GLM smoke → battery. Run **8** at `-k 8` when
budget allows, ship the first four by start time; otherwise `-k 4`.

Read `score.json` (`core_failures`, per-check `detail`) and the trajectory of every
failing run. Classify: MODEL / ambiguity / verifier bug / infra. Reward is core-gated so
runs read 1.0 or 0.0 — a 0.0 is not a crash unless `exception_info` is set or the
trajectory is missing.

Decide: 4/4 → more coupling or data shape (3b/3c/3d), not more rules. 1–3/4 with
MODEL-only causes → stop. 0/4 → check fairness, back off one element (we cannot run the
frontier re-run ourselves and need a 1.0 GLM run for solvability). Same failure in every
failing run with different passing runs = good; identical wrong answer in **all** runs =
look for ambiguity.

Verifier-only changes → re-grade existing rollouts. Instruction/data/asks changed → re-run.

Exit: oracle 1.0 on repeat; 1–3 of 4 at 1.0; every failure classified MODEL with one
understandable cause.

---

## Phase 5 — Evidence and packaging

1. `tools/annotate_rollout.py` (write ours, mirror the reference note): for each trial
   copy `agent/`, `config.json`, `result.json`, `verifier/`; add `model: "GLM-5.2"`,
   `overall_pass`, `final_answer` (from the run's `results.json`), `reward`,
   `judge: {type: "deterministic file_check", judge_model: null}`; write
   `verifier/reward.json` = `{"reward": x}` and `verifier/verifier_summary.json`
   derived from `score.json` (`source`, `reward`, `passed`, `total`, `core_failures`, `items[]`).
2. Layout: `evaluations/difficulty/r1..r4/`, `evaluations/solvability/r1/` = copy of a
   1.0 GLM trial (never the oracle). No `stability/`, no `platform/`, no `oracle/`,
   nothing loose under `evaluations/`, no job-level files.
3. `tools/make_zip.sh`: LF normalisation, digest check, zip only the task folder.
4. `README.md`: what the task asks; baseline → rounds with job names, rewards, commit ids;
   why hard (name the shortcuts and their probe results); scoring shape (core-gated);
   battery table (rollout, trial, started, reward, outcome); oracle job; solvability
   source; evidence-format note; PreQC fixes; flags left as-is (R3 stability).

---

## Phase 6 — Delivery Gate, review.csv, submit

1. Upload → Delivery Gate → fix real findings; R3 stability = reviewed, "Turing runs stability".
2. `review.csv` through the review form, 12 owned rows, in the reference's register
   (job names, trial ids, commit ids, probe counts, lanes green):
   - Package consistency — FIXED_AND_VERIFIED (manifest mirror, compute_gold, golden trajectory, engine port).
   - Clarity and scope — FIXED_AND_VERIFIED (guess list → policy clauses + format contract; instruction rewrite).
   - Realism and leakage — FIXED_AND_VERIFIED (`WrongCo`, trap named in instruction, policy paragraph; grep inputs for gold figures).
   - Difficulty — FIXED_AND_VERIFIED (baseline 4/4 → per-round results → final four rewards, paths).
   - Solvability — PASS (path, trial id, GLM not oracle).
   - Stability — "Turing runs this."
   - Oracle Mode — FIXED_AND_VERIFIED (oracle jobs per round, all 1.0; golden trajectory added).
   - Environment and files — FIXED_AND_VERIFIED (digest pin, USER root, LF, libs).
   - Connectors, MCPs, CLIs — N/A (non-connector: `mcp_servers = []`, no `_app/`).
   - Deliverables and artifact quality — PASS (format contract, gold conforms, cosmetic re-renderings still 1.0).
   - Verifier coverage and fairness — FIXED_AND_VERIFIED (forward/backward map, one tolerant header regex only, population + key-set locks, memo checks incidental and value-based).
   - LLM judge consistency — N/A (all deterministic; JUDGE_MODEL not consulted).
   - Reward hacking — PASS (closed key set, population lock, four negative lanes, probes 0.0, tests/solution never enter the image).
   - Calibration — blank / "Turing runs this."
3. Download `qc_report.html` → task root → re-zip → new version → Delivery Gate → submit.
4. On rejection: fix, refresh report, update rows to FIXED_AND_VERIFIED, new version.

---

## Decisions now settled by the reference (were open in v1 of this roadmap)

- **Scoring**: core-gated `score.py`, not fractional-by-N. Binary 1.0/0.0 per run is accepted.
- **Engine**: vendor the reference's newer `rl_world_verifiers/`; use `table_equals` / `object_equals`.
- **Evidence shapes**: `result.json` extra fields, `reward.json`, `verifier_summary.json` — as in §1.
- **Golden trajectory**: list of bash steps; `solve.sh` emits ATIF-v1.5.
- **Battery size**: 8 runs, ship first four by start time.
- **Ambiguity control**: `submission_format.md` + policy clauses, never the instruction.
