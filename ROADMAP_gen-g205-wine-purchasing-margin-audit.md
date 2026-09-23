# Roadmap — gen-g205-wine-purchasing-margin-audit

QC / hardening plan for the mined task `obi/gen-g205-wine-purchasing-margin-audit`
(non-connector, offline, file-deliverable). Goal: ship a bundle where the Oracle scores
exactly 1.0 on repeated runs, GLM-5.2 fully passes 1–3 of 4 runs, and the difficulty comes
from coupled reasoning rather than ambiguity.

---

## 0. What the mined package actually is (cold read)

| Piece | State as mined |
|---|---|
| `instruction.md` | ~90 words. Asks for `wine_findings.csv` (`wine_id,finding`), `wine_memo.md`, `results.json` (5 count keys). Names the trap explicitly: "the wine that looks low-margin and is not". |
| `environment/input/` | `wine_purchases.csv` (**6 rows**), `wine_manifest.csv` (6 rows), `margin_policy.md` (WM1 margin ≥ 20 %, futures exempt; WM2 vintage 2000–2025; WM3 supplier = manifest). |
| `solution/` | `solve.sh` just copies pre-baked files from `solution/files/`. **No `golden_trajectory.json`** (required Oracle asset per spec). |
| `tests/` | `verifier.json` (15 deterministic verifiers, no rubric/LLM judge), `test_outputs.py` (pytest, one test per verifier), `test.sh`, vendored `rl_world_verifiers/` engine. **No `manifest.json`** yet (expected for non-connector; rewrite is the last packaging step). |
| `environment/_app/` | Absent. Correct for a non-connector task — no mirror, no `sync_app_mirror.sh` needed. |
| `task.toml` | Sane. `network_mode = "public"`, agent 1800 s, verifier 600 s, no MCP servers. |

Gold answer, recomputed independently from the raw inputs — matches `solution/files/` exactly:

```
W-1 50.0% stock   compliant
W-2 10.0% futures compliant          <- the trap (futures exempt from WM1)
W-3 13.3% stock   MARGIN_TOO_LOW
W-4 50.0% stock   VINTAGE_INVALID    (vintage 2026)
W-5 50.0% stock   SUPPLIER_MISMATCH  (supplier "WrongCo" vs manifest NapaCo)
W-6 38.9% stock   compliant
counts: wine=6, margin=1, vintage=1, supplier=1, compliant=3
```

### Defects and weaknesses found in the cold read

1. **Far too easy.** Six rows, three independent single-source rules, one trap that the
   policy itself explains in a full paragraph ("This is the commonest false positive…").
   The wrong supplier is literally named `WrongCo`. Expect 4/4 GLM passes.
2. **Leakage.** The instruction tells the model there is exactly one exempt low-margin wine.
   The policy tells it which rule the trap lives in. Together they hand over the trap.
3. **Scoring is all-or-nothing, not 1/N.** `tests/test.sh` writes `1` to `reward.txt` only
   when pytest exits 0, otherwise `0`. So a run failing 1 of 15 verifiers scores **0.0**.
   Consequences: (a) a 0.0 reward must not be read as a crash — check `ctrf.json`;
   (b) the engine's weighted `reward.json` / per-verifier summary is never produced, yet
   the bundle spec wants `verifier/reward.json` and `verifier/verifier_summary.json`
   per difficulty run.
4. **Strict-regex verifier.** `memo_explains_futures` demands `futures…exempt` on one line.
   A memo saying "futures wines are excluded from / not subject to the margin rule" fails a
   correct answer. Client called this out specifically.
5. **Free points.** `findings_delivered`, `memo_delivered`, `results_exists` are implied by
   the content verifiers on the same files. Harmless under binary scoring, 20 % free under
   fractional scoring.
6. **Coverage gaps (forward check).** Instruction asks "one row per wine" — no row-count
   verifier. Memo must "explain each finding" — only the trap (W-2) is checked. Nothing
   verifies that compliant wines are written as `compliant`.
7. **Unpinned conventions (ambiguity).** Order of `|`-joined codes; whether the margin is
   compared rounded or raw at the boundary; what to do with a wine missing from the
   manifest; case/whitespace in supplier names. None bite on the 6-row data, but they will
   the moment the data is hardened, so the policy must pin every one of them.
8. **No `category` field exists in this verifier schema**, so "delete secondary verifiers"
   is N/A here — record that in the review.
9. **Container has no pandas** (python 3.12 slim + verifier deps only). The gold solver must
   be stdlib-only. GLM can `pip install` because network is public; that is fine.

---

## Phase 1 — Environment and baseline (Day 0)

