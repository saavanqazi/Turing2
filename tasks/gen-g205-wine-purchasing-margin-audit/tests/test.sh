#!/bin/bash
# Harbor verifier entrypoint. Harbor copies this to /tests/test.sh and runs it from the task
# working directory; reward is read back from /logs/verifier/reward.txt.
#
# Two steps, deliberately separate:
#   1. pytest, for Harbor's per-check grid and the CTRF report — one test per verifier, so a
#      failure names the deliverable check that failed.
#   2. score.py, which decides the reward. It is NOT the pytest exit code: core checks
#      decide whether the work is right and incidental checks grade how the prose is
#      written, and a differently-worded memo must not score the same as delivering nothing.
mkdir -p /logs/verifier

cd /app || exit 1

python3 -m pytest \
    --ctrf /logs/verifier/ctrf.json \
    /tests/test_outputs.py \
    -rA

python3 /tests/score.py > /logs/verifier/score.json
python3 -I -c "import json;print(json.load(open('/logs/verifier/score.json'))['reward'])" \
    > /logs/verifier/reward.txt

cat /logs/verifier/score.json
exit 0
