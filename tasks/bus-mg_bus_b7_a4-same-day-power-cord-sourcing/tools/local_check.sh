#!/bin/bash
# Local grader replay without Harbor: regenerate the gold, score it, run the pytest lane and
# the probes. Not a substitute for `harbor run -a oracle` — run that too.
# Needs: pytest pytest-json-ctrf pydantic "jsonpath-ng>=1.6,<2" "tenacity>=9.0,<10"
set -euo pipefail
TASK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-python3}"
cd "$TASK"
"$PY" solution/compute_gold.py > /dev/null
diff -q tests/verifier.json tests/manifest.json
WS="$(mktemp -d)"
cp solution/files/* "$WS"/
echo "--- score.py on gold"
HARBOR_TASK_WORKSPACE="$WS" "$PY" tests/score.py | "$PY" -c "import json,sys; d=json.load(sys.stdin); print('reward', d['reward'], 'core_failures', d['core_failures'], d['passed'], '/', d['total'])"
echo "--- test_outputs.py on gold"
(cd "$WS" && HARBOR_TASK_WORKSPACE="$WS" "$PY" -m pytest "$TASK/tests/test_outputs.py" -q -p no:cacheprovider | tail -1)
rm -rf "$WS"
echo "--- probes"
"$PY" tools/probes.py
