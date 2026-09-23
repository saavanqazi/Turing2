#!/bin/bash
# The golden run. `harbor run --agent oracle` uploads this solution/ directory
# into the container and executes this script; it must leave the workspace in a
# state that scores reward 1.0.
#
# These tasks are graded on delivered files, and the gold deliverables were
# produced and replay-checked by the authoring pipeline
# (nonconnector/build_tasks.py --smoke, then validate_tasks.py), so the reference
# solution installs them rather than recomputing them.
set -euo pipefail

SOLUTION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${HARBOR_TASK_WORKSPACE:-/app}"

mkdir -p "$WORKSPACE"
cp -a "$SOLUTION_DIR/files/." "$WORKSPACE/"

echo "installed gold deliverables into $WORKSPACE:"
ls -1 "$WORKSPACE"
