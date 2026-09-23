import logging
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, RootModel

logger = logging.getLogger(__name__)

SourceOutput = BaseModel | RootModel[Any]

OUTPUT_DIR_NAME = "output"
# Mirrors ``task_inputs.INPUT_DIR_NAME``. Duplicated rather than imported
# because this package is vendored standalone into generated task bundles that
# ship no harness modules beside it; a test pins the two spellings together.
INPUT_DIR_NAME = "input"

# The ordered rungs :meth:`SourceContext.resolve_path` tries. ``declared`` is
# the literal authored path and must stay first: it makes every lookup that
# succeeds today resolve to exactly what it resolves to today, so the fallbacks
# are only ever reached by a lookup that would otherwise report the file
# missing.
#
# The default is declared + ``output`` only. The ``output`` rung re-roots the
# authored path under ``output/`` -- where the golden test materializes every
# golden file, structure preserved -- so a spec authored against a bare
# deliverable name grades the same whether the file lands at the workspace root
# (a real run) or under ``output/`` (the golden test). No rung walks the tree,
# and the seeded ``input/`` fixtures stay unreachable by any fallback.
#
# ``workspace_root`` -- matching a bare basename anywhere at the workspace root
# -- is deliberately NOT a default rung. At the root a same-named but unrelated
# file (a checked-out repo's ``app.py``, a copied fixture) could shadow a
# genuinely missing deliverable and be graded as a false pass, silently
# weakening every existing ``file_check``. It stays defined so a caller that
# needs it can opt in explicitly, but no lookup reaches it by default.
DEFAULT_LOOKUP_ORDER = ("declared", "output")


def _under_input_dir(relative: str) -> bool:
    """Whether a workspace-relative candidate sits in the seeded input tree.

    A verifier must grade what the model produced, never the fixture it was
    given, so no fallback candidate may resolve under ``input/``. The current
    rungs cannot reach it anyway; this keeps the property true rather than
    incidental if a rung is ever added. Case-folded because the harness's own
    dev platform has a case-insensitive filesystem, where ``Input/x.csv`` and
    ``input/x.csv`` are the same seeded file.
    """
    parts = PurePosixPath(relative).parts
    return bool(parts) and parts[0].casefold() == INPUT_DIR_NAME


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel | RootModel[Any])


class SourceAuthoringError(ValueError):
    """Raised when verifier-authored source configuration is invalid."""


class SourceDataError(Exception):
    """Raised when an existing source artifact lacks required data."""


class SourceCapabilityError(RuntimeError):
    """Raised when the grader lacks a required, trusted source capability."""


@dataclass(frozen=True)
class MediaAttachment:
    """One in-memory attachment delivered only to a multimodal rubric judge."""

    label: str
    mime_type: str
    data_base64: str


