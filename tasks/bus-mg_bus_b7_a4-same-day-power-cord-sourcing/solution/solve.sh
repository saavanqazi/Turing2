#!/bin/bash
# The golden run. `harbor run --agent oracle` uploads this solution/ directory
# into the container and executes this script; it must leave the workspace in a
# state that scores reward 1.0.
#
# These tasks are graded on delivered files, and the gold deliverables were
# produced and replay-checked by the authoring pipeline
# (nonconnector/build_tasks.py --smoke, then validate_tasks.py), so the reference
# solution installs them rather than recomputing them.
#
# It then emits an ATIF trajectory from solution/golden_trajectory.json so the
# oracle trial carries agent/trajectory.json, which the delivery format requires.
# Harbor's oracle agent records only this script's stdout, so a bundle that wants
# a trajectory has to write one itself — the connector bundles do the same thing
# via emit_oracle_trajectory.py. The emit step is deliberately non-fatal: grading
# must never depend on evidence generation.
set -euo pipefail

SOLUTION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${HARBOR_TASK_WORKSPACE:-/app}"

mkdir -p "$WORKSPACE"
cp -a "$SOLUTION_DIR/files/." "$WORKSPACE/"

echo "installed gold deliverables into $WORKSPACE:"
ls -1 "$WORKSPACE"

# --- ATIF trajectory for the trial record (non-fatal) ---------------------
if mkdir -p /logs/agent 2>/dev/null && [ -w /logs/agent ]; then
  LOG_DIR=/logs/agent
else
  LOG_DIR="$WORKSPACE/.oracle_logs"
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  echo "oracle: /logs/agent not writable; using $LOG_DIR"
fi
export LOG_DIR SOLUTION_DIR
python3 - <<'PY' || echo "oracle: trajectory emit failed (non-fatal)" >&2
import json, os
from datetime import datetime, timezone
from pathlib import Path

sol = Path(os.environ["SOLUTION_DIR"])
log = Path(os.environ["LOG_DIR"])
golden = json.loads((sol / "golden_trajectory.json").read_text())
now = datetime.now(timezone.utc).isoformat()

steps = []
for index, step in enumerate(golden, start=1):
    steps.append({
        "step_id": index,
        "timestamp": now,
        "source": "agent",
        "message": f"Oracle step {step.get('name') or 'bash'}",
        "tool_calls": [{
            "tool_call_id": f"oracle-step-{index}",
            "function_name": step.get("name") or "bash",
            "arguments": step.get("arguments") or {},
        }],
        "observation": {"results": []},
    })
delivered = sorted(p.name for p in (sol / "files").iterdir()) if (sol / "files").is_dir() else []
steps.append({
    "step_id": len(steps) + 1,
    "timestamp": now,
    "source": "agent",
    "message": "Finished.",
    "tool_calls": [{
        "tool_call_id": "oracle-finish",
        "function_name": "finish",
        "arguments": {"message": "Deliverables written: " + ", ".join(delivered)},
    }],
    "observation": {"results": []},
})

traj = {
    "schema_version": "ATIF-v1.5",
    "session_id": "oracle-replay",
    "agent": {"name": "oracle", "version": "1.0.0",
              "model_name": None, "model_provider": None},
    "steps": steps,
}
log.mkdir(parents=True, exist_ok=True)
(log / "trajectory.json").write_text(json.dumps(traj, indent=2) + "\n")
print(f"oracle: wrote {log/'trajectory.json'} ({len(steps)} steps)")
PY
