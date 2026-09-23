#!/usr/bin/env python3
"""score.py — turn per-verifier outcomes into one reward, with a core gate.

The client register's first and highest-volume finding was all-or-nothing aggregation: a
single incidental check collapsing a substantively perfect submission to 0.0, with
"submissions scoring 76/78, 64/65, 63/64 and 30/31" all rewarded exactly the same as
delivering nothing.

So the checks are split by `metadata.tag`:

  core        the checks that decide whether the work is RIGHT — the deliverables exist
              and their graded content is correct. All of them must pass. If one fails
              the answer is wrong, and reward is 0.
  incidental  the checks on how the prose deliverables are written. They carry an explicit
              weight and are added to the reward when they pass. They can never produce a
              0 on their own.

Gold passes everything and scores exactly 1.0, so the oracle gate is unchanged.
"""
import json
import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from rl_world_verifiers.models import VerifierSpec, effective_weights
from rl_world_verifiers.sources.registry import SourceRegistry
from rl_world_verifiers.verifiers import verify_definition

WORKSPACE = Path(os.environ.get("HARBOR_TASK_WORKSPACE", "/app"))
# Harbor mounts the agent trajectory under /logs/agent. Passing it through makes
# `response.*` sources (grade the agent's final chat message) resolvable; when the
# directory is absent those sources fail with the engine's own precondition error
# instead of an unknown-registry crash, and every other source is unaffected.
AGENT_LOGS = Path(os.environ.get("HARBOR_AGENT_LOGS_DIR", "/logs/agent"))
SPEC = VerifierSpec.model_validate_json((TESTS_DIR / "verifier.json").read_text(encoding="utf-8"))
WEIGHTS = effective_weights(SPEC.verifiers)
REGISTRY = SourceRegistry(WORKSPACE, agent_logs_dir=AGENT_LOGS if AGENT_LOGS.is_dir() else None)

results, core_failures, earned = [], [], 0.0
for definition in SPEC.verifiers:
    try:
        outcome = verify_definition(
            definition, REGISTRY, WEIGHTS[definition.name],
            config=SPEC.config, completion_fn=None,
        )["result"]
        ok = bool(outcome["success"])
        detail = outcome.get("error") or outcome.get("reason") or ""
    except Exception as exc:  # an engine error is a failure, never a silent pass
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    tag = definition.metadata.tag or "core"
    results.append({"name": definition.name, "tag": tag, "passed": ok,
                    "weight": WEIGHTS[definition.name], "detail": str(detail)[:200]})
    if ok:
        earned += WEIGHTS[definition.name]
    elif tag == "core":
        core_failures.append(definition.name)

reward = 0.0 if core_failures else round(min(earned, 1.0), 6)
print(json.dumps({
    "reward": reward,
    "core_failures": core_failures,
    "passed": sum(1 for r in results if r["passed"]),
    "total": len(results),
    "checks": results,
}, indent=2))
