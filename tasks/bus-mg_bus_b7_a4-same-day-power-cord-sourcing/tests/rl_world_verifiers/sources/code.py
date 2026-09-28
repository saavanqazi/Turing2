"""Bounded execution sources for Python deliverables.

The task agent already receives a shell inside the task container, so that
container remains the operating-system security boundary. These commands add a
second, narrower boundary for grading: no shell, a scrubbed environment, a
fresh directory containing only declared files, bounded output, a wall-clock
timeout, and process-tree termination. In the root-run Linux production image,
each execution also uses a throwaway UID so trusted files are kernel-enforced
read-only and descendants remain killable after creating a new session. Pytest
reports through a bounded nonce-bound socket rather than a candidate-writable
file. Candidate code is never executed in the graders' Python process.
"""

from __future__ import annotations

import ast
import ctypes.util
import hashlib
import os
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..models import StrictModel
from ..source_types import (
    SourceAuthoringError,
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

_MAX_CODE_BYTES = 1_000_000
_MAX_STAGED_FILES = 64
_MAX_TRUSTED_FILE_BYTES = 8 * 1024 * 1024
_MAX_TRUSTED_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_MAX_AGGREGATE_RSS_BYTES = 768 * 1024 * 1024
_MAX_PROCESSES = 16
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_OPEN_FILES = 64
_MAX_FILESYSTEM_GROWTH_BYTES = 64 * 1024 * 1024
_MAX_CREATED_FILES = 4_096
_RESOURCE_POLL_SECONDS = 0.01
_READ_CHUNK = 4096
_CONTROL_NONCE_BYTES = 32
_MAX_CONTROL_BYTES = 256
_PYTEST_CONTROL_FORMAT = "!8Q"
_ISOLATED_UID_MIN = 200_000
_ISOLATED_UID_SPAN = 800_000
_IDENTITY_LOCK = threading.Lock()
_RESERVED_UIDS: set[int] = set()
_SANDBOX_ERROR_MARKER = "OBI_CODE_SANDBOX_ERROR:"

_RUNNER_SOURCE = """\
from pathlib import Path
import runpy
import sys

candidate = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(candidate.parent))
sys.argv = [str(candidate), *sys.argv[2:]]
runpy.run_path(str(candidate), run_name="__main__")
"""

_LIMIT_RUNNER_SOURCE = f"""\
import ctypes
import ctypes.util
import errno
import os
import resource
import sys


status_fd = -1


def sandbox_fail(message):
    if status_fd >= 0:
        try:
            os.write(status_fd, ("ERR:" + message).encode()[:1024])
            os.close(status_fd)
        except OSError:
            pass
    os._exit(126)


def install_landlock(write_root):
    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneathAttr(ctypes.Structure):
        _fields_ = [
            ("allowed_access", ctypes.c_uint64),
            ("parent_fd", ctypes.c_int32),
        ]

    create_ruleset, add_rule, restrict_self = 444, 445, 446
    version = libc.syscall(create_ruleset, 0, 0, 1)
    if version < 1:
        sandbox_fail("Landlock filesystem isolation is unavailable")
    rights = sum(1 << bit for bit in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12))
    if version >= 2:
        rights |= 1 << 13
    if version >= 3:
        rights |= 1 << 14
    ruleset = RulesetAttr(rights)
    ruleset_fd = libc.syscall(
        create_ruleset, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0
    )
    if ruleset_fd < 0:
        sandbox_fail("could not create Landlock ruleset")
    parent_fd = os.open(write_root, os.O_PATH | os.O_CLOEXEC)
    try:
        path_rule = PathBeneathAttr(rights, parent_fd)
        if libc.syscall(
            add_rule, ruleset_fd, 1, ctypes.byref(path_rule), 0
        ) != 0:
            sandbox_fail("could not add Landlock workspace rule")
        if libc.syscall(restrict_self, ruleset_fd, 0) != 0:
            sandbox_fail("could not enforce Landlock filesystem isolation")
    finally:
        os.close(parent_fd)
        os.close(ruleset_fd)


status_fd = int(sys.argv.pop(1))
cpu_seconds = max(1, int(sys.argv.pop(1)))
for resource_id, soft, hard in (
    (resource.RLIMIT_AS, {_MAX_ADDRESS_SPACE_BYTES}, {_MAX_ADDRESS_SPACE_BYTES}),
    (resource.RLIMIT_NPROC, {_MAX_PROCESSES}, {_MAX_PROCESSES}),
    (resource.RLIMIT_FSIZE, {_MAX_FILE_BYTES}, {_MAX_FILE_BYTES}),
    (resource.RLIMIT_NOFILE, {_MAX_OPEN_FILES}, {_MAX_OPEN_FILES}),
    (resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 1),
    (resource.RLIMIT_CORE, 0, 0),
):
    resource.setrlimit(resource_id, (soft, hard))
try:
    library_name = ctypes.util.find_library("seccomp")
    if not library_name:
        sandbox_fail("libseccomp is unavailable")
    library = ctypes.CDLL(library_name, use_errno=True)
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    class ScmpArgCmp(ctypes.Structure):
        _fields_ = [
            ("arg", ctypes.c_uint),
            ("op", ctypes.c_uint),
            ("datum_a", ctypes.c_uint64),
            ("datum_b", ctypes.c_uint64),
        ]

    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(ScmpArgCmp),
    ]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        sandbox_fail("could not enable no-new-privileges")
    install_landlock(os.getcwd())
    context = library.seccomp_init(0x7FFF0000)
    if not context:
        sandbox_fail("could not initialize seccomp")
    deny = 0x00050000 | errno.EPERM
    # Permit local AF_UNIX IPC needed by LibreOffice and multiprocessing, but
    # deny every internet/device socket family. No network descriptor is passed
    # into this process, and io_uring is disabled below to prevent bypassing the
    # socket-family rule.
    socket_number = library.seccomp_syscall_resolve_name(b"socket")
    socketcall_number = library.seccomp_syscall_resolve_name(b"socketcall")
    if socket_number < 0:
        sandbox_fail("could not resolve the socket syscall")
    comparison = ScmpArgCmp(0, 1, 1, 0)  # arg 0 != AF_UNIX
    if library.seccomp_rule_add_array(
        context, deny, socket_number, 1, ctypes.byref(comparison)
    ) != 0:
        sandbox_fail("could not install network socket rule")
    if socketcall_number >= 0:
        if library.seccomp_rule_add(context, deny, socketcall_number, 0) != 0:
            sandbox_fail("could not install socketcall rule")
    clone_number = library.seccomp_syscall_resolve_name(b"clone")
    clone3_number = library.seccomp_syscall_resolve_name(b"clone3")
    if clone_number < 0 or clone3_number < 0:
        sandbox_fail("could not resolve mandatory clone syscalls")
    for namespace_flag in (
        0x00020000, 0x02000000, 0x04000000, 0x08000000,
        0x10000000, 0x20000000, 0x40000000,
    ):
        comparison = ScmpArgCmp(0, 7, namespace_flag, namespace_flag)
        if library.seccomp_rule_add_array(
            context, deny, clone_number, 1, ctypes.byref(comparison)
        ) != 0:
            sandbox_fail("could not install clone namespace rule")
    unsupported = 0x00050000 | errno.ENOSYS
    if library.seccomp_rule_add(context, unsupported, clone3_number, 0) != 0:
        sandbox_fail("could not disable clone3")
    blocked = (
        b"io_uring_setup", b"io_uring_enter", b"io_uring_register",
        b"ptrace", b"process_vm_readv", b"process_vm_writev", b"bpf",
        b"perf_event_open", b"mount", b"umount2", b"setns", b"unshare",
    )
    for name in blocked:
        number = library.seccomp_syscall_resolve_name(name)
        if number < 0:
            sandbox_fail("could not resolve mandatory syscall " + name.decode())
        if library.seccomp_rule_add(context, deny, number, 0) != 0:
            sandbox_fail("could not install seccomp rule for " + name.decode())
    if library.seccomp_load(context) != 0:
        sandbox_fail("could not load seccomp filter")
    library.seccomp_release(context)
    os.write(status_fd, b"OK")
    os.close(status_fd)
    status_fd = -1
except BaseException as exc:
    sandbox_fail(type(exc).__name__ + ":" + str(exc))
os.execv(sys.argv[1], sys.argv[1:])
"""

_PYTEST_RUNNER_SOURCE = """\
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys

import pytest


class CandidateRunner:
    # This worker is exec'd under the unprivileged candidate UID. The trusted
    # pytest controller remains root-owned and never imports candidate code.
    _WORKER = r'''\
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys

def decode_value(value):
    if isinstance(value, dict) and set(value) == {"__obi_path__"}:
        return Path(value["__obi_path__"])
    return value


request = json.loads(sys.stdin.readline(), object_hook=decode_value)
candidate = Path(sys.argv[1])
sys.path.insert(0, str(candidate.parent))
stdout = io.StringIO()
stderr = io.StringIO()
try:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        namespace = runpy.run_path(str(candidate), run_name="obi_candidate_module")
        operation = request["operation"]
        if operation == "value":
            value = namespace[request["name"]]
        elif operation == "call":
            value = namespace[request["name"]](*request["args"], **request["kwargs"])
        else:
            raise ValueError("unsupported candidate operation")
    response = {"ok": True, "value": value}
except BaseException as exc:
    response = {
        "ok": False,
        "error": type(exc).__name__ + ": " + str(exc),
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }
print("OBI_CANDIDATE_RESULT:" + json.dumps(response, ensure_ascii=False))
'''

    def __init__(self, path, uid, gid):
        self.path = Path(path)
        self.uid = uid
        self.gid = gid

    def _environment(self):
        allowed = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "PATHEXT")
        environment = {name: os.environ[name] for name in allowed if os.environ.get(name)}
        environment.update({
            "HOME": str(self.path.parent),
            "TMPDIR": str(self.path.parent.parent / ".tmp"),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        })
        return environment

    def run(self, args=(), *, stdin="", timeout=10.0):
        timeout = max(0.1, min(float(timeout), 30.0))
        try:
            return subprocess.run(
                [sys.executable, "-I", str(self.path), *[str(value) for value in args]],
                cwd=self.path.parent,
                env=self._environment(),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                user=self.uid,
                group=self.gid,
                extra_groups=(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError("candidate subprocess timed out") from exc

    @staticmethod
    def _encode(value):
        if isinstance(value, Path):
            return {"__obi_path__": str(value)}
        raise TypeError("candidate call arguments must be JSON values or paths")

    def _request(self, request, timeout):
        timeout = max(0.1, min(float(timeout), 30.0))
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", self._WORKER, str(self.path)],
                cwd=self.path.parent,
                env=self._environment(),
                input=json.dumps(request, default=self._encode) + "\\n",
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                user=self.uid,
                group=self.gid,
                extra_groups=(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError("candidate subprocess timed out") from exc
        if completed.returncode != 0:
            raise AssertionError(
                "candidate subprocess exited " + str(completed.returncode)
                + ": " + completed.stderr[-2000:]
            )
        marker = "OBI_CANDIDATE_RESULT:"
        lines = [line for line in completed.stdout.splitlines() if line.startswith(marker)]
        if len(lines) != 1:
            raise AssertionError("candidate subprocess returned no valid result")
        try:
            response = json.loads(lines[0][len(marker):])
        except (TypeError, ValueError) as exc:
            raise AssertionError("candidate subprocess returned malformed JSON") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            error = response.get("error", "unknown candidate error") if isinstance(response, dict) else "invalid response"
            raise AssertionError(str(error))
        return response.get("value")

    def call(self, name, *args, timeout=10.0, **kwargs):
        return self._request(
            {"operation": "call", "name": name, "args": args, "kwargs": kwargs},
            timeout,
        )

    def value(self, name, *, timeout=10.0):
        return self._request({"operation": "value", "name": name}, timeout)

    def temp_path(self, name):
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("candidate temp names must be simple filenames")
        return self.path.parent.parent / ".tmp" / name


def _controller():
    class ResultPlugin:
        def __init__(self, candidate_runner):
            self._candidate = candidate_runner
            self.collected = 0
            self.counts = {
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "xfailed": 0,
                "xpassed": 0,
            }

        @pytest.fixture
        def candidate(self):
            return self._candidate

        @pytest.fixture
        def candidate_path(self):
            return self._candidate.path

        def pytest_collection_finish(self, session):
            self.collected = len(session.items)

        def pytest_collectreport(self, report):
            if report.failed:
                self.counts["errors"] += 1

        def pytest_runtest_logreport(self, report):
            was_xfail = hasattr(report, "wasxfail")
            if report.when == "call":
                if report.passed:
                    self.counts["xpassed" if was_xfail else "passed"] += 1
                elif report.failed:
                    self.counts["failed"] += 1
                elif report.skipped:
                    self.counts["xfailed" if was_xfail else "skipped"] += 1
            elif report.failed:
                self.counts["errors"] += 1
            elif report.skipped and report.when == "setup":
                self.counts["xfailed" if was_xfail else "skipped"] += 1

    control = socket.socket(fileno=int(sys.argv.pop(1)))
    candidate_uid = int(sys.argv.pop(1))
    candidate_gid = int(sys.argv.pop(1))
    candidate_path = Path(sys.argv.pop(1)).resolve(strict=True)
    nonce = bytearray()
    while len(nonce) < 32:
        chunk = control.recv(32 - len(nonce))
        if not chunk:
            raise SystemExit(120)
        nonce.extend(chunk)

    candidate_root = candidate_path.parent
    root = candidate_root.parent
    sys.path.insert(0, str(root))
    import types
    runtime = types.ModuleType("obi_candidate_runtime")
    runtime.candidate = CandidateRunner(candidate_path, candidate_uid, candidate_gid)
    sys.modules["obi_candidate_runtime"] = runtime

    def audit_guard(event, args):
        if event != "open" or not args or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        try:
            opened = Path(args[0]).resolve(strict=False)
            opened.relative_to(candidate_root)
        except (OSError, RuntimeError, ValueError):
            return
        raise PermissionError("trusted pytest may execute candidate code only through the candidate fixture")

    sys.addaudithook(audit_guard)
    plugin = ResultPlugin(runtime.candidate)
    exit_code = pytest.main(
        ["-q", "--tb=short", "--disable-warnings", "--confcutdir=input", "-p", "no:cacheprovider", "-p", "no:logging", *sys.argv[1:]],
        plugins=[plugin],
    )
    payload = struct.pack(
        "!8Q",
        int(exit_code),
        plugin.collected,
        plugin.counts["passed"],
        plugin.counts["failed"],
        plugin.counts["errors"],
        plugin.counts["skipped"],
        plugin.counts["xfailed"],
        plugin.counts["xpassed"],
    )
    control.sendall(bytes(nonce) + payload)
    control.close()
    raise SystemExit(int(exit_code))


_controller()
"""


def _module_key(value: str) -> str:
    """Return a conservative cross-filesystem Python-module identity."""
    return unicodedata.normalize("NFKC", PurePosixPath(value).name).casefold()


def _validate_python_name(value: str) -> str:
    """Require one root-level Python deliverable name."""
    normalised = value.replace("\\", "/")
    pure = PurePosixPath(normalised)
    if (
        not normalised
        or len(normalised) > 255
        or "\x00" in normalised
        or pure.is_absolute()
        or len(pure.parts) != 1
        or any(token in normalised for token in ("*", "?", "[", "]"))
        or pure.suffix.lower() != ".py"
    ):
        raise ValueError("code execution requires a .py file at the workspace root")
    if _module_key(normalised) == "conftest.py":
        raise ValueError("candidate conftest.py files are not allowed")
    return normalised


def _validate_python_names(values: list[str]) -> list[str]:
    return [_validate_python_name(value) for value in values]


def _validate_seeded_name(value: str, *, python_only: bool) -> str:
    """Require one exact path below the declared input directory."""
    normalised = value.replace("\\", "/")
    pure = PurePosixPath(normalised)
    if (
        not normalised
        or len(normalised) > 4096
        or "\x00" in normalised
        or pure.is_absolute()
        or not pure.parts
        or pure.parts[0] != "input"
        or len(pure.parts) < 2
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(token in normalised for token in ("*", "?", "[", "]"))
    ):
        raise ValueError("trusted paths must be exact relative paths below input/")
    if python_only and pure.suffix.lower() != ".py":
        raise ValueError("code.run_pytest test_paths must contain .py files")
    return normalised


def _validate_strings(values: list[str], field_name: str) -> list[str]:
    for value in values:
        if "\x00" in value:
            raise ValueError(f"{field_name} entries must not contain NUL bytes")
        if len(value) > 4096:
            raise ValueError(f"{field_name} entries must be at most 4096 characters")
    return values


class RunPythonInput(StrictModel):
    """Arguments for ``code.run_python``."""

    path: str
    support_paths: list[str] = Field(default_factory=list, max_length=31)
    fixture_paths: list[str] = Field(default_factory=list, max_length=_MAX_STAGED_FILES)
    args: list[str] = Field(default_factory=list, max_length=32)
    stdin: str = Field(default="", max_length=100_000)
    timeout_seconds: float = Field(default=10.0, ge=0.05, le=60.0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_python_name(value)

    @field_validator("support_paths")
    @classmethod
    def validate_support_paths(cls, values: list[str]) -> list[str]:
        return _validate_python_names(values)

    @field_validator("fixture_paths")
    @classmethod
    def validate_fixture_paths(cls, values: list[str]) -> list[str]:
        return [_validate_seeded_name(value, python_only=False) for value in values]

    @field_validator("args")
    @classmethod
    def validate_args(cls, values: list[str]) -> list[str]:
        return _validate_strings(values, "args")

    @model_validator(mode="after")
    def reject_duplicate_files(self) -> RunPythonInput:
        _require_unique([self.path, *self.support_paths], "candidate paths")
        _require_unique(self.fixture_paths, "fixture paths")
        return self


class RunPythonOutput(StrictModel):
    """Bounded result from one Python-script invocation."""

    passed: bool
    score: float
    syntax_valid: bool
    executed: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool
    process_group_terminated: bool
    resource_limit_exceeded: bool
    trusted_inputs_unchanged: bool
    duration_ms: int


class RunPytestInput(StrictModel):
    """Arguments for ``code.run_pytest``."""

    path: str
    support_paths: list[str] = Field(default_factory=list, max_length=31)
    test_paths: list[str] = Field(min_length=1, max_length=32)
    fixture_paths: list[str] = Field(default_factory=list, max_length=_MAX_STAGED_FILES)
    timeout_seconds: float = Field(default=30.0, ge=0.05, le=120.0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_python_name(value)

    @field_validator("support_paths")
    @classmethod
    def validate_support_paths(cls, values: list[str]) -> list[str]:
        return _validate_python_names(values)

    @field_validator("test_paths")
    @classmethod
    def validate_test_paths(cls, values: list[str]) -> list[str]:
        return [_validate_seeded_name(value, python_only=True) for value in values]

    @field_validator("fixture_paths")
    @classmethod
    def validate_fixture_paths(cls, values: list[str]) -> list[str]:
        return [_validate_seeded_name(value, python_only=False) for value in values]

    @model_validator(mode="after")
    def reject_duplicate_files(self) -> RunPytestInput:
        _require_unique([self.path, *self.support_paths], "candidate paths")
        _require_unique(self.test_paths, "test paths")
        _require_unique(self.fixture_paths, "fixture paths")
        _require_unique(
            [*self.test_paths, *self.fixture_paths], "trusted test and fixture paths"
        )
        trusted_python_names = {
            _module_key(path)
            for path in [*self.test_paths, *self.fixture_paths]
            if PurePosixPath(path).suffix.lower() == ".py"
        }
        produced_names = {
            _module_key(path) for path in [self.path, *self.support_paths]
        }
        overlap = sorted(trusted_python_names & produced_names)
        if overlap:
            raise ValueError(
                "trusted Python files must not shadow produced modules: "
                + ", ".join(overlap)
            )
        return self


class RunPytestOutput(StrictModel):
    """Pytest result with strict pass/fail and a continuous passed-test ratio."""

    passed: bool
    score: float
    syntax_valid: bool
    executed: bool
    exit_code: int | None
    collected: int
    passed_tests: int
    failed: int
    errors: int
    skipped: int
    xfailed: int
    xpassed: int
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool
    process_group_terminated: bool
    resource_limit_exceeded: bool
    trusted_inputs_unchanged: bool
    duration_ms: int


@dataclass(frozen=True)
class _ExecutionIdentity:
    uid: int
    gid: int


@dataclass(frozen=True)
class _ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool
    process_group_terminated: bool
    resource_limit_exceeded: bool
    duration_ms: int
    control_payload: bytes | None = None


def _require_unique(values: list[str], description: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{description} must not contain duplicates")
    if all(PurePosixPath(value).suffix.lower() == ".py" for value in values):
        module_keys = [_module_key(value) for value in values]
        if len(module_keys) != len(set(module_keys)):
            raise ValueError(
                f"{description} must remain distinct on case-insensitive filesystems"
            )


def _syntax_error(paths: list[Path]) -> str | None:
    """Return a bounded syntax-error string, or ``None`` when all files parse."""
    for path in paths:
        if path.stat().st_size > _MAX_CODE_BYTES:
            raise SourceDataError(
                f"Python deliverable exceeds {_MAX_CODE_BYTES} bytes: {path.name}"
            )
        data = path.read_bytes()
        try:
            ast.parse(data, filename=path.name, mode="exec")
        except (SyntaxError, UnicodeError, ValueError) as exc:
            return f"{type(exc).__name__}: {exc}"
    return None


def _safe_environment(temp_root: Path) -> dict[str, str]:
    """Build a minimal environment with no inherited credentials or proxies."""
    environment: dict[str, str] = {}
    for name in ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "PATHEXT"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    temp_dir = temp_root / ".tmp"
    temp_dir.mkdir(mode=0o700)
    environment.update(
        {
            "HOME": str(temp_root),
            "TMPDIR": str(temp_dir),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    return environment


def _pids_for_uid(uid: int, *, include_zombies: bool = True) -> list[int]:
    """Return Linux processes carrying ``uid``, optionally excluding zombies."""
    proc = Path("/proc")
    if not proc.is_dir():
        raise SourceCapabilityError("code-execution process census is unavailable")
    matches: list[int] = []
    try:
        entries = tuple(proc.iterdir())
    except OSError as exc:
        raise SourceCapabilityError(
            "code-execution process census is unavailable"
        ) from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = status.splitlines()
        state = next((line for line in lines if line.startswith("State:")), "")
        if not include_zombies and "Z" in state.split()[1:2]:
            continue
        for line in lines:
            if not line.startswith("Uid:"):
                continue
            try:
                process_uids = {int(value) for value in line.split()[1:5]}
            except ValueError:
                break
            if uid in process_uids:
                try:
                    matches.append(int(entry.name))
                except ValueError:  # guarded by isdigit; retain fail-closed parsing
                    continue
            break
    return matches


def _execution_usage(uid: int) -> tuple[int, int]:
    """Return process count and aggregate resident bytes for one Linux UID."""
    pids = _pids_for_uid(uid, include_zombies=False)
    resident_bytes = 0
    for pid in pids:
        try:
            status = Path(f"/proc/{pid}/status").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                try:
                    resident_bytes += int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    pass
                break
    return len(pids), resident_bytes


def _tree_usage(
    path: Path, *, stop_after_bytes: int, stop_after_files: int
) -> tuple[int, int]:
    """Measure sandbox files, stopping once either quota is exceeded."""
    total = 0
    count = 0
    for directory, _, filenames in os.walk(path):
        for filename in filenames:
            count += 1
            if count > stop_after_files:
                return total, count
            try:
                candidate = Path(directory, filename)
                metadata = candidate.lstat()
                if stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
            except OSError:
                continue
            if total > stop_after_bytes:
                return total, count
    return total, count


def _require_resource_limit_capability() -> None:
    """Fail before execution when Linux identity/resource controls are absent."""
    if (
        sys.platform != "linux"
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
        or not Path("/proc/self/status").is_file()
    ):
        raise SourceCapabilityError(
            "code execution requires a root-run Linux verifier with readable /proc"
        )
    if not ctypes.util.find_library("seccomp"):
        raise SourceCapabilityError(
            "code execution requires libseccomp network isolation"
        )
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - production is Linux
        raise SourceCapabilityError(
            "code execution requires POSIX resource limits"
        ) from exc
    required = (
        (resource.RLIMIT_AS, _MAX_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_NPROC, _MAX_PROCESSES),
        (resource.RLIMIT_FSIZE, _MAX_FILE_BYTES),
        (resource.RLIMIT_NOFILE, _MAX_OPEN_FILES),
    )
    for resource_id, requested in required:
        _soft, hard = resource.getrlimit(resource_id)
        if hard != resource.RLIM_INFINITY and hard < requested:
            raise SourceCapabilityError(
                "verifier host cannot install required code-execution limits"
            )


def _probe_execution_census(identity: _ExecutionIdentity) -> None:
    """Prove /proc reports one persistent process under the reserved UID."""
    try:
        sleeper = subprocess.Popen(
            ["/bin/sleep", "5"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            user=identity.uid,
            group=identity.gid,
            extra_groups=(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceCapabilityError(
            "could not start code-execution process-census probe"
        ) from exc
    try:
        deadline = time.monotonic() + 1.0
        while sleeper.poll() is None and time.monotonic() < deadline:
            if sleeper.pid in _pids_for_uid(identity.uid, include_zombies=False):
                return
            time.sleep(0.01)
        raise SourceCapabilityError(
            "code-execution process census is not trustworthy"
        )
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        sleeper.wait(timeout=2)


def _reserve_execution_identity() -> _ExecutionIdentity | None:
    """Reserve a throwaway Linux UID when the root-run container permits it."""
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    for _ in range(128):
        uid = _ISOLATED_UID_MIN + secrets.randbelow(_ISOLATED_UID_SPAN)
        with _IDENTITY_LOCK:
            if uid in _RESERVED_UIDS or _pids_for_uid(uid):
                continue
            _RESERVED_UIDS.add(uid)
            identity = _ExecutionIdentity(uid=uid, gid=uid)
        try:
            _probe_execution_census(identity)
        except Exception:
            with _IDENTITY_LOCK:
                _RESERVED_UIDS.discard(uid)
            raise
        return identity
    raise SourceCapabilityError(
        "could not allocate an isolated code-execution identity"
    )


def _release_execution_identity(identity: _ExecutionIdentity | None) -> None:
    if identity is None:
        return
    with _IDENTITY_LOCK:
        _RESERVED_UIDS.discard(identity.uid)


def _prepare_isolated_sandbox(cwd: Path, identity: _ExecutionIdentity | None) -> None:
    """Make staged inputs root-owned/read-only and only TMPDIR writable."""
    if identity is None:
        return
    temp_dir = cwd / ".tmp"
    for directory, _, filenames in os.walk(cwd):
        current = Path(directory)
        if current == cwd:
            # Permit ordinary program output while the sticky bit prevents the
            # throwaway UID from replacing root-owned staged entries.
            current.chmod(0o1777)
        elif current != temp_dir:
            current.chmod(0o555)
        for filename in filenames:
            (current / filename).chmod(0o444)
    os.chown(temp_dir, identity.uid, identity.gid)
    temp_dir.chmod(0o700)


def _protect_pytest_controller_files(cwd: Path) -> None:
    """Hide trusted tests, controller code, and proxies from the candidate UID."""
    for root in (cwd / "input", cwd / ".verifier"):
        if not root.exists():
            continue
        for directory, directories, filenames in os.walk(root, topdown=False):
            for filename in filenames:
                (Path(directory) / filename).chmod(0o400)
            for child in directories:
                (Path(directory) / child).chmod(0o500)
            Path(directory).chmod(0o500)
    for proxy in cwd.glob("*.py"):
        if proxy.is_file():
            proxy.chmod(0o400)


def _terminate_execution_identity(identity: _ExecutionIdentity | None) -> bool:
    """Kill every process under a per-execution UID, including new sessions."""
    if identity is None:
        return False
    terminated = False
    for _ in range(20):
        pids = _pids_for_uid(identity.uid, include_zombies=False)
        if not pids:
            break
        terminated = True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        time.sleep(0.01)
    return terminated


def _kill_process_group(process: subprocess.Popen[Any]) -> bool:
    """Terminate the candidate group, including children after parent exit."""
    try:
        if os.name == "posix":
            # The process-group id survives its leader while descendants live,
            # so do not return merely because the direct child has exited.
            os.killpg(process.pid, signal.SIGKILL)
            return True
        if process.poll() is None:  # pragma: no cover - production and CI are POSIX
            process.kill()
            return True
    except ProcessLookupError:
        return False
    return False


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    stdin: str,
    timeout_seconds: float,
    max_output_chars: int,
    control_channel: bool = False,
) -> _ExecutionResult:
    """Run a child while concurrently draining and bounding all output."""
    if control_channel and os.name != "posix":
        raise SourceCapabilityError("the protected pytest channel requires POSIX")

    started = time.monotonic()
    _require_resource_limit_capability()
    identity = _reserve_execution_identity()
    if identity is None:
        raise SourceCapabilityError(
            "code execution requires a root-run Linux verifier with "
            "per-execution identity containment"
        )
    parent_control: socket.socket | None = None
    child_control: socket.socket | None = None
    status_read: int | None = None
    status_write: int | None = None
    nonce = b""
    process: subprocess.Popen[Any] | None = None
    try:
        environment = _safe_environment(cwd)
        limit_helper = _write_helper(cwd, "limit_exec.py", _LIMIT_RUNNER_SOURCE)
        if control_channel:
            trusted_temp = cwd / ".verifier" / "tmp"
            trusted_temp.mkdir(mode=0o700)
            environment["TMPDIR"] = str(trusted_temp)
        initial_tree_size, initial_file_count = _tree_usage(
            cwd,
            stop_after_bytes=_MAX_FILESYSTEM_GROWTH_BYTES,
            stop_after_files=_MAX_CREATED_FILES,
        )
        initial_free_bytes = shutil.disk_usage(cwd).free
        _prepare_isolated_sandbox(cwd, identity)
        if control_channel:
            _protect_pytest_controller_files(cwd)
        target_argv = list(argv)
        popen_options: dict[str, Any] = {}
        inherited_descriptors: list[int] = []
        if identity is not None and not control_channel:
            popen_options.update(
                user=identity.uid,
                group=identity.gid,
                extra_groups=(),
                umask=0o077,
            )
        if control_channel:
            if identity is None:
                raise SourceCapabilityError(
                    "trusted pytest requires a separate candidate identity"
                )
            parent_control, child_control = socket.socketpair()
            nonce = secrets.token_bytes(_CONTROL_NONCE_BYTES)
            target_argv.insert(3, str(child_control.fileno()))
            target_argv.insert(4, str(identity.uid))
            target_argv.insert(5, str(identity.gid))
            inherited_descriptors.append(child_control.fileno())
        status_read, status_write = os.pipe()
        inherited_descriptors.append(status_write)
        popen_options["pass_fds"] = tuple(inherited_descriptors)
        process_argv = [
            sys.executable,
            "-I",
            str(limit_helper),
            str(status_write),
            str(max(1, int(timeout_seconds) + 1)),
            *target_argv,
        ]

        try:
            launched_process = subprocess.Popen(
                process_argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=False,
                **popen_options,
            )
        except OSError as exc:
            raise SourceCapabilityError(
                "could not launch the isolated Python execution"
            ) from exc
        process = launched_process
        os.close(status_write)
        status_write = None
        if child_control is not None:
            child_control.close()
            child_control = None
        nonce_delivery_failed = False
        if parent_control is not None:
            try:
                parent_control.sendall(nonce)
                parent_control.shutdown(socket.SHUT_WR)
            except OSError:
                nonce_delivery_failed = _kill_process_group(launched_process)

        if (
            launched_process.stdout is None
            or launched_process.stderr is None
            or launched_process.stdin is None
        ):
            raise SourceCapabilityError("could not create isolated execution pipes")
        stdin_pipe = launched_process.stdin
        control_read_socket = parent_control

        stdout = bytearray()
        stderr = bytearray()
        control = bytearray()
        launcher_status = bytearray()
        captured = 0
        output_limit_exceeded = False
        control_limit_exceeded = False
        process_group_terminated = nonce_delivery_failed
        lock = threading.Lock()

        def terminate_group() -> None:
            nonlocal process_group_terminated
            terminated = _kill_process_group(launched_process)
            if terminated:
                with lock:
                    process_group_terminated = True

        def consume(stream: Any, destination: bytearray) -> None:
            nonlocal captured, output_limit_exceeded
            while True:
                try:
                    chunk = stream.read(_READ_CHUNK)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                should_kill = False
                with lock:
                    remaining = max(0, max_output_chars - captured)
                    destination.extend(chunk[:remaining])
                    captured += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        output_limit_exceeded = True
                        should_kill = True
                if should_kill:
                    terminate_group()
                    return

        def consume_control() -> None:
            nonlocal control_limit_exceeded
            if control_read_socket is None:
                return
            while True:
                try:
                    chunk = control_read_socket.recv(_READ_CHUNK)
                except OSError:
                    return
                if not chunk:
                    return
                remaining = max(0, _MAX_CONTROL_BYTES - len(control))
                control.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    control_limit_exceeded = True
                    terminate_group()
                    return

        def consume_launcher_status() -> None:
            if status_read is None:
                return
            while len(launcher_status) <= 1024:
                try:
                    chunk = os.read(status_read, 1025 - len(launcher_status))
                except OSError:
                    return
                if not chunk:
                    return
                launcher_status.extend(chunk)

        def provide_input() -> None:
            try:
                stdin_pipe.write(stdin.encode("utf-8"))
                stdin_pipe.close()
            except (BrokenPipeError, OSError, ValueError):
                return

        readers = [
            threading.Thread(
                target=consume, args=(launched_process.stdout, stdout), daemon=True
            ),
            threading.Thread(
                target=consume, args=(launched_process.stderr, stderr), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        status_reader = threading.Thread(
            target=consume_launcher_status, daemon=True
        )
        status_reader.start()
        control_reader = (
            threading.Thread(target=consume_control, daemon=True)
            if parent_control is not None
            else None
        )
        if control_reader is not None:
            control_reader.start()
        writer = threading.Thread(target=provide_input, daemon=True)
        writer.start()

        timed_out = False
        resource_limit_exceeded = False
        deadline = started + timeout_seconds
        while launched_process.poll() is None:
            process_count, resident_bytes = _execution_usage(identity.uid)
            tree_size, file_count = _tree_usage(
                cwd,
                stop_after_bytes=(
                    initial_tree_size + _MAX_FILESYSTEM_GROWTH_BYTES
                ),
                stop_after_files=initial_file_count + _MAX_CREATED_FILES,
            )
            tree_growth = tree_size - initial_tree_size
            created_files = file_count - initial_file_count
            free_byte_drop = max(
                0, initial_free_bytes - shutil.disk_usage(cwd).free
            )
            if (
                process_count >= _MAX_PROCESSES
                or resident_bytes > _MAX_AGGREGATE_RSS_BYTES
                or tree_growth > _MAX_FILESYSTEM_GROWTH_BYTES
                or created_files > _MAX_CREATED_FILES
                or free_byte_drop > _MAX_FILESYSTEM_GROWTH_BYTES
            ):
                resource_limit_exceeded = True
                terminate_group()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                terminate_group()
                break
            time.sleep(_RESOURCE_POLL_SECONDS)
        try:
            launched_process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            terminate_group()
            raise SourceCapabilityError(
                "isolated Python process did not terminate"
            ) from exc
        if launched_process.returncode in {
            -getattr(signal, "SIGXCPU", signal.SIGKILL),
            -getattr(signal, "SIGXFSZ", signal.SIGKILL),
        }:
            resource_limit_exceeded = True
        final_tree_size, final_file_count = _tree_usage(
            cwd,
            stop_after_bytes=initial_tree_size + _MAX_FILESYSTEM_GROWTH_BYTES,
            stop_after_files=initial_file_count + _MAX_CREATED_FILES,
        )
        final_free_drop = max(0, initial_free_bytes - shutil.disk_usage(cwd).free)
        if (
            final_tree_size - initial_tree_size > _MAX_FILESYSTEM_GROWTH_BYTES
            or final_file_count - initial_file_count > _MAX_CREATED_FILES
            or final_free_drop > _MAX_FILESYSTEM_GROWTH_BYTES
        ):
            resource_limit_exceeded = True
        # A successful direct child may still have background descendants. Kill
        # its group, then every process under the per-execution Linux identity;
        # the latter also catches descendants that created a new session.
        terminate_group()
        if _terminate_execution_identity(identity):
            process_group_terminated = True
        if _pids_for_uid(identity.uid, include_zombies=False):
            raise SourceCapabilityError(
                "isolated Python descendants could not be terminated"
            )

        writer.join(timeout=1)
        for reader in readers:
            reader.join(timeout=1)
        if control_reader is not None:
            control_reader.join(timeout=1)
        status_reader.join(timeout=1)
        if status_read is not None:
            os.close(status_read)
            status_read = None
        if status_reader.is_alive():
            raise SourceCapabilityError(
                "code-execution sandbox attestation pipe did not close"
            )
        # Do not close a buffered pipe while another thread may hold its lock.
        if not readers[0].is_alive():
            launched_process.stdout.close()
        if not readers[1].is_alive():
            launched_process.stderr.close()
        if parent_control is not None:
            parent_control.close()
            parent_control = None

        control_payload = None
        if (
            control_channel
            and not control_limit_exceeded
            and len(control) >= _CONTROL_NONCE_BYTES
            and secrets.compare_digest(control[:_CONTROL_NONCE_BYTES], nonce)
        ):
            control_payload = bytes(control[_CONTROL_NONCE_BYTES:])
        stderr_text = stderr.decode("utf-8", errors="replace")
        if bytes(launcher_status) != b"OK":
            raise SourceCapabilityError("code-execution seccomp isolation failed")
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        return _ExecutionResult(
            exit_code=launched_process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr_text,
            timed_out=timed_out,
            output_limit_exceeded=(output_limit_exceeded or control_limit_exceeded),
            process_group_terminated=process_group_terminated,
            resource_limit_exceeded=resource_limit_exceeded,
            duration_ms=duration_ms,
            control_payload=control_payload,
        )
    finally:
        if process is not None and process.poll() is None:
            _kill_process_group(process)
        _terminate_execution_identity(identity)
        _release_execution_identity(identity)
        if parent_control is not None:
            parent_control.close()
        if child_control is not None:
            child_control.close()
        if status_read is not None:
            os.close(status_read)
        if status_write is not None:
            os.close(status_write)


def _stage_candidate_files(
    sandbox: Path, paths: list[str], context: SourceContext
) -> list[Path]:
    staged: list[Path] = []
    for name in paths:
        source = context.resolve_path(name)
        if source.stat().st_size > _MAX_CODE_BYTES:
            raise SourceDataError(
                f"Python deliverable exceeds {_MAX_CODE_BYTES} bytes: {name}"
            )
        destination = sandbox / name
        if destination.exists():
            raise SourceAuthoringError(f"multiple staged files resolve to {name!r}")
        shutil.copyfile(source, destination)
        staged.append(destination)
    return staged


def _stage_trusted_files(
    sandbox: Path, paths: list[str], context: SourceContext
) -> list[Path]:
    resolved: list[tuple[str, Path]] = []
    total_bytes = 0
    for name in paths:
        source = context.resolve_trusted_seeded_path(name)
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise SourceCapabilityError(
                f"trusted input could not be inspected: {name}"
            ) from exc
        if size > _MAX_TRUSTED_FILE_BYTES:
            raise SourceCapabilityError(
                f"trusted input exceeds {_MAX_TRUSTED_FILE_BYTES} bytes: {name}"
            )
        total_bytes += size
        if total_bytes > _MAX_TRUSTED_TOTAL_BYTES:
            raise SourceCapabilityError(
                "trusted inputs exceed the aggregate staging limit"
            )
        resolved.append((name, source))

    staged: list[Path] = []
    for name, source in resolved:
        relative = PurePosixPath(name)
        destination = sandbox.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise SourceAuthoringError(
                f"trusted input {name!r} collides with another staged file"
            )
        shutil.copyfile(source, destination)
        destination.chmod(0o444)
        staged.append(destination)
    return staged


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(paths: list[Path]) -> dict[Path, tuple[int, str]]:
    """Record trusted-file identity so candidate mutation forces zero credit."""
    return {path: (path.stat().st_size, _file_digest(path)) for path in paths}


def _files_unchanged(snapshot: dict[Path, tuple[int, str]]) -> bool:
    for path, (expected_size, expected_digest) in snapshot.items():
        try:
            if path.is_symlink() or not path.is_file():
                return False
            if (
                path.stat().st_size != expected_size
                or _file_digest(path) != expected_digest
            ):
                return False
        except OSError:
            return False
    return True


def _parse_pytest_control(
    payload: bytes | None, process_exit_code: int
) -> dict[str, int]:
    """Validate the bounded nonce-authenticated pytest controller message."""
    if not payload:
        return {}
    names = (
        "exit_code",
        "collected",
        "passed",
        "failed",
        "errors",
        "skipped",
        "xfailed",
        "xpassed",
    )
    if len(payload) != struct.calcsize(_PYTEST_CONTROL_FORMAT):
        return {}
    try:
        values = struct.unpack(_PYTEST_CONTROL_FORMAT, payload)
    except struct.error:
        return {}
    raw: dict[str, int] = dict(zip(names, values, strict=True))
    if raw["exit_code"] != process_exit_code or raw["exit_code"] not in range(6):
        return {}
    if any(raw[name] > 1_000_000 for name in names[1:]):
        return {}
    return raw


def _bounded_stderr(stdout: str, stderr: str, note: str, max_content_chars: int) -> str:
    available = max(0, max_content_chars - len(stdout))
    if not note:
        return stderr[:available]
    separator = "\n" if stderr and not stderr.endswith("\n") else ""
    return f"{stderr}{separator}{note}\n"[:available]


def _write_helper(sandbox: Path, name: str, source: str) -> Path:
    helper_dir = sandbox / ".verifier"
    helper_dir.mkdir(exist_ok=True)
    helper = helper_dir / name
    helper.write_text(source, encoding="utf-8")
    helper.chmod(0o444)
    return helper


def _write_candidate_proxy(sandbox: Path, candidate: Path) -> Path | None:
    """Expose simple functions/constants without importing candidate code."""
    if not candidate.stem.isidentifier():
        return None
    tree = ast.parse(candidate.read_text(encoding="utf-8"), filename=candidate.name)
    functions: set[str] = set()
    constants: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            constants.update(
                target.id
                for target in targets
                if isinstance(target, ast.Name) and target.id.isupper()
            )
    lines = ["from obi_candidate_runtime import candidate as _obi_candidate", ""]
    lines.extend(
        f"{name} = _obi_candidate.value({name!r})" for name in sorted(constants)
    )
    for name in sorted(functions):
        lines.extend(
            [
                "",
                f"def {name}(*args, **kwargs):",
                f"    return _obi_candidate.call({name!r}, *args, **kwargs)",
            ]
        )
    proxy = sandbox / candidate.name
    proxy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy.chmod(0o444)
    return proxy


def _python_failure(stderr: str) -> RunPythonOutput:
    return RunPythonOutput(
        passed=False,
        score=0.0,
        syntax_valid=False,
        executed=False,
        exit_code=None,
        stdout="",
        stderr=stderr,
        timed_out=False,
        output_limit_exceeded=False,
        process_group_terminated=False,
        resource_limit_exceeded=False,
        trusted_inputs_unchanged=True,
        duration_ms=0,
    )


def _pytest_failure(stderr: str) -> RunPytestOutput:
    return RunPytestOutput(
        passed=False,
        score=0.0,
        syntax_valid=False,
        executed=False,
        exit_code=None,
        collected=0,
        passed_tests=0,
        failed=0,
        errors=0,
        skipped=0,
        xfailed=0,
        xpassed=0,
        stdout="",
        stderr=stderr,
        timed_out=False,
        output_limit_exceeded=False,
        process_group_terminated=False,
        resource_limit_exceeded=False,
        trusted_inputs_unchanged=True,
        duration_ms=0,
    )


class RunPython(SourceCommand[RunPythonInput, RunPythonOutput]):
    """Execute one declared Python deliverable in a bounded subprocess."""

    name = "run_python"
    input_model = RunPythonInput
    output_model = RunPythonOutput

    def run(
        self, source_input: RunPythonInput, context: SourceContext
    ) -> RunPythonOutput:
        with tempfile.TemporaryDirectory(prefix="obi_code_run_") as raw_sandbox:
            sandbox = Path(raw_sandbox)
            candidates = _stage_candidate_files(
                sandbox,
                [source_input.path, *source_input.support_paths],
                context,
            )
            trusted_files = _stage_trusted_files(
                sandbox, source_input.fixture_paths, context
            )
            trusted_snapshot = _snapshot_files(trusted_files)
            syntax_error = _syntax_error(candidates)
            if syntax_error is not None:
                return _python_failure(syntax_error)

            helper = _write_helper(sandbox, "run_candidate.py", _RUNNER_SOURCE)
            execution = _run_bounded(
                [
                    sys.executable,
                    "-I",
                    str(helper),
                    str(sandbox / source_input.path),
                    *source_input.args,
                ],
                cwd=sandbox,
                stdin=source_input.stdin,
                timeout_seconds=source_input.timeout_seconds,
                max_output_chars=context.max_content_chars,
            )
            trusted_inputs_unchanged = _files_unchanged(trusted_snapshot)
            passed = (
                execution.exit_code == 0
                and not execution.timed_out
                and not execution.output_limit_exceeded
                and not execution.process_group_terminated
                and not execution.resource_limit_exceeded
                and trusted_inputs_unchanged
            )
            notes = []
            if execution.process_group_terminated:
                notes.append("candidate process tree required termination")
            if execution.resource_limit_exceeded:
                notes.append("candidate exceeded an execution resource limit")
            if not trusted_inputs_unchanged:
                notes.append("trusted input changed during candidate execution")
            note = "; ".join(notes)
            return RunPythonOutput(
                passed=passed,
                score=1.0 if passed else 0.0,
                syntax_valid=True,
                executed=True,
                exit_code=execution.exit_code,
                stdout=execution.stdout,
                stderr=_bounded_stderr(
                    execution.stdout,
                    execution.stderr,
                    note,
                    context.max_content_chars,
                ),
                timed_out=execution.timed_out,
                output_limit_exceeded=execution.output_limit_exceeded,
                process_group_terminated=execution.process_group_terminated,
                resource_limit_exceeded=execution.resource_limit_exceeded,
                trusted_inputs_unchanged=trusted_inputs_unchanged,
                duration_ms=execution.duration_ms,
            )


class RunPytest(SourceCommand[RunPytestInput, RunPytestOutput]):
    """Run trusted seeded pytest files against declared Python deliverables."""

    name = "run_pytest"
    input_model = RunPytestInput
    output_model = RunPytestOutput

    def run(
        self, source_input: RunPytestInput, context: SourceContext
    ) -> RunPytestOutput:
        try:
            import pytest  # noqa: F401  # capability check; child imports it again
        except ImportError as exc:  # pragma: no cover - requirements install pytest
            raise SourceCapabilityError(
                "code.run_pytest requires pytest in the verifier environment"
            ) from exc

        with tempfile.TemporaryDirectory(prefix="obi_code_pytest_") as raw_sandbox:
            sandbox = Path(raw_sandbox)
            candidate_root = sandbox / "candidate"
            candidate_root.mkdir()
            candidates = _stage_candidate_files(
                candidate_root,
                [source_input.path, *source_input.support_paths],
                context,
            )
            primary_trusted = _stage_trusted_files(
                sandbox,
                [*source_input.test_paths, *source_input.fixture_paths],
                context,
            )
            test_count = len(source_input.test_paths)
            staged_tests = primary_trusted[:test_count]
            staged_fixtures = primary_trusted[test_count:]
            candidate_fixtures = _stage_trusted_files(
                candidate_root,
                source_input.fixture_paths,
                context,
            )
            trusted = [*staged_tests, *staged_fixtures, *candidate_fixtures]
            trusted_snapshot = _snapshot_files(trusted)
            trusted_python = [
                path for path in [*staged_tests, *staged_fixtures]
                if path.suffix.casefold() == ".py"
            ]
            try:
                trusted_syntax_error = _syntax_error(trusted_python)
            except SourceDataError as exc:
                raise SourceCapabilityError(
                    f"trusted pytest apparatus is invalid: {exc}"
                ) from exc
            if trusted_syntax_error is not None:
                raise SourceCapabilityError(
                    "trusted pytest apparatus has invalid Python syntax: "
                    f"{trusted_syntax_error}"
                )
            syntax_error = _syntax_error(candidates)
            if syntax_error is not None:
                return _pytest_failure(syntax_error)

            _write_candidate_proxy(sandbox, candidates[0])
            helper = _write_helper(sandbox, "run_pytest.py", _PYTEST_RUNNER_SOURCE)
            execution = _run_bounded(
                [
                    sys.executable,
                    "-I",
                    str(helper),
                    str(candidates[0].relative_to(sandbox)),
                    *(str(path.relative_to(sandbox)) for path in staged_tests),
                ],
                cwd=sandbox,
                stdin="",
                timeout_seconds=source_input.timeout_seconds,
                max_output_chars=context.max_content_chars,
                control_channel=True,
            )

            counts = _parse_pytest_control(
                execution.control_payload, execution.exit_code
            )
            collected = counts.get("collected", 0)
            passed_tests = counts.get("passed", 0)
            failed = counts.get("failed", 0)
            errors = counts.get("errors", 0)
            skipped = counts.get("skipped", 0)
            xfailed = counts.get("xfailed", 0)
            xpassed = counts.get("xpassed", 0)
            observed = passed_tests + failed + errors + skipped + xfailed + xpassed
            denominator = max(collected, observed, 1)
            trusted_inputs_unchanged = _files_unchanged(trusted_snapshot)
            execution_integrity = (
                bool(counts)
                and trusted_inputs_unchanged
                and not execution.timed_out
                and not execution.output_limit_exceeded
                and not execution.process_group_terminated
                and not execution.resource_limit_exceeded
            )
            score = (
                passed_tests / denominator
                if execution_integrity and execution.exit_code in {0, 1}
                else 0.0
            )
            # Exit 1 normally means test failures. It may not accompany a
            # perfect ratio; doing so would turn a session-level failure into
            # false full credit.
            if execution.exit_code != 0 and score == 1.0:
                score = 0.0
            passed = (
                execution.exit_code == 0
                and collected > 0
                and passed_tests == collected
                and observed == collected
                and execution_integrity
            )
            notes = []
            if execution.process_group_terminated:
                notes.append("candidate process tree required termination")
            if execution.resource_limit_exceeded:
                notes.append("candidate exceeded an execution resource limit")
            if not trusted_inputs_unchanged:
                notes.append("trusted input changed during candidate execution")
            if not counts:
                notes.append("pytest did not return a valid protected result")
            note = "; ".join(notes)
            return RunPytestOutput(
                passed=passed,
                score=score,
                syntax_valid=True,
                executed=True,
                exit_code=execution.exit_code,
                collected=collected,
                passed_tests=passed_tests,
                failed=failed,
                errors=errors,
                skipped=skipped,
                xfailed=xfailed,
                xpassed=xpassed,
                stdout=execution.stdout,
                stderr=_bounded_stderr(
                    execution.stdout,
                    execution.stderr,
                    note,
                    context.max_content_chars,
                ),
                timed_out=execution.timed_out,
                output_limit_exceeded=execution.output_limit_exceeded,
                process_group_terminated=execution.process_group_terminated,
                resource_limit_exceeded=execution.resource_limit_exceeded,
                trusted_inputs_unchanged=trusted_inputs_unchanged,
                duration_ms=execution.duration_ms,
            )


COMMANDS = (RunPython(), RunPytest())
