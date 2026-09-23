# Phase 2 — accepted grading skeleton ported (grader-only round, "r0")

Baseline commit: d3eddf8 (mined package untouched). This round changes the grader, the
gold plumbing and the format contract. The policy, the data and the six-wine answer are
unchanged, so the baseline GLM battery (once run) is still valid evidence for the mined
version, and the new grader can be re-graded against those rollouts.

## What changed, by file

| File | Change |
|---|---|
| `tests/rl_world_verifiers/` | Replaced with the newer engine from the accepted b7_a4 bundle (`csv.read_rows`, `table_equals`, `object_equals`, `metadata.tag`). |
| `tests/test.sh` | Copied from reference: pytest (grid + CTRF) then `score.py` decides the reward; `reward.txt` from `score.json`. |
| `tests/score.py` | Copied from reference: core-gated, incidental-weighted reward. |
| `tests/test_outputs.py` | Copied from reference: four lanes (positive, deleted deliverable, corrupted figure, extra key). |
| `tests/verifier.json` + `tests/manifest.json` | Rewritten, 10 verifiers (7 core, 3 incidental at 0.05 each). Identical mirror, both written by `compute_gold.py`. |
| `environment/Dockerfile` | Copied from reference: digest-pinned base, `USER root`, engine + document libs. |
| `environment/input/submission_format.md` | New format contract: header, one row per wine keyed by `wine_id`, code join order WM1→WM2→WM3, closed `results.json` key set, count definitions. Closes guesses G1, G9, G10, G11. |
| `instruction.md` | Two lines added pointing at `input/submission_format.md`. Prose otherwise untouched (Phase 3 rewrites it). |
| `solution/compute_gold.py` | New. Stdlib-only generator: gold files, memo, `golden_trajectory.json`, manifest expected values. |
| `solution/solve.sh` | Copied from reference: installs `solution/files/`, emits an ATIF trajectory (non-fatal). |
| `solution/golden_trajectory.json` | New, generated. |
| `.gitattributes` | LF on every text file. |

## Verifier map (forward / backward)

| Instruction / contract ask | Verifier | Tag |
|---|---|---|
| `wine_findings.csv` delivered | `findings_exists` | core |
| header `wine_id,finding` | `findings_header` (case/quote tolerant, first line only) | core |
| one row per wine, every finding, codes in policy order | `findings_table` (rows + `row_set` + closed `columns`, `finding` as text) | core |
| exempt low-margin wine(s) stay compliant | `findings_table_trap` | core |
| memo delivered | `memo_exists` | core |
| memo names each flagged wine | `memo_names_findings` (look-aheads on ids) | incidental 0.05 |
| memo names the exempt wine(s) | `memo_names_trap` | incidental 0.05 |
| memo cites the exempting clause | `memo_cites_futures` (`futures` or `pre-arrival`, anywhere) | incidental 0.05 |
| `results.json` delivered | `results_exists` | core |
| five counts, no other keys | `results_figures` (`object_equals`, closed) | core |

Removed from the mined spec: `schema_present` (superseded by header + closed columns),
per-wine regex checks (superseded by `findings_table`), `futures_not_flagged`
(superseded by `findings_table_trap`), `memo_explains_futures` strict phrasing regex.

## Local replay (this container: no docker / harbor / GLM key)

- `compute_gold.py`: 6 wines → 3 findings, trap W-2, counts 6/1/1/1/3 (matches mined gold).
- `score.py` on gold: reward 1.0, 0 core failures, 10/10 checks.
- `test_outputs.py` on gold: 19 lanes green (10 positive, 3 incomplete-output, 5 corrupted-figure, 1 extra-key).
- Probes through `score.py`:

| Probe | Reward | Core failure |
|---|---|---|
| W-2 flagged MARGIN_TOO_LOW | 0.0 | findings_table_trap |
| duplicate W-3 row | 0.0 | findings_table |
| W-6 row missing | 0.0 | findings_table |
| extra column | 0.0 | findings_header, findings_table |
| compliant_count off by one | 0.0 | results_figures |
| extra key in results.json | 0.0 | results_figures |
| memo reworded, no word "exempt" | 1.0 | — |
| memo omits trap W-2 | 0.95 | — (incidental only) |
| quoted cells, `Compliant`, CRLF | 1.0 | — |
| rows reordered | 1.0 | — |

## Harbor results (Windows host, 2026-09-23)

| Job | Package | Reward |
|---|---|---|
| `oracle-g205-baseline` | d3eddf8 (mined) | 1.0 |
| `glm-g205-baseline` | d3eddf8 (mined), terminus-2, GLM-5.2, -k 4 | 1.0, 1.0, 1.0, 1.0 (4/4) |
| `oracle-g205-r0` | 07fe3c8 (new grader) | 1.0, 0 exceptions, 12 s |

No GLM battery for r0: the answer and difficulty are unchanged from the baseline.

## Commands used

```
source ~/.config/harbor/env
TASK=<repo>/tasks/gen-g205-wine-purchasing-margin-audit
# Phase 1 baseline (check out d3eddf8 first, or use the mined zip)
harbor run -p "$TASK" -a oracle --ve OPENAI_API_KEY="$OPENAI_API_KEY" --ve OPENAI_BASE_URL="$OPENAI_BASE_URL" -o /tmp/harbor-jobs --job-name oracle-g205-baseline -n 1 -y
harbor run -c glm-harbor-config.json -n 1 -y            # smoke
harbor run -c glm-harbor-config.json -n 3 -k 4 -y       # battery  → glm-g205-baseline
# Phase 2 oracle on this commit
harbor run -p "$TASK" -a oracle --ve OPENAI_API_KEY="$OPENAI_API_KEY" --ve OPENAI_BASE_URL="$OPENAI_BASE_URL" -o /tmp/harbor-jobs --job-name oracle-g205-r0 -n 1 -y
cat /tmp/harbor-jobs/oracle-g205-r0/*/verifier/reward.txt   # expect 1.0
```
Record job names and rewards in `phase1_guess_list.md` and here.
