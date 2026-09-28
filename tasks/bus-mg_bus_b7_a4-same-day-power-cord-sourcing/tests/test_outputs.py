"""Replays the harness `file_check` spec against the task workspace.

Four labelled lanes (the battery the client preflight requires SHIPPED — its
counterfactual_strength criterion failed every b29t bundle for replaying
positive-only):

  test_deliverable (positive)     every graded assertion passes on the workspace.
  test_incomplete_output          each graded file DELETED -> a check bound to it
                                  must fail. An answer may not omit a deliverable.
  test_negative_corrupted_value   each results.json figure corrupted -> its bound
                                  check(s) must fail. The numbers are load-bearing.
  test_adversarial_extra_key      an unrequested key injected into results.json ->
                                  the key-set guard must fail. Dumping extra output
                                  does not score.

The negative lanes mutate COPIES; the workspace itself is never modified. One
pytest per assertion/case, so Harbor's per-test grid (and the CTRF report) names
exactly what failed. The spec in `verifier.json` (mirrored as `manifest.json`) and the engine in
`rl_world_verifiers/` are copies of what the task harness runs, so a result here
means the same thing it means there.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from rl_world_verifiers.models import VerifierSpec, effective_weights  # noqa: E402
from rl_world_verifiers.sources.registry import SourceRegistry  # noqa: E402
from rl_world_verifiers.verifiers import verify_definition  # noqa: E402

WORKSPACE = Path(os.environ.get("HARBOR_TASK_WORKSPACE", "/app"))
SPEC = VerifierSpec.model_validate_json(
    (TESTS_DIR / "verifier.json").read_text(encoding="utf-8")
)
WEIGHTS = effective_weights(SPEC.verifiers)
REGISTRY = SourceRegistry(WORKSPACE)

RESULTS_FILE = "results.json"
_RAW = SPEC.model_dump()["verifiers"]


def _src(raw):
    return ((raw.get("source") or {}).get("file") or {})


def _det(raw):
    return ((raw.get("assertion") or {}).get("deterministic") or {})


#: every file some check grades
GRADED_PATHS = sorted(
    {_src(raw).get("arguments", {}).get("path") for raw in _RAW} - {None}
)
#: check names per graded file
CHECKS_ON = {
    path: sorted(raw["name"] for raw in _RAW
                 if _src(raw).get("arguments", {}).get("path") == path)
    for path in GRADED_PATHS
}
#: results.json figure -> the check name(s) that pay it
BOUND = {}
#: per-key corruption step: 1, or past the widest declared absolute tolerance among the
#: bound checks — a +1 bump inside a declared tolerance of 1.0 PASSES the check and the
#: lane reads a fully correct gold as "figure is decorative" (measured on the corpus:
#: five tolerance-1.0 checks made three negative tests red on gold, 2026-09-02 review).
TOL_BUMP = {}
for raw in _RAW:
    if (_src(raw).get("type") == "json"
            and _src(raw).get("arguments", {}).get("path") == RESULTS_FILE
            and _det(raw).get("comparison") in ("equals", "approx_equals")):
        key = _det(raw).get("path", "")
        if key.startswith("$.") and "." not in key[2:]:
            BOUND.setdefault(key[2:], set()).add(raw["name"])
            tol = (_det(raw).get("tolerance") or {}).get("absolute") or 0
            TOL_BUMP[key[2:]] = max(TOL_BUMP.get(key[2:], 1), 2 * tol + 1)
    if (_src(raw).get("type") == "json"
            and _src(raw).get("arguments", {}).get("path") == RESULTS_FILE
            and _det(raw).get("comparison") == "object_equals"):
        # compact dialect: one closed object_equals carries every graded key, so
        # each key binds to the object check — the negative lane fires per key
        # either way, and the bump clears the engine's per-key tolerance
        # (default 1e-3 when null)
        for key, key_spec in (
                ((raw.get("assertion") or {}).get("expected") or {})
                .get("keys") or {}).items():
            BOUND.setdefault(key, set()).add(raw["name"])
            tol = (key_spec or {}).get("tolerance")
            tol = 0.001 if tol is None else tol
            TOL_BUMP[key] = max(TOL_BUMP.get(key, 1), 2 * tol + 1)
#: the key-set guard(s) on results.json, if the spec carries one (the legacy
#: not_regex_match guard, or a compact object_equals with the key set closed)
KEYSET_GUARDS = sorted(
    raw["name"] for raw in _RAW
    if _src(raw).get("arguments", {}).get("path") == RESULTS_FILE
    and (_det(raw).get("comparison") in ("not_regex_match", "not_contains")
         or (_det(raw).get("comparison") == "object_equals"
             and (((raw.get("assertion") or {}).get("expected") or {})
                  .get("closed"))))
)


def _failed(root):
    """Names of the checks that fail when the spec replays against `root`."""
    registry = SourceRegistry(Path(root))
    failed = set()
    for definition in SPEC.verifiers:
        outcome = verify_definition(
            definition, registry, WEIGHTS[definition.name],
            config=SPEC.config, completion_fn=None,
        )["result"]
        if not outcome["success"]:
            failed.add(definition.name)
    return failed


def _workspace_copy(tmp):
    dst = Path(tmp) / "mutant"
    shutil.copytree(WORKSPACE, dst)
    return dst


@pytest.mark.parametrize(
    "definition",
    SPEC.verifiers,
    ids=[definition.name for definition in SPEC.verifiers],
)
def test_deliverable(definition):
    outcome = verify_definition(
        definition,
        REGISTRY,
        WEIGHTS[definition.name],
        config=SPEC.config,
        completion_fn=None,
    )["result"]
    detail = outcome.get("error") or outcome.get("reason") or "assertion failed"
    assert outcome["success"], f"{definition.name}: {detail}"


@pytest.mark.parametrize("path", GRADED_PATHS)
def test_incomplete_output(path):
    with tempfile.TemporaryDirectory() as tmp:
        mutant = _workspace_copy(tmp)
        target = mutant / path
        if not target.is_file():
            pytest.skip(f"{path} not present in this workspace")
        target.unlink()
        failed = _failed(mutant)
    assert failed & set(CHECKS_ON[path]), (
        f"deleted {path} and no check bound to it failed — the deliverable "
        "is optional to the grader"
    )


@pytest.mark.parametrize("key", sorted(BOUND))
def test_negative_corrupted_value(key):
    with tempfile.TemporaryDirectory() as tmp:
        mutant = _workspace_copy(tmp)
        results = mutant / RESULTS_FILE
        if not results.is_file():
            pytest.skip("no results.json in this workspace")
        data = json.loads(results.read_text(encoding="utf-8"))
        if key not in data:
            pytest.skip(f"results.json carries no {key!r}")
        value = data[key]
        if isinstance(value, bool):
            data[key] = not value
        elif isinstance(value, (int, float)):
            bump = TOL_BUMP.get(key, 1)
            data[key] = round(value + bump, 6)
        elif isinstance(value, list):
            data[key] = value + ["__corrupted__"]
        else:
            data[key] = str(value) + "_corrupted"
        results.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        failed = _failed(mutant)
    assert failed & BOUND[key], (
        f"corrupted results.json:{key} and none of {sorted(BOUND[key])} failed — "
        "the figure is decorative, not load-bearing"
    )


def test_adversarial_extra_key():
    if not KEYSET_GUARDS:
        pytest.skip("spec carries no key-set guard on results.json")
    with tempfile.TemporaryDirectory() as tmp:
        mutant = _workspace_copy(tmp)
        results = mutant / RESULTS_FILE
        if not results.is_file():
            pytest.skip("no results.json in this workspace")
        data = json.loads(results.read_text(encoding="utf-8"))
        data["unrequested_extra_key"] = 0
        results.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        failed = _failed(mutant)
    assert failed & set(KEYSET_GUARDS), (
        "injected an unrequested results.json key and the key-set guard "
        f"{KEYSET_GUARDS} did not fail — extra output is unbounded"
    )
