# Roadmap: bus-mg_bus_b7_a4-same-day-power-cord-sourcing, v4 (fixing the Final QC rejection)

Task: `obi/bus-mg-bus-b7-a4-same-day-power-cord-sourcing`. It is non-connector, offline, graded on deliverable files, and has no `_app/` mirror.
Rejected version: v3. Final QC ran on 28 Sep 16:11, batch `auto-pipeline-evaluation-d90c0b8adabf4a37`, and gave **HARBOR-CHECK-1**
(`review-001/layer1_realism_leakage__domain_correctness`, finding `ambiguous_rule_contested_gold`).
v3 cannot be resubmitted. Everything below goes into a **new version of the same task**.

Working copy: `tasks/bus-mg_bus_b7_a4-same-day-power-cord-sourcing/`.
- Commit `9b6d511` is the rejected v3 exactly as it was handed over, minus `evaluations/`.
- Commit `096eba7` is Phase 1 (done).

---

## 0. What the pipeline actually found (reproduced locally)

The Delivery Gate passed v3 and the oracle scored 1.0 on E2B and Modal. The rejection came from
the Harbor Check reviewer, which found two places where the gold answer rests on a reading the
agent-visible text does not force.

### Finding A: the repeated OF-08 line (the main one)

`same_day_offers.csv` has 23 data rows, and OF-08 appears twice, identically (lines 9 and 24).
The gold sheet has 22 rows and `eligible_offer_count = 8`. That requires deduplication, and the
only place that rule was written down was the hidden `solution/compute_gold.py:80` (*"an export
may repeat a line; one offer = one offer_id"*). The visible text said:
- "One row per record, keyed by `offer_id`"
- "One row per offer in `same_day_offers.csv` … with `offer_id` as that file writes it"

Both of those can be read as 23 rows (one per input line) or as 22 rows (one per distinct id).

- **Counterexample:** the pipeline's codex run (`openai/gpt-5.6-sol`, E2B) kept both OF-08 rows.
  Every graded cell was correct, `eligible_offer_count` was 9, and the reward was **0.0**.
- **Local reproduction:** appending the second OF-08 row to gold and setting the count to 9
  fails with exactly the pipeline's message:
  `duplicate id OF-08; population mismatch: extra ['OF-08'], missing []` and
  `eligible_offer_count: expected 8, got 9`.

**Why this matters beyond the fix.** Per the v3 README, this was the *only* discriminating
behaviour in the battery. All 8 local GLM runs got every other rule right, and the 4 failures
failed *only* on this. In the Standard's terms, the difficulty was an unstated rule. That fails:
- DIF-1: difficulty must survive full disclosure
- DIF-6: ambiguity is not difficulty
- GLD-5: a reading that can go two ways needs a clarifying sentence

Once the rule is written down, the task will very likely pass 4/4. **So v4 needs a new, honest
difficulty source (Phase 3). It is not just a wording patch.**

### Finding B: the OF-05 reason code (text truncated in the rejection)

The gold for OF-05 (ST-31, PC-1012, PICKUP) is `SAME_DAY_SUSPENDED`. ST-31 is flagged suspended,
and on Eastern time the order lands at 15:40, past the 15:00 cutoff, so it fails two clauses.
The reviewer quoted S2 and S5.

The conflict:
- `submission_format.md` said `reason_code` is **"the first clause of the terms that refuses the
  offer"**. Read as *document order*, that makes S1's `CUTOFF_PASSED` the first clause.
- S5 ranks `SAME_DAY_SUSPENDED` ahead of `CUTOFF_PASSED`.

The same document-order reading also flips OF-19 (`OUT_OF_RADIUS` is written before
`STOCK_RESERVE` inside S2). Replaying it gives 0.0: `row OF-05: expected SAME_DAY_SUSPENDED, got
CUTOFF_PASSED; row OF-19: expected STOCK_RESERVE, got OUT_OF_RADIUS`.

> **Action for you:** paste the full untruncated text of the OF-05 finding from the platform
> (expand the rejection). If it argues something other than document order vs S5, add that
> reading to `tools/probes.py` and fix it in Phase 1 before going further.

### Other readings a picky reviewer could raise (found on a cold re-read, fixed pre-emptively)

| # | Reading | Where | Effect if taken |
|---|---|---|---|
| C | "Effective stock … shared by every offer that draws on it" means the pickup OF-08 uses up one of the 2 units, so delivery OF-09 has 1 | terms S2 | OF-09 becomes `STOCK_RESERVE`, count 7 |
| D | "I need a ten-foot replacement" means exactly 10 ft, which contradicts S4's 10–15 ft | instruction.md | 12 ft and 3.7 m cords refused |

---

## Phase 1: remove every contested reading (DONE, commit `096eba7`)

| File | Change |
|---|---|
| `environment/input/submission_format.md` | "One row per record" becomes "One row per offer". Added "**each distinct `offer_id` appears in the sheet exactly once**". `reason_code` is now "the refusal that the precedence order in S5 of the terms ranks first among every clause the offer fails (**S5's order, not the order the clauses appear in the terms**)". `eligible_offer_count` is "the number of **distinct offers (counted by `offer_id`)**". |
| `environment/input/same_day_terms.md` | S5: "Report the one that comes FIRST in this order, **whichever section of these terms states it**". S2: every offer on a store and cord is judged against the same figure, and "**no offer uses up another's units**". |
| `instruction.md` | "a replacement **at least as long as the ten-foot original**" |
| `tests/verifier.json` + `tests/manifest.json` | The quoted "Entailed by" sentence in two verifiers now matches the new wording. The two files are still byte-identical. |
| `solution/compute_gold.py` | The dedup comment now cites the published rule. The gold **values are unchanged**: 22 rows, 8 eligible, OF-21 at 18.62. |
| `tools/probes.py`, `tools/local_check.sh` (repo only, **never zipped**) | Local oracle replay plus probes. |

**Verification (run locally):** `PY=<venv python> bash tools/local_check.sh`
- gold: `score.py` reward **1.0** (6/6 core), `test_outputs.py` 12 passed
- 3 contested readings (A, B, C): **0.0 each**, and the sentence that excludes each one is present
  in the agent-visible text (the probe checks this, so a later edit that drops it fails loudly)
- 4 shortcut readings (lowest-id tie, float rounding, metres read as feet, notice ignored):
  **0.0 each**
- the instruction's length wording check passes

The reason the wording is "each distinct `offer_id` appears exactly once" rather than "the
export repeats OF-08": the first *states the rule*, the second *points at the trap* (leakage).
The repeated line stays in the data as an honest record-level check.

---

## Phase 2: measure the de-ambiguated baseline (you, locally)

The instruction and format changed, so **all v3 rollouts are invalid**. Do not reuse them.

```bash
source ~/.config/harbor/env                      # OPENAI_API_KEY, OPENAI_BASE_URL, JUDGE_MODEL
TASK=~/Desktop/TuringTask/<v4 task folder>
# 1. Oracle: must be exactly 1.0, and again after every later change
harbor run -p "$TASK" -a oracle --ve OPENAI_API_KEY="$OPENAI_API_KEY" \
  --ve OPENAI_BASE_URL="$OPENAI_BASE_URL" -o /tmp/harbor-jobs --job-name oracle-b7a4-v4p1 -n 1 -y
cat /tmp/harbor-jobs/oracle-b7a4-v4p1/*/verifier/reward.txt
# 2. GLM battery: budget check, smoke run, then 8 attempts (8 gives a usable rate estimate)
docker ps --format '{{.Names}}'
#    glm-harbor-config.json: tasks[0].path = "$TASK", job_name = "glm-b7a4-v4p1"
harbor run -c glm-harbor-config.json -n 1 -y
harbor run -c glm-harbor-config.json -n <3-minus-running> -k 8 -y
cat /tmp/harbor-jobs/glm-b7a4-v4p1/*/verifier/reward.txt
```

For each failed run, classify it (MODEL / ambiguity / verifier bug / infra) by reading whole
`items[]` entries in `verifier/verifier_summary.json`.

- **Expected result: 7–8 of 8 pass**, i.e. too easy. In that case go to Phase 3.
- If it is somehow ≤ 5/8 **and** every failure is a MODEL failure against a stated rule, skip to
  Phase 4.

---

## Phase 3: honest hardening (design, then measure)

**Constraints from the Standard.**
- Each new crux must be one disclosed sentence (DIF-1).
- It must be a sanctioned pattern (DIF-3).
- The wrong readings must be written in the README **before** the trials (DIF-4).
- No hidden rules, no hair-thin tolerances, no volume (DIF-6).
- Coupled rules beat stacked rules. v3 showed GLM scripts every independent rule correctly.

Do the levers **one per round, H1 first**. Re-run the oracle, then 8 GLM runs, after each.

### H1: the tie is decided by *when* the cord is ready, across two clocks (entangled rules + wrong-default lure)

Today the "ready earliest" tie-break never decides anything: every eligible collection is ready
at 16:40 Central. Make it decide:

1. **Catalogue:** add `PC-4035,VLX-F8-3M5,"Figure-8 AC power cord, 3.5 m (EU import)",C7,N,3.5 m,18,2.5 A / 250 V,17.24`.
   3.5 m is 11.48 ft, so it fits.
2. **Offers + ledger:** add `OF-23,ST-08,PC-4035,PICKUP` and give ST-08 an `OPENING` for PC-4035.
   Columbus is on Eastern time and its collection cutoff is 16:00 by notice.
   17.24 × 1.08 = 18.6192, which rounds to **18.62**: a four-way tie with OF-04, OF-12 and OF-21.
3. **Notices:** Phenix City (ST-44): "stocktake today; collection orders are ready **three hours**
   after the order instead of two". This makes OF-21 ready at 17:40 CT.
4. **Terms:**
   - S1: "Collection is ready two hours after the order instant **unless a notice for that store
     and day gives a different preparation time**."
   - S6: "take the one ready earliest **as a moment in time**". Per GLD-5, derive the key under the
     "compare local clock readings" reading. It changes the key, so this clarifying phrase is
     required.

**Gold:** OF-04, OF-12 and OF-23 are all ready at 16:40 CT (OF-23 is 17:40 ET), and OF-21 at 17:40 CT.
Nearest of the three is **OF-23 (3.4 mi)**. Wrong readings to pre-declare in the README:

| Wrong reading | Gives |
|---|---|
| Compare local clock strings ("16:40" < "17:40") | OF-12 |
| Ignore the ST-44 stocktake notice | OF-21 |
| Lowest `offer_id` among equal costs | OF-04 |
| Miss the Columbus notice | OF-23 `CUTOFF_PASSED` |

Only `chosen_offer_id` moves, and only a run that gets the *whole* chain right passes.

**Watch-out:** Python compares timezone-aware datetimes correctly for free, so a careful script
avoids this lure. If H1 alone still passes ≥ 7/8, add H2.

### H2: an order placed exactly on an Eastern store's cutoff (latent crux on a stated boundary)

- Notice for Columbus (ST-08): "same-day **delivery** orders are accepted until **15:40** today".
  This replaces "delivery keeps the usual cutoff".
- Add `OF-24,ST-08,PC-1015,DELIVERY`. Stock is 3, the store is 3.4 mi away, and the order instant
  is 15:40 ET. That is **on** the cutoff, so S1's "on or after" applies: `CUTOFF_PASSED`.
- A `>` comparison instead of `>=`, or reading the buyer's clock (14:40), makes it eligible at
  34.12. That wrongly changes the row and `eligible_offer_count`.
- The boundary is stated in words in S1, so it is legitimate (Standard 4.2: "every threshold says
  which side an exact hit falls on").

### Held in reserve (only if H1 + H2 still pass ≥ 7/8)

- **H3, stale authority on an offer:** replace the bare duplicate with a *re-listing*. Add a
  `listed_local` column on the store's clock, with the file not in time order. The rule is "the
  latest listing at or before the order instant is in force". This ties offer versioning to the
  timezone reasoning.
- **H4, delivery arrival on the store clock:** "arrives by 20:00" at an Eastern store is 19:00
  CT. It matters only if a delivery ties a collection on cost.

### What not to do

- Do not add independent rules.
- Do not hide the dedup rule again.
- Do not tighten tolerances.
- Do not reword verifiers to demand phrasing.

### After each lever

1. `python3 solution/compute_gold.py`. It regenerates the gold files, `golden_trajectory.json` and
   the verifier expected values.
2. Add the lever's wrong readings to `tools/probes.py`.
3. Run `tools/local_check.sh`. It must show gold 1.0 and every probe 0.0.
4. Harbor oracle: exactly 1.0.
5. 8 GLM runs.
6. Update the README's round section.

**Stop hardening when:**
- the 8 local runs pass **2–5 of 8**, **and**
- every failure is a MODEL failure on a stated rule.

v3's round 2 went 3/4 locally but 4/4 on the platform battery, so aim for the lower half.

---

## Phase 4: final evidence battery (you, locally)

1. Final oracle: 1.0, run twice (it must hold on repeat).
2. Final GLM battery at `-k 8`. **Ship the first four by start time** (the same mechanical rule
   v3 used; write it in the README). Those four must read 1/4, 2/4 or 3/4. If the first four read
   4/4 or 0/4, do not hand-pick: harden or relax and re-run.
3. Build the evidence with `annotate_rollout.py` (the v3 tool). Each `result.json` must carry:
   - `"model": "GLM-5.2"`
   - a boolean `overall_pass`
   - `final_answer`
   - `reward`
   - judge provenance

   It must also produce `verifier/reward.json` and `verifier/verifier_summary.json`.
4. `evaluations/solvability/r1`: a copy of one reward-1.0 difficulty rollout. **Never the oracle.**

---

## Phase 5: package v4

Zip **only the task folder** with this layout. Leave out `tools/`, `notes/`, `consistency/`,
`evaluations/oracle`, `evaluations/nop`, `evaluations/platform`, `__pycache__`, and any job-level
`config.json`, `lock.json` or `job.log`. Use LF line endings, and keep the Dockerfile digest-pinned
(unchanged).

```
bus-mg_bus_b7_a4-same-day-power-cord-sourcing/
├── task.toml  instruction.md  README.md  review.csv  qc_report.html
├── environment/{Dockerfile,input/*}
├── solution/{solve.sh,compute_gold.py,golden_trajectory.json,files/*}
├── tests/{test.sh,test_outputs.py,score.py,verifier.json,manifest.json,rl_world_verifiers/}
└── evaluations/
    ├── solvability/r1/{agent/trajectory.json,result.json,verifier/reward.json}
    └── difficulty/r1..r4/{agent/trajectory.json,result.json,config.json,verifier/{reward.json,verifier_summary.json}}
```

### README.md

Keep it cumulative. Add a "v4: Final QC rejection and fix" section:
- the two findings, the counterexample, and the local reproduction
- the Phase 1 wording
- the round(s) of Phase 3, with the pre-declared wrong readings
- the new battery table (trial IDs, rewards, a cause per failure)
- the updated "Why it is hard now"

Remove any claim that the duplicate line is an unannounced trap.

### review.csv

**You write this in the review form. A model may not author it.** The header is
`review_check,status,review_notes,change_made,what_to_record`. Rows to change from v3:

| Row | Status | What to cite |
|---|---|---|
| Layer 1 · Clarity and scope | FIXED_AND_VERIFIED | Findings A–D, the exact new sentences, and the probe results |
| Layer 1 · Realism and leakage | FIXED_AND_VERIFIED | The HARBOR-CHECK-1 source was `layer1_realism_leakage__domain_correctness`. The dedup rule lived only in the hidden `compute_gold.py`. It is now stated as a rule without pointing at the data. |
| Layer 5 · Verifier coverage and fairness | FIXED_AND_VERIFIED | The row_set lock and `eligible_offer_count` are now entailed by visible text. The codex counterexample is now defensibly wrong. |
| Layer 2 Difficulty | FIXED_AND_VERIFIED | The v3 difficulty was the unstated rule. New levers, the new battery, a cause per failure. |
| Layer 2 Solvability | PASS or FIXED_AND_VERIFIED | The new solvability run |
| Layer 3 Oracle Mode | FIXED_AND_VERIFIED | The oracle re-run after each change, 1.0 |
| Layer 1 · Package consistency | FIXED_AND_VERIFIED | The justification quotes were synced. The two manifests are identical. |
| Layer 4 · Connectors, MCPs, and CLIs | N/A | Native task, `mcp_servers = []` |
| Layer 2 Stability, Cross-trial · Calibration | Blank or "Turing runs this" | |

---

## Phase 6: QC platform loop (the order matters)

1. On the platform, **Upload a new version from the task itself** (not a fresh drag-in).
2. Run the Delivery Gate on it. It must reach "Ready for finalization". The R3 stability finding is
   expected: mark it reviewed with "Turing runs stability".
3. Download `qc_report.html` and place it at the task root next to `review.csv`.
4. Re-zip and upload as the **next** version, then run the Delivery Gate again.
5. Confirm the zip itself contains **both** `review.csv` and `qc_report.html`. The platform
   never injects the report.
6. Submit that version.

Submit is refused unless the upload is a Harbor bundle, the Delivery Gate passed, the score is
above the floor, `review.csv` is present with every row resolved, and this version was not already
submitted.

---

## Phase 7: before you press Submit (self-audit against the rejection)

- [ ] `grep -rn "one offer = one offer_id\|export may repeat" solution/` finds **nothing**. No
      rule lives only in hidden files.
- [ ] Re-read `instruction.md` and `input/*` cold. Write down every guess, derive the key under
      each alternative, and confirm each alternative is excluded by a visible sentence (GLD-5).
      `tools/probes.py` encodes the known ones.
- [ ] `tests/verifier.json` and `tests/manifest.json` are identical, and every "Entailed by" quote
      exists verbatim in the visible text.
- [ ] Oracle 1.0 on Harbor, twice.
- [ ] 4 shipped rollouts in band (1–3 of 4), every failure MODEL, zero crashes.
- [ ] The solvability run is a GLM or other-model run at 1.0, never the oracle.
- [ ] The zip contains `review.csv` and `qc_report.html`, and no `tools/`, `consistency/` or `platform/`.

---

## Status

| Phase | State |
|---|---|
| 0 Diagnose | done: both findings reproduced locally |
| 1 Remove contested readings | done: commit `096eba7`, local gold 1.0, all probes 0.0 |
| 2 Baseline battery | **you**: Harbor oracle + 8 GLM runs |
| 3 Hardening H1 → H2 | design ready. Implement H1 on request, then measure. |
| 4–7 Evidence, package, platform | **you**, per the steps above |