Purpose: prove the mined task grades correctly before touching anything.

1. `source ~/.config/harbor/env`; confirm `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
   `JUDGE_MODEL=openai/glm-5.2` are set (JUDGE_MODEL is irrelevant today — no rubric
   verifiers — but set it anyway so a later rubric does not silently score 0).
2. `docker ps --format '{{.Names}}'` — budget check.
3. Unzip to a working folder; `export TASK=/path/to/gen-g205-wine-purchasing-margin-audit`.
4. Write the "guess list": every judgment call a careful analyst must make reading
   `instruction.md` + `margin_policy.md` cold (see item 7 above). Keep it; it drives Phase 3.
5. Baseline Oracle:
   ```
   harbor run -p "$TASK" -a oracle \
     --ve OPENAI_API_KEY="$OPENAI_API_KEY" --ve OPENAI_BASE_URL="$OPENAI_BASE_URL" \
     -o /tmp/harbor-jobs --job-name oracle-g205-baseline -n 1 -y
   cat /tmp/harbor-jobs/oracle-g205-baseline/*/verifier/reward.txt   # expect 1
   ```
   Expected: 1.0. If not, it is a grader defect — read `verifier/test-stdout.txt`.
6. Baseline GLM smoke (1 run) then battery (`-k 4`) with `glm-harbor-config.json` pointed at
   the task. Expected: 4/4. Record the four rewards; this is the "before" evidence for
   `review.csv` Layer 2 Difficulty.
7. Inspect what Harbor actually lands in each trial's `verifier/` folder (reward.txt,
   ctrf.json, test-stdout.txt). This decides Phase 2 step 1.

Exit criteria: Oracle 1.0 recorded; baseline GLM 4/4 recorded; guess list written.

---

## Phase 2 — Grader mechanics (do this before hardening, so every later run is measured the same way)

1. **Make the reward fractional and produce the evidence files.** Rewrite `tests/test.sh`
   to call the vendored engine (`rl_world_verifiers.runner.run_verifier`) so
   `/logs/verifier/reward.json` (weighted reward + `assertion_results`) and `reward.txt`
   are written by the engine, and additionally emit `verifier_summary.json` with an
   `items[]` list (name, passed, reason) in the shape the team's accepted bundles use —
   copy the shape from a previously accepted bundle, do not invent one. Keep the pytest
   CTRF run as well so Harbor's per-test grid still works.
   Rationale: the client's rule is 1/N per verifier, "fully pass" is still reward == 1.0,
   and fractional rewards are what make the bimodal / crash / calibration reads possible.
2. **Fix the strict regex now** (it is a fairness bug independent of hardening):
   replace `memo_explains_futures` with a value-based check — memo mentions the trap wine
   id AND the word `futures` (or `pre-arrival`) anywhere, case-insensitive, no ordering
   constraint. Match the value, not the phrasing.
3. **Drop the three pure-existence verifiers** (or keep exactly one) — they are implied by
   the content checks on the same file.
4. **Add the missing coverage**: row count of `wine_findings.csv` equals distinct wine count
   (regex/count on raw text is enough); at least one explicit `compliant` row check for a
   decoy wine; memo mentions each flagged wine id.
5. Re-run Oracle → must be 1.0. Re-grade the baseline GLM runs against the new grader
   (verifier-only change, no re-run needed) and note any score shifts.

Exit criteria: engine-produced reward.json per run; no phrasing regexes; Oracle 1.0.

---

## Phase 3 — Hardening design (the real work)

Principle: coupled reasoning, not more rules. GLM writes one script per independent rule;
difficulty only compounds when correctness of one step depends on another. Levers in order:
`instruction.md` → input data → verifiers. Every new convention is pinned in
`margin_policy.md` (the authority), never left to the model to guess, and never spelled out
as a recipe in the instruction.

### 3a. Data scale and shape (`environment/input/`)
- Grow `wine_purchases.csv` to ~40–60 rows across ~10 suppliers and 3–4 categories.
- Supplier names become realistic (no `WrongCo`); mismatches are plausible near-names
  (`Napa Valley Co` vs `NapaCo`), and the policy states comparison is exact after trimming
  whitespace and ignoring case — so a near-name IS a mismatch and the rule is unambiguous.
- Add **superseded/duplicate rows**: the same `wine_id` appears more than once with a
  `po_status` (`active` / `superseded` / `void`) or a later `po_date`; the policy defines
  which line governs. "One row per wine" now requires dedupe *before* aggregating, and
  `wine_count` must be distinct governing wines, not row count. (State across steps.)
- Boundary rows: a stock wine at exactly 20.0 % (compliant: rule is "below 20"), one at
  19.95 % (policy pins: compare the unrounded ratio), one futures wine with a large margin
  (no exemption needed — checks the model does not over-apply the exemption).
- Multi-code wines: at least two wines carrying two or three codes so the `|` join and its
  **policy-defined order** (WM1, WM2, WM3) are exercised. Verifiers accept either order
  anyway (alternation) so ordering is never a scoring trap.

### 3b. Chained derivation (threshold is not a literal)
- Margin is computed on **landed cost**, not purchase price: landed cost = purchase price +
  per-bottle freight for the supplier, where freight lives in a new
  `supplier_terms.csv` (supplier, freight_per_bottle, incoterm). No single file suffices.
- The minimum margin is per category and lives in the policy's table (e.g. still 20 %,
  sparkling 25 %, fortified 18 %); category comes from the manifest. Three sources must be
  joined to evaluate WM1 for one wine.

### 3c. Cross-source exceptions (the trap becomes a family, and the instruction stops naming it)
- WM1: futures exempt (kept), but only while `wine_type = futures` **and** the manifest
  marks the allocation as open; a futures wine with a closed allocation is treated as
  stock. Coupling wine_type × manifest field.
- WM2: vintage window becomes relative — 2000 up to the policy year stated in the policy
  header, **plus one year for futures** (en primeur is sold before bottling). A 2026
  futures wine is valid; a 2026 stock wine is not. Non-vintage sparkling (`vintage = NV`)
  is exempt from WM2 under a manifest `nv_allowed` flag.
- WM3: the manifest gains `alternate_supplier`; the alternate is acceptable **only for
  futures** lines. Supplier rule now depends on wine_type.
- Remove the "this is the commonest false positive" paragraph from the policy; state the
  exemption once, plainly. Remove "the wine that looks low-margin and is not" from the
  instruction; ask instead for "the wines that a naive margin check would flag but the
  policy does not" (plural, unnumbered) so the count is derived, not leaked.

### 3d. Extra derived figure in `results.json`
- Add one chained number, e.g. `margin_shortfall_total` = Σ over MARGIN_TOO_LOW wines of
  (required selling price − actual selling price), rounded per a policy-stated rule. Wrong
  dedupe, wrong landed cost or wrong exemption all propagate into this single number.

### 3e. Instruction rewrite (90–150 words, colleague voice)
- Context paragraph, the asks, exact filenames, the finding codes, the `results.json` keys.
- No `/app/input/` paths, no column dumps, no step-by-step, no hint about the trap count.
- Keep the deliverable block and working-environment block as the harness expects them.

### 3f. Gold solution
- Replace the static copy in `solve.sh` with a stdlib-only `solve.py` that recomputes
  everything from `input/` (csv + json only; no pandas in the image), regenerates
  `solution/files/*`, and is called by `solve.sh`. Now gold and data cannot drift.
- Produce `solution/golden_trajectory.json` (required Oracle asset) in the format the team
  uses — take it from the oracle Harbor run's trajectory or the team template.

### 3g. Verifier rewrite (`tests/verifier.json`, value-based)
- Per-wine assertions for a chosen set: the trap family, the boundary rows, the multi-code
  rows, the superseded rows, and several decoys expected `compliant`. Use
  `(?m)^"?W-17"?\s*,\s*"?(A\|B|B\|A)"?\s*$` style patterns — exact value, order-free.
- Row count = distinct wines. All `results.json` keys with `equals`; the shortfall with
  `approx_equals` and an explicit `tolerance` matching the policy's rounding.
- Memo: contains each flagged wine id and each trap wine id; contains `futures`; no
  phrasing regexes. Stay fully deterministic (no rubric): no `litellm` in the image, no
  judge dependency, better stability.
- Keep verifiers ↔ instruction 1:1 (forward and backward check again after the rewrite).

Exit criteria: guess list closed (every guess now has a policy sentence), gold regenerated
by `solve.py`, verifier list mapped 1:1 to the instruction.

---

## Phase 4 — Measure, classify, iterate (expect 2–3 rounds)

Loop per round:
1. Oracle run → must be exactly 1.0 (re-run after **every** change to gold, data,
   verifiers or instruction). Run it twice to show it holds on repeat.
2. `docker ps` budget check → 1 GLM smoke run → full `-k 4` battery.
3. Read all four rewards from `reward.txt`; read whole `items[]` entries in
   `verifier_summary.json` (do not grep for "passed").
4. Classify every failing run: MODEL (keep), ambiguity (fix policy/instruction), verifier
   bug (fix grader, re-grade), infra (re-run). Only MODEL failures count.
5. Decide:
   - 4/4 → add coupling (3b/3c/3d), not more independent rules.
   - 1/4–3/4 with MODEL-only failures → stop hardening.
   - 0/4 → check fairness first; if fair, back off one coupling and re-run (a 0/4 needs a
     frontier re-run we cannot do ourselves and is accepted only ~1/3 of the time —
     aim for 2/4).
   - Bimodal or near-identical failure across runs → it is an ambiguity; fix the wording.
6. A verifier-only change can be re-graded against existing rollouts; a change to the
   instruction, data or what verifiers ask invalidates them — re-run the four.

Exit criteria: Oracle 1.0 on repeat; four GLM rewards with 1–3 exactly 1.0; every failure
classified MODEL; failure causes concentrated on the designed couplings.

---

## Phase 5 — Packaging

1. **`tests/verifier.json` → `tests/manifest.json`** (last packaging step for non-connector
   tasks). Point `test_outputs.py` / `test.sh` at `manifest.json`; re-run Oracle → 1.0.
2. Evidence layout (nothing loose under `evaluations/`):
   - `evaluations/difficulty/r1..r4/` — the four trial folders copied intact
     (`agent/trajectory.json`, `result.json`, `verifier/reward.json`,
     `verifier/verifier_summary.json`, trial `config.json`). Never job-level
     `config.json`, `lock.json`, `job.log` or job-root `result.json`.
   - `evaluations/solvability/r1/` — a copy of one **GLM** run that scored 1.0. Never the
     oracle. (If no GLM run hits 1.0 we have no solvability evidence with our key — another
     reason to land at 2/4 rather than 0/4.)
   - Each `result.json`: `"model": "GLM-5.2"` exactly, boolean `overall_pass`,
     `final_answer`, `reward`, judge provenance. Normalise Harbor's output with a small
     script if fields are missing; do not hand-edit.
   - No `stability/` (Turing runs it). No `platform/` (gate rejects it today). No
     `evaluations/oracle/`.
3. `README.md` at task root: mined baseline → final, why each change, why it is hard now,
   any QC flag left unfixed (expect R3 stability: "Turing runs stability").
4. Zip **only** the task folder.

---

## Phase 6 — Delivery Gate, review record, submit

1. Upload to the QC platform; run the Delivery Gate; do the manual checklist while waiting.
2. Fix real findings; mark R3 (stability) reviewed with the one-line note.
3. Write `review.csv` in the review form (never by hand), 12 rows owned, specifics only:
   - Package consistency — PASS (three deliverable names identical across toml/instruction/manifest).
   - Clarity and scope — FIXED_AND_VERIFIED (guess list → policy pins; instruction rewrite).
   - Realism and leakage — FIXED_AND_VERIFIED (`WrongCo`, trap named in instruction, policy paragraph explaining the trap).
   - Difficulty — FIXED_AND_VERIFIED (baseline 4/4 → final rewards listed individually, paths to `evaluations/difficulty/rN/verifier/reward.json`).
   - Solvability — PASS/FIXED (path to the 1.0 GLM run).
   - Stability — leave blank ("Turing runs this").
   - Oracle Mode — FIXED_AND_VERIFIED (1.0 on repeat; note the `golden_trajectory.json` addition and `solve.py`).
   - Environment and files — PASS/FIXED (Dockerfile, inputs read-only, stdlib-only gold).
   - Connectors, MCPs, CLIs — N/A with reason (non-connector, `mcp_servers = []`, no `_app/`).
   - Deliverables and artifact quality — PASS/FIXED.
   - Verifier coverage and fairness — FIXED_AND_VERIFIED (strict regex removed, free points removed, row-count/compliant/memo coverage added, 1:1 map).
   - LLM judge consistency — N/A with reason (all deterministic; no rubric verifiers; no judge in image) — or PASS if a rubric is added later.
   - Reward hacking — PASS (checks are on values in three files that must agree; no way to satisfy counts without the findings).
   - Calibration — blank.
4. Download `qc_report.html` → drop at task root → re-zip → upload as new version → run
   the Delivery Gate again → submit that version.
5. After submit: watch pipeline state; a rejection restarts at Phase 4/5 with a new version.

---

## Decision points to flag early (not blockers, but confirm)

- **Fractional vs binary reward in `test.sh`** (Phase 2.1). Recommended: fractional via the
  vendored runner; it matches the client's 1/N rule and the evidence file spec.
- **Shape of `verifier_summary.json` and `result.json` provenance fields** — copy from an
  accepted bundle rather than guessing.
- **`golden_trajectory.json` format** — take the team template.
- **How far to harden** — target 2/4; 0/4 costs a frontier re-run we cannot do ourselves.