@dataclass(frozen=True)
class SourceContext:
    """Runtime context shared by source commands.

    Attributes:
        workspace_dir: Root directory where task output artifacts live.
        agent_logs_dir: Root directory where the host exposes agent log
            artifacts. Optional; required only for ``response.*`` sources.
        max_content_chars: Maximum text content a source should return.
        seeded_input_paths: Workspace-relative task-input paths that the
            harness seeded for this run. Commands may read these only through
            an explicit seeded-input resolver; ordinary file sources continue
            to grade produced artifacts only.
        lookup_order: Ordered rungs :meth:`resolve_path` tries when locating a
            declared path. Defaults to :data:`DEFAULT_LOOKUP_ORDER`; setting it
            to ``("declared",)`` restores strict exact-path resolution.
        trusted_input_dir: Optional clean snapshot of the declared task inputs,
            materialized by the harness after the agent finishes. Executable
            code checks use this snapshot instead of the agent-visible copy.
        trusted_reference_dir: Optional verifier-private root containing gold
            artifacts. It must be materialized outside the agent workspace and
            is used only through :meth:`resolve_trusted_reference_path`.
    """

    workspace_dir: Path
    agent_logs_dir: Path | None
    max_content_chars: int
    seeded_input_paths: tuple[str, ...] = ()
    lookup_order: tuple[str, ...] = DEFAULT_LOOKUP_ORDER
    trusted_input_dir: Path | None = None
    trusted_reference_dir: Path | None = None

    def resolve_path(self, path: str) -> Path:
        """Resolve a declared deliverable path by name rather than by folder.

        A verifier names the file the evaluated model had to produce, but the
        folder that file lands in is not something the task grades, and the two
        environments that run these specs disagree about it: a real run hands
        the agent an empty workspace as its cwd, while the verifier-generation
        golden test materializes every golden file under ``output/``. Resolving
        the authored string literally made the same spec pass in one place and
        fail in the other.

        So this walks :attr:`lookup_order` and returns the first candidate that
        exists: the declared path itself, then the same path re-rooted under
        ``output/`` (the default order). The declared path is always tried
        first, so a lookup that succeeds today is unaffected; a lookup that
        would have reported the file missing gets one more specific place to
        look inside ``output/``, and nothing else. The bare-basename
        ``workspace_root`` rung is reached only when a caller opts into it.

        Args:
            path: Workspace-relative path from a source command input.

        Returns:
            Absolute resolved path inside the workspace. When no rung matches,
            the literal declared path is returned even though it does not
            exist, so ``filesystem.check_path_exists`` still reports
            ``exists: false`` and the document readers still raise
            ``FileNotFoundError`` naming the path the author wrote.

            A path whose final component contains ``*`` is a deliberately
            narrow selector: it must sit directly under ``output/`` or at the
            workspace root and resolve to exactly one regular file. This lets a
            verifier grade a named deliverable even when the task did not
            prescribe its incidental filename.

        Raises:
            SourceAuthoringError: If the path is absolute, escapes the
                workspace, or uses an unsafe selector.
            SourceDataError: If a valid selector matches zero files at every
                rung, or multiple files at one rung.
        """
        if any(ord(character) < 32 or ord(character) == 127 for character in path):
            raise SourceAuthoringError("source path must use portable characters")
        normalised = path.replace("\\", "/")
        candidate = Path(normalised)
        if candidate.is_absolute() or (
            len(normalised) >= 2 and normalised[0].isalpha() and normalised[1] == ":"
        ):
            raise SourceAuthoringError("source path must be relative to workspace")

        workspace = self.workspace_dir.resolve()
        if any(token in normalised for token in ("*", "?", "[", "]")):
            return self._resolve_selector(path, normalised, workspace)
        return self._resolve_file(normalised, workspace)

    def _resolve_file(self, normalised: str, workspace: Path) -> Path:
        """Return the first rung whose candidate exists, else the literal path."""
        # Resolved up front so an escaping declared path is rejected before any
        # fallback is considered, exactly as it was before the ladder existed.
        literal = self._inside_workspace(workspace, normalised)
        for rung, relative in self._candidates(normalised):
            if rung == "declared":
                # Existence, not is_file: `filesystem.check_path_exists`
                # legitimately asks about a directory, and a declared directory
                # must keep resolving to itself.
                if literal.exists():
                    return literal
                continue
            if _under_input_dir(relative):
                continue
            resolved = self._inside_workspace(workspace, relative)
            # A fallback that landed on a directory must not shadow a real file
            # a later rung would have found.
            if resolved.is_file():
                logger.info(
                    "verifier path `%s` not found as declared; graded `%s` found by the %s rung",
                    normalised,
                    relative,
                    rung,
                )
                return resolved
        return literal

    def _resolve_selector(self, path: str, normalised: str, workspace: Path) -> Path:
        """Return the single file a selector names, trying each rung in turn."""
        pure = PurePosixPath(normalised)
        parts = pure.parts
        has_recursive = "**" in normalised
        # A selector is intentionally much narrower than pathlib.glob: no
        # character-class or single-char globs, and a wildcard is required in
        # the filename component so the pattern names a deliverable by type,
        # not by exact path.
        if any(token in normalised for token in ("?", "[", "]")) or "*" not in pure.name:
            raise SourceAuthoringError(
                "selectors must use one pattern in the filename, "
                "such as *.xlsx, output/*.xlsx, or output/**/*.xlsx"
            )
        if has_recursive:
            # Recursive globs are allowed only under output/.  A recursive
            # glob at the workspace root (``**/*.xlsx``) would walk input/ and
            # every checked-out repo file; only output/ is a safe tree to
            # search recursively.
            if parts[0] != OUTPUT_DIR_NAME or "**" not in parts:
                raise SourceAuthoringError(
                    "recursive selectors must stay under output/, "
                    "such as output/**/*.xlsx"
                )
        else:
            # Non-recursive: one part at the root, or two parts under output/.
            if not (
                len(parts) == 1 or (len(parts) == 2 and parts[0] == OUTPUT_DIR_NAME)
            ):
                raise SourceAuthoringError(
                    "selectors must use one non-recursive pattern at the workspace root "
                    "or directly under output/, such as *.xlsx or output/*.xlsx"
                )

        for rung, relative in self._candidates(normalised):
            if rung != "declared" and _under_input_dir(relative):
                continue
            matches: list[Path] = []
            for item in workspace.glob(relative):
                try:
                    item_relative = item.relative_to(workspace).as_posix()
                except ValueError as exc:
                    raise SourceAuthoringError(
                        "source path must stay inside workspace"
                    ) from exc
                try:
                    resolved_item = self._inside_workspace(workspace, item_relative)
                except SourceDataError as exc:
                    raise SourceAuthoringError(
                        "selector matched an unsafe path that is not inside workspace"
                    ) from exc
                try:
                    metadata = resolved_item.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(metadata.st_mode):
                    matches.append(resolved_item)
            # Two matches in one rung is a real ambiguity about which
            # deliverable to grade. Falling through to the next rung would
            # resolve it by grading an unrelated file instead.
            if len(matches) > 1:
                raise SourceDataError(
                    f"selector `{path}` matched {len(matches)} files; expected exactly one"
                )
            if matches:
                if rung != "declared":
                    logger.info(
                        "verifier selector `%s` matched nothing as declared; graded `%s` "
                        "found by the %s rung",
                        normalised,
                        matches[0].name,
                        rung,
                    )
                return matches[0]
        raise SourceDataError(
            f"selector `{path}` matched 0 files; expected exactly one"
        )

    def _candidates(self, normalised: str) -> list[tuple[str, str]]:
        """Ordered, de-duplicated ``(rung, workspace-relative path)`` candidates.

        The ``output`` rung offers two spellings: the declared path re-rooted
        under ``output/`` -- which is where a nested golden bundle materializes
        -- and then the bare file name under ``output/``.
        """
        pure = PurePosixPath(normalised)
        basename = pure.name
        already_under_output = pure.parts[:1] == (OUTPUT_DIR_NAME,)
        by_rung: dict[str, tuple[str, ...]] = {
            "declared": (normalised,),
            "output": ()
            if already_under_output
            else (f"{OUTPUT_DIR_NAME}/{normalised}", f"{OUTPUT_DIR_NAME}/{basename}"),
            "workspace_root": (basename,),
        }
        unknown = [rung for rung in self.lookup_order if rung not in by_rung]
        if unknown:
            raise SourceAuthoringError(
                f"unknown path lookup rung: {', '.join(unknown)}"
            )

        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        for rung in self.lookup_order:
            for relative in by_rung[rung]:
                if relative in seen:
                    continue
                seen.add(relative)
                ordered.append((rung, relative))
        return ordered

    def _inside_workspace(self, workspace: Path, relative: str) -> Path:
        """Resolve one workspace-relative candidate, rejecting any escape.

        Also rejects a path that resolves *under* the seeded ``input/`` tree.
        ``_under_input_dir`` inspects the declared path *string*; ``.resolve()``
        follows symlinks, so a model that writes ``output/link.csv ->
        input/secret.csv`` would otherwise get its own untrusted symlink graded
        as if it were produced work.  The resolved-path check closes that.
        """
        unresolved = workspace / Path(relative)
        current = workspace
        for component in Path(relative).parts:
            current /= component
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(metadata.st_mode):
                raise SourceDataError("candidate path must not contain symlinks")
            if current == unresolved:
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                    raise SourceDataError(
                        "candidate file must be one unlinked regular file"
                    )
                if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                    raise SourceDataError(
                        "candidate path must be a regular file or directory"
                    )

        resolved = unresolved.resolve()
        try:
            workspace_relative = resolved.relative_to(workspace)
        except ValueError as exc:
            raise SourceAuthoringError(
                "source path must stay inside workspace"
            ) from exc
        if _under_input_dir(workspace_relative.as_posix()):
            raise SourceAuthoringError(
                "source path must not resolve under the seeded input directory"
            )
        return resolved

    def resolve_trusted_seeded_path(self, path: str) -> Path:
        """Resolve a declared task input from the harness's clean snapshot.

        Executable candidate code must not be tested against the copy that was
        visible to the agent: permission bits are advisory when the agent and
        harness share an OS user. The benchmark runner therefore re-verifies
        and re-extracts ``input.zip`` after the agent finishes and passes that
        separate root here.
        """
        normalised = self._validate_seeded_path(path)
        if self.trusted_input_dir is None:
            raise SourceCapabilityError(
                "code execution requires a trusted input snapshot from the harness"
            )

        root = self.trusted_input_dir.resolve()
        resolved = (root / Path(normalised)).resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise SourceAuthoringError(
                "seeded input path must stay inside trusted snapshot"
            ) from exc
        if relative != normalised:
            raise SourceAuthoringError(
                "seeded input path must resolve to its declared trusted snapshot file"
            )
        if not resolved.is_file():
            raise SourceDataError(f"declared trusted input is missing: {path}")
        return resolved

    def _validate_seeded_path(self, path: str) -> str:
        """Validate one exact path against the harness-recorded input list."""
        normalised = path.replace("\\", "/")
        candidate = Path(normalised)
        if candidate.is_absolute() or any(
            token in normalised for token in ("*", "?", "[", "]")
        ):
            raise SourceAuthoringError(
                "seeded input path must be one declared, non-glob relative path"
            )

        allowed = {item.replace("\\", "/") for item in self.seeded_input_paths}
        if normalised not in allowed:
            raise SourceAuthoringError(
                "seeded input path is not declared for this task"
            )
        return normalised

    def resolve_trusted_reference_path(self, path: str) -> Path:
        """Resolve one exact gold artifact from verifier-private storage.

        Gold references must never share the model's workspace or its seeded
        ``input/`` tree.  This resolver intentionally has no lookup ladder,
        globbing, basename fallback, or symlink support: the authored path must
        name the same regular file beneath the private reference root.
        """
        normalised = path.replace("\\", "/")
        pure = PurePosixPath(normalised)
        candidate = Path(normalised)
        if (
            not normalised
            or normalised == "."
            or "\\" in path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or (
                len(normalised) >= 3
                and normalised[0].isalpha()
                and normalised[1:3] == ":/"
            )
            or candidate.is_absolute()
            or pure.is_absolute()
            or normalised != pure.as_posix()
            or ".." in pure.parts
            or any(token in normalised for token in ("*", "?", "[", "]"))
        ):
            raise SourceAuthoringError(
                "reference path must be one normalized, non-glob relative path"
            )
        if self.trusted_reference_dir is None:
            raise SourceCapabilityError(
                "gold comparison requires verifier-private reference artifacts"
            )

        try:
            root_metadata = self.trusted_reference_dir.lstat()
        except OSError as exc:
            raise SourceCapabilityError(
                "trusted reference storage is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
        ):
            raise SourceCapabilityError(
                "trusted reference storage must be a real directory"
            )
        root = self.trusted_reference_dir.resolve()
        forbidden_roots = [self.workspace_dir.resolve()]
        if self.trusted_input_dir is not None:
            forbidden_roots.append(self.trusted_input_dir.resolve())
        if any(
            root == forbidden
            or root.is_relative_to(forbidden)
            or forbidden.is_relative_to(root)
            for forbidden in forbidden_roots
        ):
            raise SourceCapabilityError(
                "trusted reference storage must be disjoint from agent-visible inputs"
            )
        unresolved = root / candidate
        if unresolved.is_symlink():
            raise SourceAuthoringError("reference path must not be a symlink")
        resolved = unresolved.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise SourceAuthoringError(
                "reference path must stay inside verifier-private storage"
            ) from exc
        if relative != normalised:
            raise SourceAuthoringError(
                "reference path must resolve to its declared verifier-private file"
            )
        if not resolved.is_file():
            raise SourceCapabilityError(f"trusted reference is missing: {path}")
        metadata = resolved.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SourceCapabilityError(
                "trusted reference must be one unlinked regular file"
            )
        return resolved

    def resolve_seeded_path(self, path: str) -> Path:
        """Resolve one declared, seeded task input without opening input access generally.

        Reconciliation sources need authoritative source data, but a normal
        ``file_check`` must never pass merely by reading the task fixture. This
        method is deliberately stricter than :meth:`resolve_path`: the caller
        must name exactly one path the harness recorded while extracting the
        task's ``input.zip`` and the resolved filesystem object must still be
        that exact workspace-relative path.
        """
        normalised = self._validate_seeded_path(path)
        allowed = {item.replace("\\", "/") for item in self.seeded_input_paths}
        workspace = self.workspace_dir.resolve()
        resolved = (workspace / Path(normalised)).resolve()
        try:
            relative = resolved.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise SourceAuthoringError("seeded input path must stay inside workspace") from exc
        if relative != normalised or relative not in allowed:
            raise SourceAuthoringError("seeded input path must resolve to its declared workspace file")
        if not resolved.is_file():
            raise SourceDataError(f"declared seeded input is missing: {path}")
        return resolved


class SourceCommand(ABC, Generic[InputT, OutputT]):
    """Base class for a command exposed by a source namespace.

    Type parameters ``InputT`` and ``OutputT`` bind a concrete command's
    Pydantic input and output models. Subclasses parameterize the generic to
    let static checkers verify that ``run`` consumes and produces the right
    model types.
    """

    name: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel] | type[RootModel[Any]]]

    @abstractmethod
    def run(self, source_input: InputT, context: SourceContext) -> OutputT:
        """Runs the source command.

        Args:
            source_input: Validated command-specific input model.
            context: Runtime context with workspace and extraction limits.

        Returns:
            Validated command-specific output model.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class RegisteredSource:
    """Resolved source command registered under its public dotted name."""

    name: str
    command: "SourceCommand[Any, Any]"

    @property
    def input_model(self) -> type[BaseModel]:
        """Returns the command input model class."""
        return self.command.input_model

    @property
    def output_model(self) -> type[BaseModel] | type[RootModel[Any]]:
        """Returns the command output model class."""
        return self.command.output_model
