#!/bin/bash
# Local grader replay without Harbor: regenerate the gold, score it, run every test lane.
# Needs: pytest==8.4.1 pytest-json-ctrf==0.3.5 pydantic==2.12.5 "jsonpath-ng>=1.6,<2" "tenacity>=9.0,<10"
set -euo pipefail
TASK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK"
python3 solution/compute_gold.py
diff -q tests/verifier.json tests/manifest.json
WS="$(mktemp -d)"
cp solution/files/* "$WS"/
echo "--- score.py"
HARBOR_TASK_WORKSPACE="$WS" python3 tests/score.py | python3 -c "import json,sys; d=json.load(sys.stdin); print('reward', d['reward'], 'core_failures', d['core_failures'], d['passed'], '/', d['total'])"
echo "--- test_outputs.py"
HARBOR_TASK_WORKSPACE="$WS" python3 -m pytest tests/test_outputs.py -q
rm -rf "$WS"
