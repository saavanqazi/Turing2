"""Deterministic analysis of a Figma design tree.

Figma is the only gym with no SQL surface -- its world is an in-memory JSON tree
-- so the SQL-backed verifier sources cannot reach it and, until this namespace
existed, a design could only be graded by an exact key/value match plus a few
dotted paths on the one object that matched. That cannot express a count, an
ordering, a text match, or an absence.

These commands read the same document the gym itself holds. The tree returned by
``GET /state?include_db=true`` (``database_state``), the ``figma_seed/*.json``
files, and the connector's canonical in-memory dict are all the identical shape::

    {"files": [...], "current_selection_node_ids": [...], "projects": [...],
     "current_file_key": ..., "current_figma_channel": ...}

so one file-based namespace grades a live episode, a seed, and a fixture without
knowing which it was handed.

A verifier names that design by its own filename -- ``path`` is
``"Account Lock _ Account Close.fig"``, the document the task is about, never
the ``gym_state.json`` the harness materializes for a live run. See
:func:`load_document` for how the one declared name resolves to a design in
both worlds.

Every command is bounded: counts are computed over the whole tree and are never
truncated, while the per-node detail that accompanies them is capped and reports
``truncated`` when the cap bit. A verifier that asserts "there are 12 TEXT nodes"
must not silently become "there are 12 of the first 50 nodes I looked at".
"""

import json
import re
from collections import Counter, OrderedDict
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from pydantic import Field, field_validator, model_validator

from ..models import StrictModel
from ..source_types import (
    SourceAuthoringError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)
from .fig_decode import FIG_JAM_MAGIC, FIG_KIWI_MAGIC, ZIP_MAGIC, parse_fig_bytes

# Per-command cap on how many node summaries accompany a count. Deep design
# files run to thousands of nodes; returning all of them would blow the content
# budget and bury the evidence a judge or a JSONPath actually reads.
DEFAULT_NODE_LIMIT = 50
MAX_NODE_LIMIT = 500

# --- what a figma source's `path` names --------------------------------------
#
# The design document, by its own filename: `"Account Lock _ Account Close.fig"`.
# That name is the one string an author, the task prompt and the trainer already
# agree on. `gym_state.json` is not: it is how a LIVE episode's tree reaches the
# grader, written by the harness into the workspace and deleted again after the
# run. Every figma verifier used to name it, so a trainer reading its own
# verifiers was told to produce a JSON file nothing ever writes.
#
# Only these two suffixes are design documents. Anything else -- an office
# deliverable, and `gym_state.json` above all -- is refused at argument
# validation rather than at grade time, because a path the reader cannot resolve
# surfaces as `FileNotFoundError`, which the engine classifies as bad agent
# output: the MODEL would score 0 with reason "required file missing" for an
# author's typo.
DESIGN_SUFFIXES = (".fig", ".jam")

# The container magics those suffixes carry. A ``.fig`` may also be a ZIP whose
# ``canvas.fig`` entry holds one; the decoder unwraps that itself.
DESIGN_MAGICS = (FIG_KIWI_MAGIC, FIG_JAM_MAGIC)

# The harness's internal materialization of a live gym's tree. Kept in sync with
# `benchmark_runner.GYM_STATE_FILENAME` by a test in `test_figma_file_check.py`;
# this package cannot import that module. It is read here as a FALLBACK and is
# never a legal authored path.
RUNTIME_STATE_FILENAME = "gym_state.json"

# A design task runs 30+ checks against one document, and a real `.fig` runs to
# tens of megabytes; decoding it per check is the difference between a run that
# finishes and one that times out. Entries are keyed by file identity (resolved
# path, size, mtime_ns) so a rewritten document is never served stale, and the
# map is bounded because a benchmark process grades many tasks in a row.
PARSE_CACHE_SIZE = 4
PARSE_CACHE: "OrderedDict[tuple[str, int, int], dict[str, Any]]" = OrderedDict()


def validate_design_path(path: str) -> str:
    """Checks that a declared path names a design document.

    Args:
        path: The authored ``path`` argument.

    Returns:
        The path unchanged.

    Raises:
        ValueError: If the path does not end in one of :data:`DESIGN_SUFFIXES`.
            Raised as a plain ``ValueError`` so Pydantic reports it as a field
            error; the registry turns that into a ``SourceAuthoringError``.
    """
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.casefold()
    if suffix not in DESIGN_SUFFIXES:
        raise ValueError(
            f"{path!r} is not a design document: figma sources read a .fig/.jam "
            "design document, named by its own filename (for example "
            f"'Account Lock _ Account Close.fig'). {RUNTIME_STATE_FILENAME!r} is "
            "the harness's internal materialization of a live gym's tree and is "
            "never a verifier path."
        )
    return path


class FigmaDocumentInput(StrictModel):
    """Base for every figma command input: ``path`` names a design document.

    ``path`` lives here rather than being repeated on each of the four command
    inputs, so the rule below is attached to a field that provably exists on
    every one of them. Inherited fields are ordered first, which is where
    ``path`` already sat, so the argument schemas the capability manifest
    publishes are unchanged.

    Attributes:
        path: Filename of the ``.fig``/``.jam`` design document to grade.
    """

    path: str

    @field_validator("path")
    @classmethod
    def _path_names_a_design_document(cls, value: str) -> str:
        """Rejects a ``path`` that is not a ``.fig``/``.jam`` document."""
        return validate_design_path(value)


# --- the vocabulary the summaries grade against ------------------------------
#
# Every judgement below ("this layer was never named", "this colour is not in
# the palette", "this artboard is a phone") is a rule, not an observation, so
# each rule is a named module constant with a test pinning it. A verifier that
# asserts `default_named_count == 0` is asserting against THIS list; changing it
# silently rescores every design already graded.

# Figma's own new-layer names. Case-sensitive and anchored: a designer who typed
# "Frame" meant it, and "framework" is not a default name.
DEFAULT_NAME_PATTERN = re.compile(
    r"^(Frame|Rectangle|Ellipse|Group|Vector|Line|Text|Component|Instance"
    r"|Polygon|Star|Section|Slice|Union|Subtract|Intersect|Exclude|Image"
    r"|Arrow|Boolean|Rounded Rectangle)( \d+)?$"
)

# Working-file residue: "Final 2", "Header copy", "Home v2", "FINAL FINAL".
# Word-bounded so "Finality" and "Newsletter" are left alone.
CLUTTER_NAME_PATTERN = re.compile(
    r"\b(final|new|latest|copy|old|backup|v\d+)\b", re.IGNORECASE
)

# Copy that was never written. The anchored half matches a whole label that is
# just its own control type ("Button", "Label"); the unanchored half matches
# filler anywhere in a string.
PLACEHOLDER_PATTERN = re.compile(
    r"(\blorem ipsum\b|\btbd\b|\btodo\b|\bxxx\b|\basdf\b)"
    r"|^(text|label|button|title|heading|placeholder|untitled)$",
    re.IGNORECASE,
)

# Component states a design system is normally asked to cover.
STATE_TERMS = (
    "empty",
    "loading",
    "error",
    "success",
    "disabled",
    "focus",
    "hover",
    "active",
    "pressed",
    "selected",
    "filled",
    "default",
)

# Names that mean "a person taps this", which is what makes a 30x30 box a
# 30x30 *target* rather than a decoration.
INTERACTIVE_NAME_TERMS = (
    "button",
    "btn",
    "cta",
    "icon",
    "tab",
    "toggle",
    "link",
    "chip",
    "checkbox",
    "radio",
    "switch",
)

ANNOTATION_NAME_TERMS = ("annotation", "note", "spec", "redline", "comment")
ARCHIVE_NAME_TERMS = ("archive", "archived", "old", "deprecated", "graveyard", "trash")

# Top-level frame width buckets. The gap between MOBILE_MAX_WIDTH and
# TABLET_MIN_WIDTH is deliberate: 501-699 is not a device, it is a fragment, and
# calling it "mobile" would let a design claim a breakpoint it does not have.
DESKTOP_MIN_WIDTH = 1200
TABLET_MIN_WIDTH = 700
MOBILE_MAX_WIDTH = 500

# The buckets that name a device, in report order. "other" is a fourth bucket
# but not a device: a 600-wide artboard covers no breakpoint.
DEVICE_BUCKETS = ("desktop", "tablet", "mobile")

# A name beats the ruler: an artboard called "iPhone 14 Landscape" is a phone
# at 844 wide, whatever the width bucket would otherwise say.
DEVICE_NAME_HINTS = (
    ("desktop", ("desktop",)),
    ("tablet", ("tablet",)),
    ("mobile", ("mobile", "iphone", "android")),
)

# Page roles, first match wins, so the archive test runs before the others: an
# "Old Components" page is an archive, not a component library.
PAGE_ROLE_TERMS = (
    ("archive", ARCHIVE_NAME_TERMS),
    ("cover", ("cover", "readme", "start here", "title page")),
    (
        "foundations",
        (
            "foundation",
            "foundations",
            "style",
            "styles",
            "token",
            "tokens",
            "typography",
            "color",
            "colors",
            "colour",
            "colours",
            "grid",
            "brand",
        ),
    ),
    (
        "components",
        ("component", "components", "library", "symbols", "ui kit", "atoms"),
    ),
    ("desktop", ("desktop", "web")),
    ("tablet", ("tablet", "ipad")),
    ("mobile", ("mobile", "iphone", "android", "phone")),
)

# Labels that are the same button written two ways. Keys are the normalised
# form (lowercase, alphanumerics only), so "Log in" and "LOG IN" both arrive
# here as "login".
LABEL_SYNONYMS = {
    "login": "login",
    "signin": "login",
    "signup": "signup",
    "register": "signup",
    "ok": "ok",
    "okay": "ok",
    "cancel": "cancel",
    "dismiss": "cancel",
}

# fontPostScriptName carries the weight as a word ("SemiBold", "Inter-Bold").
# Tool-written text carries a numeric style.fontWeight instead and no
# postscript name at all, so both spellings have to resolve to one number.
FONT_WEIGHT_BY_STYLE_NAME = {
    "thin": 100,
    "extralight": 200,
    "light": 300,
    "regular": 400,
    "italic": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
    "extrabold": 800,
    "black": 900,
}

# Only FRAME counts as a frame, in every command. An INSTANCE's layoutMode is
# its main component's, not a layout decision taken in this file -- counting it
# would report auto-layout adoption the designer never made (on the EC13 seed,
# 31 of 38 auto-layout nodes are instances). A card that wants instance layout
# asks find_nodes for it.
FRAME_TYPE = "FRAME"
COMPONENT_TYPES = ("COMPONENT", "COMPONENT_SET")
AUTO_LAYOUT_MODES = ("HORIZONTAL", "VERTICAL")
ICON_TYPES = ("VECTOR", "INSTANCE")

# A default spacing scale of 4s and a radius scale of 2s (plus the "fully
# rounded" sentinel Figma stores as a very large radius) covers most systems;
# both are replaceable per task through a command argument.
DEFAULT_SPACING_SCALE = tuple(float(value) for value in range(0, 201, 4))
DEFAULT_RADIUS_SCALE = tuple(float(value) for value in range(0, 65, 2)) + (9999.0,)

# Report thresholds.
DEEP_NESTING_DEPTH = 8
ICON_MAX_DIMENSION = 48
MIN_TAP_TARGET = 44
TINY_FONT_SIZE = 12
MIN_TEXT_CONTRAST = 4.5
OVERFLOW_TOLERANCE = 1.0
REPEATED_PATTERN_MIN_MEMBERS = 3


def _term_pattern(terms: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    """Compiles one word-bounded, case-insensitive alternation of terms.

    Args:
        terms: Literal terms to match as whole words.

    Returns:
        A compiled pattern, or one that can never match when ``terms`` is empty.
    """
    if not terms:
        return re.compile(r"(?!)")
    joined = "|".join(re.escape(term) for term in terms)
    return re.compile(rf"\b({joined})\b", re.IGNORECASE)


INTERACTIVE_NAME_PATTERN = _term_pattern(INTERACTIVE_NAME_TERMS)
ANNOTATION_NAME_PATTERN = _term_pattern(
    tuple(term for base in ANNOTATION_NAME_TERMS for term in (base, base + "s"))
)
ARCHIVE_NAME_PATTERN = _term_pattern(ARCHIVE_NAME_TERMS)
ICON_NAME_PATTERN = _term_pattern(("icon", "icons"))


class FigmaNodeSummary(StrictModel):
    """One node's addressable properties.

    Geometry is lifted out of ``absoluteBoundingBox`` into flat ``x``/``y``/
    ``width``/``height`` because that is what an author asserts on, and the
    Figma REST shape nests it one level deeper than every other property. These
    are the only geometry fields any command reports.

    Attributes:
        id: Node id. Seeded nodes use the colon form (``1516:368``); nodes
            created during an episode carry a 32-character hex id.
        name: Node name as stored in the document.
        type: Figma node type, such as ``FRAME``, ``TEXT`` or ``COMPONENT``.
        parent_id: Id of the node's parent, computed by traversal rather than
            read from the node's own ``parentId``, which seeds do not always set.
        page_id: Id of the CANVAS (page) the node sits on.
        page_name: Name of that page.
        visible: Whether the node is visible. Absent in the document means true.
        x: Absolute x coordinate, or None when the node carries no bounding box.
        y: Absolute y coordinate, or None.
        width: Absolute width, or None.
        height: Absolute height, or None.
        fills: The node's fills, unmodified. Usually a list of paints, but the
            connector's ``Fill`` model is a union: a shared fill style is stored
            as a plain string. Reported as-is either way, so a design that uses
            a style reference reads as that reference rather than as a list of
            its characters.
        characters: Text content for TEXT nodes, else None.
        child_count: Number of direct children.
    """

    id: str
    name: str
    type: str
    parent_id: str | None
    page_id: str | None
    page_name: str | None
    visible: bool
    x: float | None
    y: float | None
    width: float | None
    height: float | None
    fills: Any = None
    characters: str | None
    child_count: int


class FigmaNodeSelector(FigmaDocumentInput):
    """The fields that pick nodes out of a design, shared by both commands.

    One model rather than duck-typing across two: the selector fields are read
    by :func:`matches_nodes`, and reading them off whichever input happened to
    be passed meant a rename could silently disable a filter instead of failing.

    Every field is optional and they combine with AND. Supplying none of them
    selects every node in the file, which is how ``find_nodes`` answers "how
    many nodes are there".

    Attributes:
        path: Filename of the ``.fig``/``.jam`` design document to grade.
        file_key: Which file to search. Defaults to the document's
            ``current_file_key``, then to the only file when there is just one.
        name: Exact node name.
        name_contains: Case-sensitive substring of the node name.
        node_type: Exact Figma node type.
        node_id: Exact node id.
        page: Name or id of the page to restrict the search to.
        name_regex: Python regular expression matched against the node name
            with ``re.search``. An invalid pattern is an authoring error, not a
            selector that quietly matches nothing.
        parent_name: Exact name of the node's parent. A node sitting directly
            on a page has the page as its parent.
        fill_hex: ``#RRGGBB`` of the node's first visible SOLID fill, matched
            case-insensitively. A node whose fills are a style reference has no
            hex and never matches.
        font_family: Exact ``style.fontFamily``.
        component_name: Resolved component name -- the file's component map
            entry for the node's ``componentId``, falling back to an INSTANCE's
            own name.
        visible: Whether the node is visible. Figma omits the field on visible
            nodes, so ``visible: false`` selects only explicitly hidden ones.
        locked: Whether the node is locked.
        default_named: Whether the name is one Figma assigned, per
            :data:`DEFAULT_NAME_PATTERN`.
        min_depth: Minimum depth, where 0 is a direct child of a page. Pages
            themselves sit at -1, so any depth bound excludes them.
        max_depth: Maximum depth, same scale.
    """

    file_key: str | None = None
    name: str | None = None
    name_contains: str | None = None
    node_type: str | None = None
    node_id: str | None = None
    page: str | None = None
    name_regex: str | None = None
    parent_name: str | None = None
    fill_hex: str | None = None
    font_family: str | None = None
    component_name: str | None = None
    visible: bool | None = None
    locked: bool | None = None
    default_named: bool | None = None
    min_depth: int | None = Field(default=None, ge=0)
    max_depth: int | None = Field(default=None, ge=0)

    @field_validator("name_regex")
    @classmethod
    def validate_name_regex(cls, value: str | None) -> str | None:
        """Rejects a pattern the runtime could not compile.

        Args:
            value: Authored regular expression, or None.

        Returns:
            The unchanged pattern when it compiles.

        Raises:
            ValueError: If the pattern is invalid. Returning it unchecked would
                raise ``re.error`` mid-walk, which is not classified as agent
                output and takes the whole spec down as unscored.
        """
        if value is None:
            return value
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(
                f"name_regex is not a valid regular expression: {exc}"
            ) from exc
        return value

    @field_validator("fill_hex")
    @classmethod
    def validate_fill_hex(cls, value: str | None) -> str | None:
        """Normalizes a colour selector to upper-case ``#RRGGBB``.

        Args:
            value: Authored colour, or None.

        Returns:
            The colour upper-cased, so a case difference cannot silently make a
            correct design report zero matches.

        Raises:
            ValueError: If the value is not a six-digit hex colour.
        """
        if value is None:
            return value
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError(f"fill_hex must be #RRGGBB, got {value!r}")
        return value.upper()

    @model_validator(mode="after")
    def validate_depth_window(self) -> "FigmaNodeSelector":
        """Rejects a depth window that can never match.

        Returns:
            The validated selector.

        Raises:
            ValueError: If ``min_depth`` exceeds ``max_depth``.
        """
        if (
            self.min_depth is not None
            and self.max_depth is not None
            and self.min_depth > self.max_depth
        ):
            raise ValueError(
                f"min_depth ({self.min_depth}) is greater than max_depth "
                f"({self.max_depth}); no node can match"
            )
        return self


class FigmaFindNodesInput(FigmaNodeSelector):
    """Input for ``figma.find_nodes``.

    Attributes:
        limit: Maximum number of summaries returned alongside the count.
    """

    limit: int = Field(default=DEFAULT_NODE_LIMIT, ge=1, le=MAX_NODE_LIMIT)


class FigmaFindNodesOutput(StrictModel):
    """Matches for a node selector.

    ``match_count`` is the load-bearing field and is never truncated: asserting
    it ``equals 0`` is how a verifier states that something must NOT exist, and
    ``equals 1`` is how it rejects duplicates. ``matches`` is the bounded
    evidence that accompanies it.

    Attributes:
        match_count: Total nodes matching the selector across the whole file.
        matches: Up to ``limit`` matching node summaries, in document order.
        truncated: Whether ``match_count`` exceeded ``limit``.
    """

    match_count: int
    matches: list[FigmaNodeSummary]
    truncated: bool


class FindNodes(SourceCommand[FigmaFindNodesInput, FigmaFindNodesOutput]):
    """Count and describe the nodes matching a selector."""

    name = "find_nodes"
    input_model = FigmaFindNodesInput
    output_model = FigmaFindNodesOutput

    def run(
        self, source_input: FigmaFindNodesInput, context: SourceContext
    ) -> FigmaFindNodesOutput:
        """Selects nodes from one file of a Figma state document.

        Args:
            source_input: Validated selector input.
            context: Source runtime context.

        Returns:
            The total match count with bounded per-node evidence.
        """
        figma_file = load_file(source_input, context)
        match_count, matches = matches_nodes(figma_file, source_input)
        return FigmaFindNodesOutput(
            match_count=match_count,
            matches=[
                summarize(
                    match.node,
                    match.parent.get("id") if match.parent else None,
                    match.page.get("id"),
                    match.page.get("name"),
                )
                for match in matches[: source_input.limit]
            ],
            truncated=match_count > source_input.limit,
        )


class FigmaDocument(NamedTuple):
    """A resolved design: the whole state document and the file to grade.

    Attributes:
        state: The Figma state mapping the design was found in.
        file: The one file inside it this command analyses.
    """

    state: dict[str, Any]
    file: dict[str, Any]


def load_file(source_input: Any, context: SourceContext) -> dict[str, Any]:
    """Resolves the declared path and picks the file every command starts from.

    Args:
        source_input: Any command input carrying ``path`` and ``file_key``.
        context: Source runtime context.

    Returns:
        The selected file mapping.
    """
    return load_document(source_input, context).file


def load_document(source_input: Any, context: SourceContext) -> FigmaDocument:
    """Resolves a declared design filename to the design it names.

    The declared path is a design document's own filename, and exactly one of
    two things is on disk under it:

    * **the document itself** -- a golden archive's ``.fig``, a seeded fixture,
      a design copied into the workspace. It is decoded in-process by the
      vendored fig-kiwi decoder, which yields the same state shape the gym
      holds, so every command below is unaware of which world it is in;
    * **nothing**, because this is a live run against a design gym. The harness
      fetched ``GET /state?include_db=true`` and materialized it beside the
      workspace as :data:`RUNTIME_STATE_FILENAME`; the declared filename then
      says WHICH design in that state to grade, by matching the design's name.

    Resolution never falls the other way. A file that exists at the declared
    path but is not a fig-kiwi container is an error rather than a quiet
    fallback to the materialized state: accepting a JSON body under a ``.fig``
    name would make the two halves of the contract interchangeable again.

    Args:
        source_input: Any command input carrying ``path`` and ``file_key``.
        context: Source runtime context.

    Returns:
        The resolved state document and the file to analyse.

    Raises:
        SourceAuthoringError: If neither a design document nor a materialized
            state can be found, or if what was found cannot be read as one.
    """
    declared = validate_design_path(source_input.path)
    design_name = PurePosixPath(declared.replace("\\", "/")).stem
    resolved = context.resolve_path(declared)
    if resolved.is_file():
        state = parse_design(resolved, design_name, source_input.file_key)
        return FigmaDocument(state, select_file(state, source_input.file_key))

    state_path = context.resolve_path(RUNTIME_STATE_FILENAME)
    if state_path.is_file():
        state = load_state(state_path)
        return FigmaDocument(
            state, select_named_file(state, design_name, source_input.file_key)
        )

    raise SourceAuthoringError(
        f"figma: no design to grade. Looked for the design document {declared!r} "
        f"(at {resolved}) and for the harness-materialized "
        f"{RUNTIME_STATE_FILENAME!r} beside it; neither exists. A live design "
        "gym writes the second; a golden or a seeded fixture provides the first."
    )


def parse_design(path: Path, design_name: str, file_key: str | None) -> dict[str, Any]:
    """Decodes a ``.fig``/``.jam`` container into a Figma state document.

    The decoded document is cached by file identity and returned SHARED, not
    copied: a 57 MB design deep-copied per check costs as much as re-decoding
    it. Every command in this module only reads the tree.

    Args:
        path: Resolved path to the design document.
        design_name: The declared filename's stem, used as the design's name.
        file_key: The author's explicit file key, if any. No gym assigned this
            document a key, so the name doubles as one when none was given --
            which keeps ``$.file_key`` assertable and lets ``select_file``
            resolve an explicit key against the parsed document.

    Returns:
        The parsed state mapping, holding exactly one file.

    Raises:
        SourceAuthoringError: If the file is not a fig-kiwi/fig-jam container,
            or cannot be decoded as one. A malformed design is an authoring or
            environment fault; classifying it as bad agent output would score
            the MODEL zero for it.
    """
    try:
        identity = path.stat()
    except OSError as exc:  # pragma: no cover - is_file() just succeeded
        raise SourceAuthoringError(f"figma: {path.name} is unreadable: {exc}") from exc
    cache_key = (str(path), identity.st_size, identity.st_mtime_ns)
    cached = PARSE_CACHE.get(cache_key)
    if cached is not None:
        PARSE_CACHE.move_to_end(cache_key)
        return cached

    data = path.read_bytes()
    head = data[:8]
    if not (head.startswith(ZIP_MAGIC) or head in DESIGN_MAGICS):
        raise SourceAuthoringError(
            f"figma: {path.name} is not a fig-kiwi/fig-jam container "
            f"(it starts {head[:8]!r}). The path names the design document "
            "itself; a JSON state document saved under a design name is not "
            "accepted."
        )
    try:
        state = parse_fig_bytes(
            data, file_key=file_key or design_name, file_name=design_name
        )
    except RuntimeError as exc:
        # The one dependency the decoder imports lazily: a zstd-compressed
        # block cannot be read without it, and the hint has to name where to
        # add it rather than leaving an operator to guess.
        raise SourceAuthoringError(
            f"figma: {path.name} could not be read: {exc}"
        ) from exc
    except Exception as exc:
        # Deliberately broad. The decoder is a binary codec: a truncated or
        # foreign payload surfaces as EOFError, struct.error, KeyError or
        # IndexError depending on where it ran out, and every one of them is an
        # authoring fault. Letting them escape would take the whole spec down
        # unscored instead of naming the file that is wrong.
        raise SourceAuthoringError(
            f"figma: {path.name} could not be decoded as a design document "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    PARSE_CACHE[cache_key] = state
    while len(PARSE_CACHE) > PARSE_CACHE_SIZE:
        PARSE_CACHE.popitem(last=False)
    return state


def select_named_file(
    state: dict[str, Any], design_name: str, file_key: str | None
) -> dict[str, Any]:
    """Picks the file a declared design filename names inside a live state.

    A live gym's state can hold several designs, and the connector names each
    file after the source document it was built from -- so the filename the
    verifier declares is what tells them apart.

    An explicit ``file_key`` still wins outright -- it is the author saying
    which design regardless of names, and letting a name quietly satisfy a
    lookup would stop a key that matches NOTHING from being reported as the
    typo it is.

    Otherwise the name decides, but only when it decides UNIQUELY. One match
    wins; zero or several leave the choice exactly where it was before this
    contract existed, with :func:`select_file`'s fallback order
    (``current_file_key``, then the only file). So a state the old path graded
    correctly is still graded the same way, and the declared name can only add
    precision, never take it away.

    Args:
        state: Parsed Figma state document.
        design_name: The declared filename's stem.
        file_key: Explicit file key, or None.

    Returns:
        The selected file mapping.

    Raises:
        SourceAuthoringError: If neither the name nor the fallback order picks
            one file.
    """
    files = state.get("files") or []
    named: list[dict[str, Any]] = []
    if file_key is None and isinstance(files, list):
        named = [
            figma_file
            for figma_file in files
            if isinstance(figma_file, dict) and _is_named(figma_file, design_name)
        ]
        if len(named) == 1:
            return named[0]
    try:
        return select_file(state, file_key)
    except SourceAuthoringError as exc:
        if file_key is not None or named:
            raise
        # The author named a design that is not in this state at all. Saying so
        # is the difference between "fix the filename" and "add a file_key".
        available = ", ".join(
            sorted(repr(_text(f.get("name"))) for f in files if isinstance(f, dict))
        )
        raise SourceAuthoringError(
            f"{exc}. No file is named {design_name!r} either -- the design the "
            f"path names (have: {available})"
        ) from exc


def _is_named(figma_file: dict[str, Any], design_name: str) -> bool:
    """Whether a state file is the design a declared filename names.

    Case-insensitive, and it accepts the name with or without a design suffix:
    the connector sets a file's name to the source filename's stem, but a state
    captured from elsewhere may have kept the extension. Only the suffix is
    stripped, never a version segment -- ``Checkout v1.2`` keeps its ``.2``.

    Args:
        figma_file: One file mapping from a state document.
        design_name: The declared filename's stem.

    Returns:
        Whether the two name the same design.
    """
    wanted = design_name.casefold()
    name = _text(figma_file.get("name"))
    if name.casefold() == wanted:
        return True
    as_path = PurePosixPath(name)
    return (
        as_path.suffix.casefold() in DESIGN_SUFFIXES
        and as_path.stem.casefold() == wanted
    )


def load_state(path: Path) -> dict[str, Any]:
    """Reads a Figma state document from disk.

    The document here is the harness's materialization of a live gym's tree
    (:data:`RUNTIME_STATE_FILENAME`), or a ``figma_seed/*.json`` fixture in a
    test -- never an authored verifier path. :func:`load_document` is what a
    command calls.

    Args:
        path: Resolved path to the JSON document.

    Returns:
        The parsed state mapping.

    Raises:
        SourceAuthoringError: If the file is not JSON, or is JSON that is not a
            Figma state document. A Git LFS pointer -- what an unpulled seed
            actually contains -- fails here rather than grading as an empty
            design.
    """
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceAuthoringError(
            f"figma: {path.name} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(state, dict) or "files" not in state:
        raise SourceAuthoringError(
            f"figma: {path.name} is not a Figma state document (no 'files' key). "
            "Expected the tree from GET /state?include_db=true or a figma_seed file."
        )
    return state


def select_file(state: dict[str, Any], file_key: str | None) -> dict[str, Any]:
    """Chooses which file in the state document to analyse.

    Args:
        state: Parsed Figma state document.
        file_key: Explicit file key, or None to fall back.

    Returns:
        The selected file mapping.

    Raises:
        SourceAuthoringError: If the requested key is absent, or if no key was
            given and the fallback is ambiguous. Silently analysing the first of
            several files would grade a different design than the author named.
    """
    files = state.get("files") or []
    if not isinstance(files, list) or not files:
        raise SourceAuthoringError("figma: state document holds no files")
    if file_key is not None:
        for figma_file in files:
            if figma_file.get("fileKey") == file_key:
                return figma_file
        available = ", ".join(sorted(str(f.get("fileKey")) for f in files))
        raise SourceAuthoringError(
            f"figma: no file with fileKey {file_key!r} (have: {available})"
        )
    current = state.get("current_file_key")
    if current is not None:
        for figma_file in files:
            if figma_file.get("fileKey") == current:
                return figma_file
    if len(files) == 1:
        return files[0]
    available = ", ".join(sorted(str(f.get("fileKey")) for f in files))
    raise SourceAuthoringError(
        f"figma: state holds {len(files)} files and no usable current_file_key; "
        f"pass file_key explicitly (have: {available})"
    )


def _descend(
    node: dict[str, Any], parent: dict[str, Any] | None, depth: int = -1
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None, int]]:
    """Yields a node and every descendant, parents first.

    The parent is carried as the mapping rather than as its id because the
    selector and ``read_node`` both report the parent's *name*, and looking it
    up afterwards would mean a second walk.

    Args:
        node: Node mapping to start from.
        parent: The node's parent mapping, or None at a page root.
        depth: Depth of ``node``. A page sits at -1 so its direct children --
            the artboards -- sit at 0.

    Yields:
        Triples of node mapping, parent mapping, and depth.
    """
    yield node, parent, depth
    for child in _children(node):
        yield from _descend(child, node, depth + 1)


def summarize(
    node: dict[str, Any],
    parent_id: str | None,
    page_id: str | None,
    page_name: str | None,
) -> FigmaNodeSummary:
    """Builds one node's flat summary.

    Args:
        node: Node mapping.
        parent_id: Id of the node's parent.
        page_id: Id of the page the node sits on.
        page_name: Name of that page.

    Returns:
        The node's addressable properties.
    """
    # absoluteBoundingBox is Optional[Union[AbsoluteBoundingBox, str]] -- the
    # model documents that it "may be a string when malformed". Anything that is
    # not a mapping simply yields no geometry; it must never raise, because an
    # AttributeError here is not classified as agent output and would take the
    # whole spec down as unscored.
    box = node.get("absoluteBoundingBox")
    box = box if isinstance(box, dict) else {}
    return FigmaNodeSummary(
        id=_text(node.get("id")),
        name=_text(node.get("name")),
        type=_text(node.get("type")),
        parent_id=parent_id,
        page_id=page_id,
        page_name=page_name,
        # Figma omits `visible` on visible nodes, and a tree serialized without
        # exclude_none writes an explicit null for the same thing. Only an
        # explicit false means hidden.
        visible=node.get("visible") is not False,
        x=box.get("x"),
        y=box.get("y"),
        width=box.get("width"),
        height=box.get("height"),
        fills=_fills(node.get("fills")),
        # The field is declared `text` with AliasChoices("characters", "text"),
        # so seed data carries either spelling.
        characters=node.get("characters", node.get("text")),
        child_count=len(_children(node)),
    )


def _text(value: Any) -> str:
    """Coerces an optional model string to a plain string.

    ``name`` and ``type`` are ``Optional[str]``, so an unnamed node carries an
    explicit null. ``str(None)`` would report it as the literal ``"None"``,
    which then answers ``name="None"`` and ``name_contains="on"`` selectors and
    grows a bogus ``"None"`` bucket in a node-type census.

    Args:
        value: Raw field value.

    Returns:
        The string, or ``""`` when unset.
    """
    return value if isinstance(value, str) else ""


def _fills(value: Any) -> Any:
    """Normalizes the ``fills`` union without flattening it.

    Args:
        value: Raw ``fills`` value: a list of paints, a style-reference string,
            or a ``{"root": ...}`` mapping from a ``RootModel`` dumped without
            ``mode="json"``.

    Returns:
        The paints or the style reference, unwrapped but otherwise unchanged.
    """
    if isinstance(value, dict) and set(value) == {"root"}:
        return value["root"]
    return value


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns a node's dict children, skipping malformed entries.

    A ``children`` list can carry a bare id string or a null. Calling ``.get``
    on one raises AttributeError, which is not classified as agent output and
    would abort the entire spec as unscored rather than failing one assertion.

    Args:
        node: Node mapping.

    Returns:
        Only the children that are mappings.
    """
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, dict)]


# --- reading one node's properties without assuming the happy shape ----------


def _as_float(value: Any, default: float | None = None) -> float | None:
    """Coerces a JSON number, refusing bools and strings.

    Args:
        value: Raw field value.
        default: What to return when the value is not a number.

    Returns:
        The number as a float, or ``default``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _paints(value: Any) -> list[dict[str, Any]]:
    """The paint mappings of a ``fills`` or ``strokes`` value.

    Args:
        value: Raw fills/strokes value: a paint list, a style-reference string,
            a ``{"root": ...}`` wrapper, or None.

    Returns:
        Only the entries that are mappings. A style reference yields none,
        because a reference carries no colour this reader can resolve.
    """
    resolved = _fills(value)
    if not isinstance(resolved, list):
        return []
    return [paint for paint in resolved if isinstance(paint, dict)]


def _is_style_reference(value: Any) -> bool:
    """Whether a fills value is a shared style reference rather than paints."""
    return isinstance(_fills(value), str) and bool(_fills(value))


def _has_visible_paint(value: Any) -> bool:
    """Whether a fills/strokes value paints anything at all.

    A style reference counts: the design does have a fill, this reader just
    cannot say which colour. Treating it as "no fill" would report a styled
    frame as an empty one.
    """
    if _is_style_reference(value):
        return True
    return any(paint.get("visible") is not False for paint in _paints(value))


def _hex(color: Any) -> str | None:
    """Renders a Figma colour mapping as ``#RRGGBB``.

    Alpha is deliberately dropped: a card asserts "the button is #1D4ED8", and
    an eight-digit hex would make the same colour at 99% opacity a different
    string. Opacity is reported separately where it matters.

    Args:
        color: Raw ``color`` mapping with ``r``/``g``/``b`` in 0..1.

    Returns:
        The upper-case hex triplet, or None when the mapping is unusable.
    """
    if not isinstance(color, dict):
        return None
    channels = [_as_float(color.get(key)) for key in ("r", "g", "b")]
    if any(channel is None for channel in channels):
        return None
    return "#" + "".join(
        f"{max(0, min(255, round(channel * 255))):02X}" for channel in channels
    )


def _solid_hex(value: Any) -> str | None:
    """The first visible SOLID paint of a fills/strokes value, as ``#RRGGBB``.

    Args:
        value: Raw fills or strokes value.

    Returns:
        The colour, or None when the node has no visible solid paint (a
        gradient, an image, a style reference, or nothing at all).
    """
    for paint in _paints(value):
        if paint.get("type") != "SOLID" or paint.get("visible") is False:
            continue
        return _hex(paint.get("color"))
    return None


def _background_hex(value: Any) -> str | None:
    """The first visible SOLID paint, but only when it is opaque.

    A background is only a background if you cannot see through it. A
    half-transparent panel over an unknown parent has no computable contrast,
    so it yields None and the text on it is skipped rather than scored against
    a colour nobody sees.

    Args:
        value: Raw fills value.

    Returns:
        The colour, or None.
    """
    for paint in _paints(value):
        if paint.get("type") != "SOLID" or paint.get("visible") is False:
            continue
        color = paint.get("color")
        if not isinstance(color, dict):
            return None
        if _as_float(color.get("a"), 1.0) < 0.99:
            return None
        if _as_float(paint.get("opacity"), 1.0) < 0.99:
            return None
        return _hex(color)
    return None


def _is_annotation_red(value: Any) -> bool:
    """Whether a fills value is the red designers mark up redlines in."""
    for paint in _paints(value):
        if paint.get("type") != "SOLID" or paint.get("visible") is False:
            continue
        color = paint.get("color")
        if not isinstance(color, dict):
            return False
        return (
            _as_float(color.get("r"), 0.0) > 0.8
            and _as_float(color.get("g"), 1.0) < 0.3
            and _as_float(color.get("b"), 1.0) < 0.3
        )
    return False


def _luminance(hex_code: str) -> float:
    """WCAG relative luminance of an ``#RRGGBB`` colour."""
    total = 0.0
    for offset, weight in ((1, 0.2126), (3, 0.7152), (5, 0.0722)):
        channel = int(hex_code[offset : offset + 2], 16) / 255
        linear = (
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )
        total += weight * linear
    return total


def _contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colours, 1.0 to 21.0."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _style_value(node: dict[str, Any], key: str) -> Any:
    """Reads one key off a node's ``style`` mapping, tolerating a missing one."""
    style = node.get("style")
    return style.get(key) if isinstance(style, dict) else None


def _font_weight(node: dict[str, Any]) -> int | None:
    """Resolves a numeric font weight from either spelling.

    Seeded text carries ``style.fontPostScriptName`` ("SemiBold", sometimes
    family-qualified as "Inter-SemiBold"); text a tool wrote carries a numeric
    ``style.fontWeight`` and no postscript name. Both must land on one number
    or a weight assertion grades a design differently depending on who built it.

    Args:
        node: Raw node mapping.

    Returns:
        The weight, or None when neither spelling is present or recognised.
    """
    numeric = _as_float(_style_value(node, "fontWeight"))
    if numeric is not None:
        return int(numeric)
    name = _style_value(node, "fontPostScriptName")
    if not isinstance(name, str):
        return None
    for candidate in (name, name.rsplit("-", 1)[-1]):
        key = re.sub(r"[^a-z]", "", candidate.lower())
        weight = FONT_WEIGHT_BY_STYLE_NAME.get(key)
        if weight is not None:
            return weight
    return None


def _bounds(node: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """The node's ``absoluteBoundingBox`` as ``(x, y, width, height)``.

    ``absoluteBoundingBox`` is declared ``Optional[Union[AbsoluteBoundingBox,
    str]]`` and documented as possibly a string when malformed, so anything
    that is not four numbers yields no geometry rather than raising.
    """
    box = node.get("absoluteBoundingBox")
    if not isinstance(box, dict):
        return None
    values = [_as_float(box.get(key)) for key in ("x", "y", "width", "height")]
    if any(value is None for value in values):
        return None
    return (values[0], values[1], values[2], values[3])


def component_name(node: dict[str, Any], components: dict[str, Any]) -> str | None:
    """Resolves an instance's component name.

    Args:
        node: Raw node mapping.
        components: The file's ``components`` map.

    Returns:
        The mapped component's name; failing that, an INSTANCE's own name,
        which is what a designer sees in the layer panel for a component that
        lives in another library file; else None.
    """
    component_id = node.get("componentId")
    if isinstance(component_id, str):
        entry = components.get(component_id)
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                return name
    if _text(node.get("type")) == "INSTANCE":
        return _text(node.get("name")) or None
    return None


def _normalise_name(name: str) -> str:
    """Lowercases a name and drops everything that is not alphanumeric."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _label_key(text: str) -> str:
    """The key two spellings of the same label share."""
    key = _normalise_name(text)
    return LABEL_SYNONYMS.get(key, key)


def _number(value: Any) -> str:
    """Renders a number for a signature string, without a trailing ``.0``."""
    number = _as_float(value)
    if number is None:
        return ""
    return str(int(number)) if number.is_integer() else str(round(number, 3))


def _subtree_signature(node: dict[str, Any]) -> str:
    """A shallow structural fingerprint of a node and its direct children.

    Deliberately shallow: two screens that differ only far down the tree are
    still the same screen duplicated, and hashing the whole subtree of a 72k
    node file would cost more than every other metric combined.
    """
    bounds = _bounds(node)
    children = _children(node)
    child_types = ",".join(sorted(_text(child.get("type")) for child in children))
    width = _number(round(bounds[2])) if bounds else ""
    height = _number(round(bounds[3])) if bounds else ""
    return f"{_text(node.get('type'))}|{len(children)}|{width}|{height}|{child_types}"


def _device_bucket(node: dict[str, Any], width: float | None) -> str:
    """Which device a top-level frame represents.

    Args:
        node: Raw frame mapping.
        width: The frame's width, or None.

    Returns:
        One of ``desktop``, ``tablet``, ``mobile`` or ``other``.
    """
    name = _text(node.get("name"))
    for bucket, terms in DEVICE_NAME_HINTS:
        if _term_pattern(terms).search(name):
            return bucket
    if width is None:
        return "other"
    if width >= DESKTOP_MIN_WIDTH:
        return "desktop"
    if width >= TABLET_MIN_WIDTH:
        return "tablet"
    if width <= MOBILE_MAX_WIDTH:
        return "mobile"
    return "other"


def _page_role(page: dict[str, Any]) -> str:
    """Which role a page's name claims, ``other`` when it claims none."""
    name = _text(page.get("name"))
    for role, terms in PAGE_ROLE_TERMS:
        if _term_pattern(terms).search(name):
            return role
    return "other"


class WalkedNode(NamedTuple):
    """One node plus the ancestry facts a summary needs but a node cannot hold.

    Attributes:
        node: The raw node mapping.
        parent: The node's parent mapping, or None at a page root.
        depth: Depth of the node, 0 being a direct child of a page.
        page: The CANVAS the node sits on.
        hidden: Whether the node or any ancestor is explicitly invisible.
        background: Nearest ancestor's opaque SOLID fill, as ``#RRGGBB``.
        inside_instance: Whether any ancestor is an INSTANCE.
        auto_layout_depth: How many auto-layout frames are on the path from the
            page to this node, this node included.
    """

    node: dict[str, Any]
    parent: dict[str, Any] | None
    depth: int
    page: dict[str, Any]
    hidden: bool
    background: str | None
    inside_instance: bool
    auto_layout_depth: int


def walk_pages(
    figma_file: dict[str, Any], page: str | None = None
) -> Iterator[WalkedNode]:
    """Walks one file's pages once, carrying every inherited fact with it.

    Iterative rather than recursive: a deeply nested design would otherwise
    trade a real answer for a RecursionError, and that error is not classified
    as agent output, so it would take the whole spec down as unscored.

    Args:
        figma_file: One file mapping from a Figma state document.
        page: Name or id of a single page to restrict the walk to.

    Yields:
        Every node under the selected pages, in document order, pages included.
    """
    document = figma_file.get("document")
    for page_node in _children(document if isinstance(document, dict) else {}):
        if page is not None and page not in (
            page_node.get("id"),
            page_node.get("name"),
        ):
            continue
        stack: list[
            tuple[
                dict[str, Any], dict[str, Any] | None, int, bool, str | None, bool, int
            ]
        ] = [(page_node, None, -1, False, None, False, 0)]
        while stack:
            (
                node,
                parent,
                depth,
                ancestor_hidden,
                background,
                inside_instance,
                auto_layout_depth,
            ) = stack.pop()
            hidden = ancestor_hidden or node.get("visible") is False
            node_type = _text(node.get("type"))
            own_auto_layout = (
                node_type == FRAME_TYPE and node.get("layoutMode") in AUTO_LAYOUT_MODES
            )
            own_auto_layout_depth = auto_layout_depth + (1 if own_auto_layout else 0)
            yield WalkedNode(
                node,
                parent,
                depth,
                page_node,
                hidden,
                background,
                inside_instance,
                own_auto_layout_depth,
            )
            children = _children(node)
            if not children:
                continue
            child_background = _background_hex(node.get("fills")) or background
            child_inside_instance = inside_instance or node_type == "INSTANCE"
            for child in reversed(children):
                stack.append(
                    (
                        child,
                        node,
                        depth + 1,
                        hidden,
                        child_background,
                        child_inside_instance,
                        own_auto_layout_depth,
                    )
                )


class _Cap:
    """Applies one command's ``limit`` to every list it returns.

    Counts are computed over the whole tree and never truncated; only the
    evidence beside them is bounded. This records whether any cap actually bit,
    so a single ``truncated`` flag can tell the truth for a whole output.
    """

    def __init__(self, limit: int) -> None:
        """Initializes the cap.

        Args:
            limit: Maximum entries any one list may carry.
        """
        self.limit = limit
        self.hit = False

    def take(self, values: Any) -> list[Any]:
        """Returns at most ``limit`` values, recording whether any were dropped."""
        values = list(values)
        if len(values) > self.limit:
            self.hit = True
            return values[: self.limit]
        return values

    def take_map(self, items: Any) -> dict[Any, Any]:
        """Returns at most ``limit`` mapping entries, in the order supplied."""
        return dict(self.take(items))


class FigmaMatch(NamedTuple):
    """One selected node with the context a summary or ``read_node`` reports.

    Attributes:
        node: The raw node mapping.
        parent: The node's parent mapping, or None at a page root.
        depth: Depth of the node, 0 being a direct child of a page.
        page: The CANVAS the node sits on.
    """

    node: dict[str, Any]
    parent: dict[str, Any] | None
    depth: int
    page: dict[str, Any]


def matches_nodes(
    figma_file: dict[str, Any], selector: FigmaNodeSelector
) -> tuple[int, list[FigmaMatch]]:
    """Finds the nodes a selector picks, without summarizing the whole file.

    The predicate reads raw mapping fields only, so the validated per-node
    model is built by the caller for the nodes it actually returns. On the
    largest committed seed (112 MB) that is the difference between the cost of
    a question and the cost of a document.

    Args:
        figma_file: One file mapping from a Figma state document.
        selector: Validated selector fields.

    Returns:
        The total match count, and every match in document order.
    """
    matches: list[FigmaMatch] = []
    # Compiled once rather than per node: `re` caches compiled patterns, but the
    # cache lookup still runs for every one of a 72k-node file's names.
    pattern = re.compile(selector.name_regex) if selector.name_regex else None
    components = figma_file.get("components")
    components = components if isinstance(components, dict) else {}
    document = figma_file.get("document")
    for page in _children(document if isinstance(document, dict) else {}):
        if selector.page is not None and selector.page not in (
            page.get("id"),
            page.get("name"),
        ):
            continue
        for node, parent, depth in _descend(page, None):
            if _matches(node, selector, parent, depth, pattern, components):
                matches.append(FigmaMatch(node, parent, depth, page))
    return len(matches), matches


def _matches(
    node: dict[str, Any],
    selector: FigmaNodeSelector,
    parent: dict[str, Any] | None,
    depth: int,
    pattern: re.Pattern[str] | None,
    components: dict[str, Any],
) -> bool:
    """Whether one raw node satisfies every selector field that was supplied.

    Args:
        node: Raw node mapping.
        selector: Validated selector fields.
        parent: The node's parent mapping, or None at a page root.
        depth: Depth of the node.
        pattern: Pre-compiled ``name_regex``, or None.
        components: The file's component map, for ``component_name``.

    Returns:
        True when every supplied selector field matches.
    """
    name = _text(node.get("name"))
    if selector.name is not None and name != selector.name:
        return False
    if selector.name_contains is not None and selector.name_contains not in name:
        return False
    if selector.node_type is not None and _text(node.get("type")) != selector.node_type:
        return False
    if selector.node_id is not None and _text(node.get("id")) != selector.node_id:
        return False
    if pattern is not None and pattern.search(name) is None:
        return False
    if selector.parent_name is not None and (
        parent is None or _text(parent.get("name")) != selector.parent_name
    ):
        return False
    if (
        selector.fill_hex is not None
        and _solid_hex(node.get("fills")) != selector.fill_hex
    ):
        return False
    if selector.font_family is not None and _style_value(node, "fontFamily") != (
        selector.font_family
    ):
        return False
    if selector.component_name is not None and (
        component_name(node, components) != selector.component_name
    ):
        return False
    if selector.visible is not None and (node.get("visible") is not False) != (
        selector.visible
    ):
        return False
    if selector.locked is not None and (node.get("locked") is True) != selector.locked:
        return False
    if (
        selector.default_named is not None
        and bool(DEFAULT_NAME_PATTERN.match(name)) != selector.default_named
    ):
        return False
    if selector.min_depth is not None and depth < selector.min_depth:
        return False
    if selector.max_depth is not None and depth > selector.max_depth:
        return False
    return True


class FigmaReadNodeInput(FigmaNodeSelector):
    """Input for ``figma.read_node``.

    Carries the same selector fields as ``find_nodes``; the difference is the
    rule below, not the vocabulary.
    """

    # find_nodes accepts an empty selector because "how many nodes are there" is
    # a real question. "Read some node" is not, so this one refuses.
    @model_validator(mode="after")
    def require_a_selector(self) -> "FigmaReadNodeInput":
        """Requires at least one identifying field.

        Returns:
            The validated input.

        Raises:
            ValueError: If no selector field was supplied.
        """
        if (
            self.node_id is None
            and self.name is None
            and self.name_contains is None
            and self.name_regex is None
        ):
            raise ValueError(
                "figma.read_node requires node_id, name, name_contains or name_regex"
            )
        return self


class FigmaChildRef(StrictModel):
    """One direct child, in document order.

    Attributes:
        id: Child node id.
        name: Child node name.
        type: Child Figma node type.
    """

    id: str
    name: str
    type: str


class FigmaReadNodeOutput(FigmaNodeSummary):
    """One node in full addressable detail.

    Extends the summary with the fields that only matter once a single node has
    been pinned down: its strokes, corner radius, opacity and rotation, and its
    children in the order the document stores them -- which is what makes an
    ordering requirement gradable at all. Geometry stays on the flat
    ``x``/``y``/``width``/``height`` fields inherited from the summary; there is
    deliberately no second, nested spelling of it.

    The flat scalars below exist for the same reason ``x``/``y``/``width``/
    ``height`` do. A card that asks "is the heading semibold" should not have to
    write ``$.style.fontPostScriptName`` and know that a tool-written node
    spells the same fact ``$.style.fontWeight`` instead, nor should a colour
    check have to reach into ``$.fills[0].color.r`` and compare three floats.
    Every one of them is None when the node does not carry the property, so a
    ``not_equals`` assertion cannot pass merely because a field is missing --
    check the value you expect, not the absence of another.

    Attributes:
        strokes: The node's stroke paints, unmodified.
        stroke_weight: Stroke weight, or None.
        corner_radius: Corner radius, or None.
        opacity: Node opacity, or None.
        rotation: Node rotation, or None.
        children: Direct children in document order.
        fill_hex: First visible SOLID fill as ``#RRGGBB``, alpha dropped. None
            for a gradient, an image, or a shared style reference.
        stroke_hex: The same for the node's strokes.
        font_family: ``style.fontFamily``.
        font_size: ``style.fontSize``.
        font_weight: Numeric weight, from ``style.fontWeight`` when the node
            carries one, else mapped from ``style.fontPostScriptName``.
        line_height_px: ``style.lineHeightPx``.
        letter_spacing_px: ``style.letterSpacing``.
        layout_mode: ``HORIZONTAL``/``VERTICAL`` when the frame uses auto
            layout, else None.
        item_spacing: Auto-layout gap between children.
        padding_left: Auto-layout left padding.
        padding_right: Auto-layout right padding.
        padding_top: Auto-layout top padding.
        padding_bottom: Auto-layout bottom padding.
        component_id: An INSTANCE's ``componentId``.
        component_name: The component's name from the file's component map,
            falling back to an INSTANCE's own name when the component lives in
            another library file.
        depth: 0 for a direct child of a page; a page itself is -1.
        parent_name: Name of the node's parent, which for a top-level frame is
            the page.
        is_default_name: Whether Figma named this layer, not a person.
        locked: Whether the layer is locked.
        effect_count: How many effects (shadows, blurs) the node carries.
        annotation_count: How many annotations the node carries.
    """

    strokes: list[Any] = Field(default_factory=list)
    stroke_weight: float | None
    corner_radius: float | None
    opacity: float | None
    rotation: float | None
    children: list[FigmaChildRef]
    fill_hex: str | None = None
    stroke_hex: str | None = None
    font_family: str | None = None
    font_size: float | None = None
    font_weight: int | None = None
    line_height_px: float | None = None
    letter_spacing_px: float | None = None
    layout_mode: str | None = None
    item_spacing: float | None = None
    padding_left: float | None = None
    padding_right: float | None = None
    padding_top: float | None = None
    padding_bottom: float | None = None
    component_id: str | None = None
    component_name: str | None = None
    depth: int | None = None
    parent_name: str | None = None
    is_default_name: bool = False
    locked: bool = False
    effect_count: int = 0
    annotation_count: int = 0


class ReadNode(SourceCommand[FigmaReadNodeInput, FigmaReadNodeOutput]):
    """Read exactly one node, refusing to guess when the selector is ambiguous."""

    name = "read_node"
    input_model = FigmaReadNodeInput
    output_model = FigmaReadNodeOutput

    def run(
        self, source_input: FigmaReadNodeInput, context: SourceContext
    ) -> FigmaReadNodeOutput:
        """Resolves a selector to a single node and reports it in full.

        Args:
            source_input: Validated node selector.
            context: Source runtime context.

        Returns:
            The one matching node's full detail.

        Raises:
            SourceDataError: If the selector matches no node, or more than one.
                Both are facts about the design the model produced, so the
                engine scores them as failures. They are deliberately NOT
                SourceAuthoringError: that propagates as a fatal error, which
                the harness records as `unscored`, and an unscored verifier
                whose peers pass makes the whole run ungradable -- dropping it
                from the headline numerator and denominator. A model that never
                built the frame would then vanish from the results instead of
                failing. An author who wants to assert uniqueness uses
                figma.find_nodes and match_count.
        """
        figma_file = load_file(source_input, context)
        _, matches = matches_nodes(figma_file, source_input)
        if not matches:
            raise SourceDataError(
                f"figma.read_node: no node matches {describe(source_input)}"
            )
        if len(matches) > 1:
            found = ", ".join(
                f"{_text(match.node.get('id'))} ({_text(match.node.get('type'))})"
                for match in matches[:5]
            )
            raise SourceDataError(
                f"figma.read_node: {len(matches)} nodes match {describe(source_input)}: "
                f"{found}. Narrow it with node_type or page, or use figma.find_nodes to "
                "assert the count."
            )
        match = matches[0]
        node = match.node
        components = figma_file.get("components")
        components = components if isinstance(components, dict) else {}
        summary = summarize(
            node,
            match.parent.get("id") if match.parent else None,
            match.page.get("id"),
            match.page.get("name"),
        )
        annotations = node.get("annotations")
        component_id = node.get("componentId")
        return FigmaReadNodeOutput(
            **summary.model_dump(),
            strokes=list(node.get("strokes") or []),
            stroke_weight=node.get("strokeWeight"),
            corner_radius=node.get("cornerRadius"),
            opacity=node.get("opacity"),
            rotation=node.get("rotation"),
            children=[
                FigmaChildRef(
                    id=_text(child.get("id")),
                    name=_text(child.get("name")),
                    type=_text(child.get("type")),
                )
                for child in _children(node)
            ],
            fill_hex=_solid_hex(node.get("fills")),
            stroke_hex=_solid_hex(node.get("strokes")),
            font_family=_style_value(node, "fontFamily"),
            font_size=_as_float(_style_value(node, "fontSize")),
            font_weight=_font_weight(node),
            line_height_px=_as_float(_style_value(node, "lineHeightPx")),
            letter_spacing_px=_as_float(_style_value(node, "letterSpacing")),
            layout_mode=node.get("layoutMode")
            if node.get("layoutMode") in AUTO_LAYOUT_MODES
            else None,
            item_spacing=_as_float(node.get("itemSpacing")),
            padding_left=_as_float(node.get("paddingLeft")),
            padding_right=_as_float(node.get("paddingRight")),
            padding_top=_as_float(node.get("paddingTop")),
            padding_bottom=_as_float(node.get("paddingBottom")),
            component_id=component_id if isinstance(component_id, str) else None,
            component_name=component_name(node, components),
            depth=match.depth,
            parent_name=_text(match.parent.get("name")) if match.parent else None,
            is_default_name=bool(DEFAULT_NAME_PATTERN.match(_text(node.get("name")))),
            locked=node.get("locked") is True,
            effect_count=len(node.get("effects"))
            if isinstance(node.get("effects"), list)
            else 0,
            annotation_count=len(annotations) if isinstance(annotations, list) else 0,
        )


def describe(selector: FigmaNodeSelector) -> str:
    """Renders a selector for an error message.

    Args:
        selector: Validated node selector.

    Returns:
        The supplied selector fields as ``key=value`` pairs.
    """
    supplied = selector.model_dump(exclude={"path", "file_key"}, exclude_none=True)
    return ", ".join(f"{field}={value!r}" for field, value in supplied.items())


class FigmaInspectDocumentInput(FigmaDocumentInput):
    """Input for ``figma.inspect_document``.

    Attributes:
        path: Filename of the ``.fig``/``.jam`` design document to grade.
        file_key: Which file to inspect. Same fallback as ``find_nodes``.
        limit: Maximum number of page entries returned alongside the counts.
    """

    file_key: str | None = None
    limit: int = Field(default=DEFAULT_NODE_LIMIT, ge=1, le=MAX_NODE_LIMIT)


class FigmaPageSummary(StrictModel):
    """One page (CANVAS node) of a file.

    Attributes:
        id: Page node id.
        name: Page name.
        child_count: Number of direct children on the page.
        node_count: Number of nodes in the page's subtree, including the page.
    """

    id: str
    name: str
    child_count: int
    node_count: int


class FigmaInspectDocumentOutput(StrictModel):
    """Bounded structural evidence about one design file.

    The counts are computed across the whole file and are never truncated; only
    the ``pages`` list is capped. A verifier asserting "this file has four
    pages" must not quietly become "four of the pages I chose to look at".

    Attributes:
        current_file_key: The document's current file key, or None.
        file_count: How many files the state document holds.
        file_key: Key of the file this output describes.
        name: Name of that file.
        page_count: Total pages in the file, never truncated.
        pages: Up to ``limit`` page summaries, in document order.
        node_count: Total nodes in the file, pages included. For a count of one
            *type* of node, use ``figma.find_nodes`` with ``node_type``: it
            answers the same question through a selector the editor can draw
            and a control shaped to the answer.
        truncated: Whether ``page_count`` exceeded ``limit``.
    """

    current_file_key: str | None
    file_count: int
    file_key: str | None
    name: str | None
    page_count: int
    pages: list[FigmaPageSummary]
    node_count: int
    truncated: bool


class InspectDocument(
    SourceCommand[FigmaInspectDocumentInput, FigmaInspectDocumentOutput]
):
    """Expose a design file's page inventory and node census."""

    name = "inspect_document"
    input_model = FigmaInspectDocumentInput
    output_model = FigmaInspectDocumentOutput

    def run(
        self, source_input: FigmaInspectDocumentInput, context: SourceContext
    ) -> FigmaInspectDocumentOutput:
        """Summarises one file's structure.

        Args:
            source_input: Validated inspection input.
            context: Source runtime context.

        Returns:
            Exact counts with a bounded page list.
        """
        state, figma_file = load_document(source_input, context)

        # One raw pass: this command reports counts and page metadata only, so
        # building a validated summary per node would be work nothing reads.
        node_count = 0
        pages: list[FigmaPageSummary] = []
        document = figma_file.get("document")
        for page in _children(document if isinstance(document, dict) else {}):
            page_nodes = 0
            for _ in _descend(page, None):
                page_nodes += 1
            node_count += page_nodes
            pages.append(
                FigmaPageSummary(
                    id=_text(page.get("id")),
                    name=_text(page.get("name")),
                    child_count=len(_children(page)),
                    node_count=page_nodes,
                )
            )
        return FigmaInspectDocumentOutput(
            current_file_key=state.get("current_file_key"),
            file_count=len(state.get("files") or []),
            file_key=figma_file.get("fileKey"),
            name=figma_file.get("name"),
            page_count=len(pages),
            pages=pages[: source_input.limit],
            node_count=node_count,
            truncated=len(pages) > source_input.limit,
        )


class FigmaExtractTextInput(FigmaDocumentInput):
    """Input for ``figma.extract_text``.

    Attributes:
        path: Filename of the ``.fig``/``.jam`` design document to grade.
        file_key: Which file to read from. Same fallback as ``find_nodes``.
        page: Name or id of a page to restrict extraction to.
        limit: Maximum number of per-node entries returned beside ``text``.
    """

    file_key: str | None = None
    page: str | None = None
    limit: int = Field(default=DEFAULT_NODE_LIMIT, ge=1, le=MAX_NODE_LIMIT)


class FigmaTextNode(StrictModel):
    """One TEXT node's content.

    Attributes:
        id: Node id.
        name: Node name.
        characters: The node's text content.
    """

    id: str
    name: str
    characters: str


class FigmaExtractTextOutput(StrictModel):
    """Every TEXT node's content, in document order.

    Attributes:
        text: All text joined by newlines, capped at the content limit.
        text_nodes: The nodes that contributed, never more than ``limit`` and
            never more than ``text`` actually contains, so the two cannot
            disagree about what was read. Bounded independently because node
            *names* also reach a rubric judge's prompt, and nothing about the
            text length bounds those.
        text_node_count: How many TEXT nodes contributed, never truncated.
        truncated: Whether either cap dropped anything.
    """

    text: str
    text_nodes: list[FigmaTextNode]
    text_node_count: int
    truncated: bool


class ExtractText(SourceCommand[FigmaExtractTextInput, FigmaExtractTextOutput]):
    """Read the design's text content."""

    name = "extract_text"
    input_model = FigmaExtractTextInput
    output_model = FigmaExtractTextOutput

    def run(
        self, source_input: FigmaExtractTextInput, context: SourceContext
    ) -> FigmaExtractTextOutput:
        """Collects TEXT node content from a file or one of its pages.

        Args:
            source_input: Validated extraction input.
            context: Source runtime context.

        Returns:
            The joined text and the nodes it came from, both capped together.
        """
        figma_file = load_file(source_input, context)

        # Raw walk: this command needs three fields off the TEXT nodes, so a
        # validated summary per node in the file would be work nothing reads.
        nodes: list[FigmaTextNode] = []
        document = figma_file.get("document")
        for page in _children(document if isinstance(document, dict) else {}):
            if source_input.page is not None and source_input.page not in (
                page.get("id"),
                page.get("name"),
            ):
                continue
            for node, _, _ in _descend(page, None):
                if _text(node.get("type")) != "TEXT":
                    continue
                characters = node.get("characters", node.get("text"))
                if characters is None:
                    continue
                nodes.append(
                    FigmaTextNode(
                        id=_text(node.get("id")),
                        name=_text(node.get("name")),
                        characters=characters,
                    )
                )

        joined = "\n".join(node.characters for node in nodes)
        capped = joined[: context.max_content_chars]
        if len(capped) == len(joined):
            return FigmaExtractTextOutput(
                text=capped,
                text_nodes=nodes[: source_input.limit],
                text_node_count=len(nodes),
                truncated=len(nodes) > source_input.limit,
            )
        # Keep `text_nodes` consistent with the text actually returned: a node
        # whose content was cut away must not still be listed as fully read.
        kept: list[FigmaTextNode] = []
        offset = 0
        for node in nodes:
            offset += len(node.characters)
            if offset > len(capped):
                break
            kept.append(node)
            offset += 1  # the newline joining this node to the next
        return FigmaExtractTextOutput(
            text=capped,
            text_nodes=kept[: source_input.limit],
            text_node_count=len(nodes),
            truncated=True,
        )


# --- the design-review summaries ---------------------------------------------
#
# find_nodes and read_node answer questions about one node an author can already
# name. A design review asks the other kind: how much of this file was never
# named, is the palette the token palette, does any text fall below 4.5:1, is
# there still an archive page. Those are aggregates over the whole tree, and the
# only way to express one before these commands was a rubric judge reading raw
# JSON -- non-deterministic, expensive, and unable to say a number out loud.
#
# Each command walks the tree exactly once and returns flat scalars, so every
# figure is reachable as `$.field` and a card can be built on it without a
# JSONPath that knows the document's shape. Counts are computed over the whole
# tree and are never truncated; only the samples beside them are capped, and
# `truncated` says when a cap bit.


class FigmaSummaryInput(FigmaDocumentInput):
    """Arguments shared by every ``figma.summarize_*`` command.

    Attributes:
        path: Filename of the ``.fig``/``.jam`` design document to grade.
        file_key: Which file to summarize. Same fallback as ``find_nodes``.
        page: Name or id of a single page to restrict the summary to.
        limit: Maximum entries in any one list the summary returns.
    """

    file_key: str | None = None
    page: str | None = None
    limit: int = Field(default=DEFAULT_NODE_LIMIT, ge=1, le=MAX_NODE_LIMIT)


class FigmaPageStructure(StrictModel):
    """One page's own share of the file's structural counts.

    Attributes:
        id: Page node id.
        name: Page name.
        node_count: Nodes in the page's subtree, the page itself included.
        top_level_frame_count: FRAME children sitting directly on the page.
        max_depth: Deepest node under the page, 0 being a direct child.
        default_named_count: Layers Figma named rather than a person.
        hidden_count: Layers explicitly marked invisible.
    """

    id: str
    name: str
    node_count: int
    top_level_frame_count: int
    max_depth: int
    default_named_count: int
    hidden_count: int


class FigmaStructureOutput(StrictModel):
    """The shape, hygiene and reuse of one design file.

    Attributes:
        node_count: Every node in scope, pages included -- the same number
            ``inspect_document`` and an empty ``find_nodes`` selector report.
        page_count: Pages in scope.
        max_depth: Deepest node, 0 being a direct child of a page.
        nodes_over_depth: Nodes deeper than :data:`DEEP_NESTING_DEPTH`.
        top_level_frame_count: FRAMEs sitting directly on a page.
        section_count: SECTION nodes.
        default_named_count: Layers still carrying a Figma-assigned name.
        default_named_ratio: That count over ``node_count``.
        clutter_named_count: Names carrying working-file residue.
        clutter_named_samples: Up to ``limit`` of those names.
        hidden_count: Nodes explicitly marked invisible.
        locked_count: Nodes marked locked.
        empty_frame_count: FRAMEs with no children and nothing painted.
        wrapper_frame_count: FRAMEs holding exactly one child, with no auto
            layout and nothing painted -- a container that does nothing.
        instance_count: INSTANCE nodes.
        component_count: COMPONENT and COMPONENT_SET nodes.
        library_instance_count: Instances whose ``componentId`` is not in this
            file's component map, so the component lives in another library.
        component_names: Up to ``limit`` component names, in document order.
        repeated_pattern_groups: Groups of three or more non-INSTANCE nodes
            sharing a structural signature -- copy-paste that should be a
            component.
        duplicate_component_groups: Components whose names differ only in case
            and punctuation.
        duplicate_screen_groups: Top-level frames sharing a signature.
        archive_page_count: Pages whose name says they are an archive.
        page_roles: Page name to the role its name claims. A repeated page name
            keeps the first page's role.
        pages: Up to ``limit`` per-page structural counts.
        largest_subtree_node_count: Nodes under the biggest single top-level
            frame, that frame included.
        annotation_layer_count: Layers named like a redline, plus TEXT painted
            in markup red.
        icon_size_buckets: ``WxH`` to how many icons are that size.
        icon_stroke_weights: Distinct stroke weights across those icons.
        loose_icon_count: Icon-shaped VECTORs that are not inside an instance.
        manual_positioning_ratio: One minus the share of FRAMEs using auto
            layout. 0.0 when the scope holds no frames at all.
        truncated: Whether any list above was capped.
    """

    node_count: int
    page_count: int
    max_depth: int
    nodes_over_depth: int
    top_level_frame_count: int
    section_count: int
    default_named_count: int
    default_named_ratio: float
    clutter_named_count: int
    clutter_named_samples: list[str]
    hidden_count: int
    locked_count: int
    empty_frame_count: int
    wrapper_frame_count: int
    instance_count: int
    component_count: int
    library_instance_count: int
    component_names: list[str]
    repeated_pattern_groups: int
    duplicate_component_groups: int
    duplicate_screen_groups: int
    archive_page_count: int
    page_roles: dict[str, str]
    pages: list[FigmaPageStructure]
    largest_subtree_node_count: int
    annotation_layer_count: int
    icon_size_buckets: dict[str, int]
    icon_stroke_weights: list[float]
    loose_icon_count: int
    manual_positioning_ratio: float
    truncated: bool


class SummarizeStructure(SourceCommand[FigmaSummaryInput, FigmaStructureOutput]):
    """Report a file's shape, naming hygiene and component reuse."""

    name = "summarize_structure"
    input_model = FigmaSummaryInput
    output_model = FigmaStructureOutput

    def run(
        self, source_input: FigmaSummaryInput, context: SourceContext
    ) -> FigmaStructureOutput:
        """Walks the design once and counts everything structural.

        Args:
            source_input: Validated summary input.
            context: Source runtime context.

        Returns:
            Exact counts with bounded samples.
        """
        figma_file = load_file(source_input, context)
        components = figma_file.get("components")
        components = components if isinstance(components, dict) else {}
        cap = _Cap(source_input.limit)

        node_count = 0
        max_depth = 0
        nodes_over_depth = 0
        section_count = 0
        default_named_count = 0
        clutter_names: list[str] = []
        hidden_count = 0
        locked_count = 0
        empty_frame_count = 0
        wrapper_frame_count = 0
        instance_count = 0
        library_instance_count = 0
        component_names: list[str] = []
        frame_count = 0
        auto_layout_count = 0
        annotation_layer_count = 0
        loose_icon_count = 0
        largest_subtree_node_count = 0
        icon_sizes: Counter[str] = Counter()
        icon_stroke_weights: set[float] = set()
        signatures: Counter[str] = Counter()
        component_name_keys: Counter[str] = Counter()
        screen_signatures: Counter[str] = Counter()
        top_level_frame_count = 0
        pages_seen: list[dict[str, Any]] = []
        page_stats: dict[int, dict[str, int]] = {}
        subtree_count = 0
        in_subtree = False

        for walked in walk_pages(figma_file, source_input.page):
            node = walked.node
            name = _text(node.get("name"))
            node_type = _text(node.get("type"))
            page_key = id(walked.page)
            if page_key not in page_stats:
                pages_seen.append(walked.page)
                page_stats[page_key] = {
                    "node_count": 0,
                    "top_level_frame_count": 0,
                    "max_depth": 0,
                    "default_named_count": 0,
                    "hidden_count": 0,
                }
            stats = page_stats[page_key]

            # Pre-order means a top-level frame's whole subtree arrives before
            # the next one starts, so the biggest screen can be measured
            # without a second walk or a per-node parent chain.
            if walked.depth <= 0:
                if in_subtree:
                    largest_subtree_node_count = max(
                        largest_subtree_node_count, subtree_count
                    )
                in_subtree = walked.depth == 0
                subtree_count = 1 if in_subtree else 0
            elif in_subtree:
                subtree_count += 1

            node_count += 1
            stats["node_count"] += 1
            max_depth = max(max_depth, walked.depth)
            stats["max_depth"] = max(stats["max_depth"], walked.depth)
            if walked.depth > DEEP_NESTING_DEPTH:
                nodes_over_depth += 1
            if DEFAULT_NAME_PATTERN.match(name):
                default_named_count += 1
                stats["default_named_count"] += 1
            if CLUTTER_NAME_PATTERN.search(name):
                clutter_names.append(name)
            if node.get("visible") is False:
                hidden_count += 1
                stats["hidden_count"] += 1
            if node.get("locked") is True:
                locked_count += 1
            if node_type == "SECTION":
                section_count += 1
            if node_type != "INSTANCE":
                signatures[_subtree_signature(node)] += 1

            if node_type == FRAME_TYPE:
                frame_count += 1
                children = _children(node)
                auto_layout = node.get("layoutMode") in AUTO_LAYOUT_MODES
                if auto_layout:
                    auto_layout_count += 1
                painted = _has_visible_paint(node.get("fills")) or _has_visible_paint(
                    node.get("strokes")
                )
                if not children and not painted:
                    empty_frame_count += 1
                if len(children) == 1 and not auto_layout and not painted:
                    wrapper_frame_count += 1
                if walked.depth == 0:
                    top_level_frame_count += 1
                    stats["top_level_frame_count"] += 1
                    screen_signatures[_subtree_signature(node)] += 1
            elif node_type == "INSTANCE":
                instance_count += 1
                identifier = node.get("componentId")
                if not isinstance(identifier, str) or identifier not in components:
                    library_instance_count += 1
            elif node_type in COMPONENT_TYPES:
                component_names.append(name)
                component_name_keys[_normalise_name(name)] += 1

            if ANNOTATION_NAME_PATTERN.search(name) or (
                node_type == "TEXT" and _is_annotation_red(node.get("fills"))
            ):
                annotation_layer_count += 1

            if node_type in ICON_TYPES:
                bounds = _bounds(node)
                small = bounds is not None and max(bounds[2], bounds[3]) <= (
                    ICON_MAX_DIMENSION
                )
                if small or ICON_NAME_PATTERN.search(name):
                    if bounds is not None:
                        icon_sizes[f"{round(bounds[2])}x{round(bounds[3])}"] += 1
                    weight = _as_float(node.get("strokeWeight"))
                    if weight is not None:
                        icon_stroke_weights.add(weight)
                    if node_type == "VECTOR" and not walked.inside_instance:
                        loose_icon_count += 1

        if in_subtree:
            largest_subtree_node_count = max(largest_subtree_node_count, subtree_count)

        pages = [
            FigmaPageStructure(
                id=_text(page.get("id")),
                name=_text(page.get("name")),
                **page_stats[id(page)],
            )
            for page in pages_seen
        ]
        return FigmaStructureOutput(
            node_count=node_count,
            page_count=len(pages_seen),
            max_depth=max_depth,
            nodes_over_depth=nodes_over_depth,
            top_level_frame_count=top_level_frame_count,
            section_count=section_count,
            default_named_count=default_named_count,
            default_named_ratio=default_named_count / node_count if node_count else 0.0,
            clutter_named_count=len(clutter_names),
            clutter_named_samples=cap.take(clutter_names),
            hidden_count=hidden_count,
            locked_count=locked_count,
            empty_frame_count=empty_frame_count,
            wrapper_frame_count=wrapper_frame_count,
            instance_count=instance_count,
            component_count=len(component_names),
            library_instance_count=library_instance_count,
            component_names=cap.take(component_names),
            repeated_pattern_groups=sum(
                1
                for count in signatures.values()
                if count >= REPEATED_PATTERN_MIN_MEMBERS
            ),
            duplicate_component_groups=sum(
                1 for count in component_name_keys.values() if count > 1
            ),
            duplicate_screen_groups=sum(
                1 for count in screen_signatures.values() if count > 1
            ),
            archive_page_count=sum(
                1
                for page in pages_seen
                if ARCHIVE_NAME_PATTERN.search(_text(page.get("name")))
            ),
            page_roles=cap.take_map(_page_role_pairs(pages_seen)),
            pages=cap.take(pages),
            largest_subtree_node_count=largest_subtree_node_count,
            annotation_layer_count=annotation_layer_count,
            icon_size_buckets=cap.take_map(
                sorted(icon_sizes.items(), key=lambda item: (-item[1], item[0]))
            ),
            icon_stroke_weights=cap.take(sorted(icon_stroke_weights)),
            loose_icon_count=loose_icon_count,
            manual_positioning_ratio=(
                1.0 - auto_layout_count / frame_count if frame_count else 0.0
            ),
            truncated=cap.hit,
        )


def _page_role_pairs(pages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Page name to role, keeping the first page when a name repeats.

    Args:
        pages: Page mappings in document order.

    Returns:
        Ordered ``(name, role)`` pairs with no duplicate name.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for page in pages:
        name = _text(page.get("name"))
        if name in seen:
            continue
        seen.add(name)
        pairs.append((name, _page_role(page)))
    return pairs


class FigmaLayoutInput(FigmaSummaryInput):
    """Input for ``figma.summarize_layout``.

    Attributes:
        spacing_scale: The spacing values this design system allows. Defaults
            to :data:`DEFAULT_SPACING_SCALE`, multiples of four up to 200.
    """

    spacing_scale: list[float] | None = Field(default=None, min_length=1)


class FigmaLayoutOutput(StrictModel):
    """How a design is laid out, and where the layout breaks.

    ``overflow_count`` and ``off_canvas_count`` are report metrics, not
    verdicts: a design can legitimately let a shadow or a decorative shape
    extend past its frame. They are here so a card can say "nothing spills"
    when the task asked for that, not so every spill is a failure.

    Attributes:
        frame_count: FRAME nodes in scope. INSTANCE and COMPONENT are excluded
            deliberately: an instance's ``layoutMode`` belongs to its main
            component, so counting it would report auto-layout adoption this
            file never made.
        auto_layout_count: Those frames using auto layout.
        auto_layout_ratio: The second over the first, 0.0 with no frames.
        auto_layout_nesting_max: Deepest chain of nested auto-layout frames.
        device_coverage: ``desktop``/``tablet``/``mobile``/``other`` to how
            many top-level frames fall in each. Always all four keys.
        desktop_frame_count: The ``desktop`` entry of ``device_coverage``, flat.
        tablet_frame_count: The ``tablet`` entry, flat.
        mobile_frame_count: The ``mobile`` entry, flat.
        other_frame_count: The ``other`` entry, flat.
        device_bucket_count: How many of ``desktop``/``tablet``/``mobile`` hold
            at least one top-level frame, so "ships desktop and mobile" is one
            comparison. ``other`` is not a device and never counts.
        breakpoint_widths: Distinct top-level frame widths, ascending.
        container_widths: Page name to its most common top-level frame width.
            Pages with no top-level frame are absent.
        overflow_count: Children whose box extends more than
            :data:`OVERFLOW_TOLERANCE` past a parent that does not clip.
        off_canvas_count: Children whose box lies entirely outside its parent.
        small_target_count: FRAME/INSTANCE/RECTANGLE nodes named like something
            tappable whose width or height is under :data:`MIN_TAP_TARGET`.
            TEXT is excluded: its box is the glyph run, not the target.
        spacing_values: Distinct item-spacing and padding values, ascending.
        off_scale_spacing_count: How many of those are off the spacing scale.
        off_scale_spacing_values: Up to ``limit`` of them.
        truncated: Whether any list above was capped.
    """

    frame_count: int
    auto_layout_count: int
    auto_layout_ratio: float
    auto_layout_nesting_max: int
    device_coverage: dict[str, int]
    desktop_frame_count: int
    tablet_frame_count: int
    mobile_frame_count: int
    other_frame_count: int
    device_bucket_count: int
    breakpoint_widths: list[int]
    container_widths: dict[str, int]
    overflow_count: int
    off_canvas_count: int
    small_target_count: int
    spacing_values: list[float]
    off_scale_spacing_count: int
    off_scale_spacing_values: list[float]
    truncated: bool


class SummarizeLayout(SourceCommand[FigmaLayoutInput, FigmaLayoutOutput]):
    """Report auto-layout adoption, breakpoints and layout defects."""

    name = "summarize_layout"
    input_model = FigmaLayoutInput
    output_model = FigmaLayoutOutput

    def run(
        self, source_input: FigmaLayoutInput, context: SourceContext
    ) -> FigmaLayoutOutput:
        """Walks the design once and measures its layout.

        Args:
            source_input: Validated layout input.
            context: Source runtime context.

        Returns:
            Exact counts with bounded samples.
        """
        figma_file = load_file(source_input, context)
        cap = _Cap(source_input.limit)
        scale = set(source_input.spacing_scale or DEFAULT_SPACING_SCALE)

        frame_count = 0
        auto_layout_count = 0
        auto_layout_nesting_max = 0
        overflow_count = 0
        off_canvas_count = 0
        small_target_count = 0
        device_coverage = dict.fromkeys((*DEVICE_BUCKETS, "other"), 0)
        breakpoint_widths: set[int] = set()
        spacing_values: set[float] = set()
        page_widths: dict[int, Counter[int]] = {}
        pages_seen: list[dict[str, Any]] = []

        for walked in walk_pages(figma_file, source_input.page):
            node = walked.node
            name = _text(node.get("name"))
            node_type = _text(node.get("type"))
            bounds = _bounds(node)

            if node_type == FRAME_TYPE:
                frame_count += 1
                if node.get("layoutMode") in AUTO_LAYOUT_MODES:
                    auto_layout_count += 1
                auto_layout_nesting_max = max(
                    auto_layout_nesting_max, walked.auto_layout_depth
                )
                if walked.depth == 0:
                    width = bounds[2] if bounds else None
                    device_coverage[_device_bucket(node, width)] += 1
                    if width is not None:
                        breakpoint_widths.add(round(width))
                        page_key = id(walked.page)
                        if page_key not in page_widths:
                            pages_seen.append(walked.page)
                            page_widths[page_key] = Counter()
                        page_widths[page_key][round(width)] += 1

            for key in (
                "itemSpacing",
                "paddingLeft",
                "paddingRight",
                "paddingTop",
                "paddingBottom",
            ):
                value = _as_float(node.get(key))
                if value is not None:
                    spacing_values.add(value)

            if (
                node_type in (FRAME_TYPE, "INSTANCE", "RECTANGLE")
                and bounds is not None
                and INTERACTIVE_NAME_PATTERN.search(name)
                and min(bounds[2], bounds[3]) < MIN_TAP_TARGET
            ):
                small_target_count += 1

            parent_bounds = _bounds(walked.parent) if walked.parent else None
            if bounds is not None and parent_bounds is not None:
                if _is_off_canvas(bounds, parent_bounds):
                    off_canvas_count += 1
                if walked.parent is not None and not walked.parent.get("clipsContent"):
                    if _overflows(bounds, parent_bounds):
                        overflow_count += 1

        off_scale = sorted(value for value in spacing_values if value not in scale)
        return FigmaLayoutOutput(
            frame_count=frame_count,
            auto_layout_count=auto_layout_count,
            auto_layout_ratio=(auto_layout_count / frame_count if frame_count else 0.0),
            auto_layout_nesting_max=auto_layout_nesting_max,
            device_coverage=device_coverage,
            desktop_frame_count=device_coverage["desktop"],
            tablet_frame_count=device_coverage["tablet"],
            mobile_frame_count=device_coverage["mobile"],
            other_frame_count=device_coverage["other"],
            device_bucket_count=sum(
                1 for bucket in DEVICE_BUCKETS if device_coverage[bucket]
            ),
            breakpoint_widths=cap.take(sorted(breakpoint_widths)),
            container_widths=cap.take_map(
                (
                    _text(page.get("name")),
                    sorted(
                        page_widths[id(page)].items(),
                        key=lambda item: (-item[1], -item[0]),
                    )[0][0],
                )
                for page in pages_seen
            ),
            overflow_count=overflow_count,
            off_canvas_count=off_canvas_count,
            small_target_count=small_target_count,
            spacing_values=cap.take(sorted(spacing_values)),
            off_scale_spacing_count=len(off_scale),
            off_scale_spacing_values=cap.take(off_scale),
            truncated=cap.hit,
        )


def _overflows(
    bounds: tuple[float, float, float, float],
    parent: tuple[float, float, float, float],
) -> bool:
    """Whether a child's box extends past its parent's by more than a pixel."""
    return (
        bounds[0] < parent[0] - OVERFLOW_TOLERANCE
        or bounds[1] < parent[1] - OVERFLOW_TOLERANCE
        or bounds[0] + bounds[2] > parent[0] + parent[2] + OVERFLOW_TOLERANCE
        or bounds[1] + bounds[3] > parent[1] + parent[3] + OVERFLOW_TOLERANCE
    )


def _is_off_canvas(
    bounds: tuple[float, float, float, float],
    parent: tuple[float, float, float, float],
) -> bool:
    """Whether a child's box lies entirely outside its parent's."""
    return (
        bounds[0] + bounds[2] <= parent[0]
        or bounds[0] >= parent[0] + parent[2]
        or bounds[1] + bounds[3] <= parent[1]
        or bounds[1] >= parent[1] + parent[3]
    )


class FigmaStylesInput(FigmaSummaryInput):
    """Input for ``figma.summarize_styles``.

    Attributes:
        palette: The colours this design is allowed to use, as ``#RRGGBB``.
        reference_file_key: Another file in the same state document whose
            colours are the allowed palette. Mutually exclusive with
            ``palette``: two sources of truth for one question is how a design
            gets graded against the wrong one.
        radius_scale: The corner radii this system allows. Defaults to
            :data:`DEFAULT_RADIUS_SCALE`.
        typography_scale: The font sizes this system allows. No default: unlike
            spacing and radius there is no near-universal type scale, so with
            no scale given ``off_scale_font_size_count`` is None rather than a
            zero that reads as "everything is on scale".
    """

    palette: list[str] | None = Field(default=None, min_length=1)
    reference_file_key: str | None = None
    radius_scale: list[float] | None = Field(default=None, min_length=1)
    typography_scale: list[float] | None = Field(default=None, min_length=1)

    @field_validator("palette")
    @classmethod
    def validate_palette(cls, value: list[str] | None) -> list[str] | None:
        """Normalizes an authored palette to upper-case ``#RRGGBB``.

        Args:
            value: Authored colours, or None.

        Returns:
            The colours upper-cased.

        Raises:
            ValueError: If any entry is not a six-digit hex colour. A typo
                would otherwise report every fill in the design as rogue.
        """
        if value is None:
            return value
        for colour in value:
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
                raise ValueError(f"palette entries must be #RRGGBB, got {colour!r}")
        return [colour.upper() for colour in value]

    @model_validator(mode="after")
    def validate_one_reference(self) -> "FigmaStylesInput":
        """Refuses two palettes at once.

        Returns:
            The validated input.

        Raises:
            ValueError: If both ``palette`` and ``reference_file_key`` are set.
        """
        if self.palette is not None and self.reference_file_key is not None:
            raise ValueError(
                "figma.summarize_styles takes palette or reference_file_key, "
                "not both: they are two answers to one question"
            )
        return self


class FigmaStylesOutput(StrictModel):
    """The colours, type and elevation a design actually uses.

    Hidden nodes are excluded throughout -- a layer nobody can see is not part
    of the design, and neither is anything under it. Every palette is
    frequency-ordered, ties broken on the hex itself so the order is stable
    across runs.

    Attributes:
        fill_palette: Up to ``limit`` distinct fill colours, commonest first.
        fill_palette_size: How many distinct fill colours there are.
        stroke_palette: The same for strokes.
        stroke_palette_size: How many distinct stroke colours there are.
        text_palette: The same for TEXT fills only.
        text_palette_size: How many distinct text colours there are.
        gradient_fill_count: Gradient paints.
        image_fill_count: Image paints.
        style_ref_fill_count: Nodes whose fills are a shared style reference
            rather than paints, so no colour is readable from them.
        palette_reference: ``argument``, ``file:<key>``, or None when no
            palette was supplied.
        rogue_fills: Up to ``limit`` colours outside the reference, or None
            when there is no reference.
        rogue_fill_count: How many there are, or None. Without a stated palette
            there is no such thing as a rogue colour, and reporting 0 would let
            a card claim the design passed a check nobody ran.
        font_families: Up to ``limit`` families, commonest first.
        font_family_count: How many distinct families there are.
        font_sizes: Distinct sizes, ascending.
        font_size_count: How many there are.
        font_weights: Distinct numeric weights, ascending.
        min_font_size: Smallest size, or None with no text.
        tiny_text_count: TEXT nodes below :data:`TINY_FONT_SIZE`.
        line_heights_px: Distinct line heights, ascending.
        letter_spacings_px: Distinct letter spacings, ascending.
        off_scale_font_size_count: Sizes outside ``typography_scale``, or None
            when no scale was given.
        radius_values: Distinct corner radii, ascending.
        off_scale_radius_count: How many are off ``radius_scale``.
        shadow_signature_count: Distinct shadow definitions.
        shadow_signatures: Up to ``limit`` of them, as
            ``TYPE|radius|offsetX|offsetY|#RRGGBB``.
        min_text_contrast: Lowest WCAG contrast ratio between a TEXT node and
            the nearest opaque ancestor behind it, or None when no text has
            both a readable colour and such an ancestor.
        low_contrast_text_count: TEXT nodes below :data:`MIN_TEXT_CONTRAST`.
        truncated: Whether any list above was capped.
    """

    fill_palette: list[str]
    fill_palette_size: int
    stroke_palette: list[str]
    stroke_palette_size: int
    text_palette: list[str]
    text_palette_size: int
    gradient_fill_count: int
    image_fill_count: int
    style_ref_fill_count: int
    palette_reference: str | None
    rogue_fills: list[str] | None
    rogue_fill_count: int | None
    font_families: list[str]
    font_family_count: int
    font_sizes: list[float]
    font_size_count: int
    font_weights: list[int]
    min_font_size: float | None
    tiny_text_count: int
    line_heights_px: list[float]
    letter_spacings_px: list[float]
    off_scale_font_size_count: int | None
    radius_values: list[float]
    off_scale_radius_count: int
    shadow_signature_count: int
    shadow_signatures: list[str]
    min_text_contrast: float | None
    low_contrast_text_count: int
    truncated: bool


def _by_frequency(counter: "Counter[str]") -> list[str]:
    """Orders a colour or family census commonest first, ties on the value."""
    return [
        value
        for value, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _file_palette(figma_file: dict[str, Any]) -> set[str]:
    """Every visible fill and stroke colour in a file, hidden layers excluded."""
    palette: set[str] = set()
    for walked in walk_pages(figma_file):
        if walked.hidden:
            continue
        for key in ("fills", "strokes"):
            for paint in _paints(walked.node.get(key)):
                if paint.get("type") != "SOLID" or paint.get("visible") is False:
                    continue
                colour = _hex(paint.get("color"))
                if colour is not None:
                    palette.add(colour)
    return palette


class SummarizeStyles(SourceCommand[FigmaStylesInput, FigmaStylesOutput]):
    """Report a design's colour, type, radius and elevation discipline."""

    name = "summarize_styles"
    input_model = FigmaStylesInput
    output_model = FigmaStylesOutput

    def run(
        self, source_input: FigmaStylesInput, context: SourceContext
    ) -> FigmaStylesOutput:
        """Walks the design once and censuses everything visual.

        Args:
            source_input: Validated styles input.
            context: Source runtime context.

        Returns:
            Exact counts with bounded samples.
        """
        state, figma_file = load_document(source_input, context)
        cap = _Cap(source_input.limit)
        radius_scale = set(source_input.radius_scale or DEFAULT_RADIUS_SCALE)
        typography_scale = (
            set(source_input.typography_scale)
            if source_input.typography_scale is not None
            else None
        )

        fills: Counter[str] = Counter()
        strokes: Counter[str] = Counter()
        texts: Counter[str] = Counter()
        families: Counter[str] = Counter()
        font_sizes: set[float] = set()
        font_weights: set[int] = set()
        line_heights: set[float] = set()
        letter_spacings: set[float] = set()
        radii: set[float] = set()
        shadows: set[str] = set()
        gradient_fill_count = 0
        image_fill_count = 0
        style_ref_fill_count = 0
        tiny_text_count = 0
        low_contrast_text_count = 0
        min_text_contrast: float | None = None

        for walked in walk_pages(figma_file, source_input.page):
            if walked.hidden:
                continue
            node = walked.node
            node_type = _text(node.get("type"))

            if _is_style_reference(node.get("fills")):
                style_ref_fill_count += 1
            for paint in _paints(node.get("fills")):
                if paint.get("visible") is False:
                    continue
                paint_type = paint.get("type")
                if paint_type == "SOLID":
                    colour = _hex(paint.get("color"))
                    if colour is not None:
                        fills[colour] += 1
                        if node_type == "TEXT":
                            texts[colour] += 1
                elif isinstance(paint_type, str) and paint_type.startswith("GRADIENT_"):
                    gradient_fill_count += 1
                elif paint_type == "IMAGE":
                    image_fill_count += 1
            for paint in _paints(node.get("strokes")):
                if paint.get("type") != "SOLID" or paint.get("visible") is False:
                    continue
                colour = _hex(paint.get("color"))
                if colour is not None:
                    strokes[colour] += 1

            radius = _as_float(node.get("cornerRadius"))
            if radius is not None:
                radii.add(radius)

            effects = node.get("effects")
            if isinstance(effects, list):
                for effect in effects:
                    signature = _shadow_signature(effect)
                    if signature is not None:
                        shadows.add(signature)

            if node_type != "TEXT":
                continue
            family = _style_value(node, "fontFamily")
            if isinstance(family, str) and family:
                families[family] += 1
            size = _as_float(_style_value(node, "fontSize"))
            if size is not None:
                font_sizes.add(size)
                if size < TINY_FONT_SIZE:
                    tiny_text_count += 1
            weight = _font_weight(node)
            if weight is not None:
                font_weights.add(weight)
            line_height = _as_float(_style_value(node, "lineHeightPx"))
            if line_height is not None:
                line_heights.add(line_height)
            letter_spacing = _as_float(_style_value(node, "letterSpacing"))
            if letter_spacing is not None:
                letter_spacings.add(letter_spacing)

            foreground = _solid_hex(node.get("fills"))
            if foreground is None or walked.background is None:
                continue
            ratio = _contrast_ratio(foreground, walked.background)
            min_text_contrast = (
                ratio if min_text_contrast is None else min(min_text_contrast, ratio)
            )
            if ratio < MIN_TEXT_CONTRAST:
                low_contrast_text_count += 1

        reference: set[str] | None = None
        palette_reference: str | None = None
        if source_input.palette is not None:
            reference = set(source_input.palette)
            palette_reference = "argument"
        elif source_input.reference_file_key is not None:
            reference = _file_palette(
                select_file(state, source_input.reference_file_key)
            )
            palette_reference = f"file:{source_input.reference_file_key}"

        rogue: list[str] | None = None
        if reference is not None:
            used = set(fills) | set(strokes) | set(texts)
            rogue = sorted(used - reference)

        sizes = sorted(font_sizes)
        return FigmaStylesOutput(
            fill_palette=cap.take(_by_frequency(fills)),
            fill_palette_size=len(fills),
            stroke_palette=cap.take(_by_frequency(strokes)),
            stroke_palette_size=len(strokes),
            text_palette=cap.take(_by_frequency(texts)),
            text_palette_size=len(texts),
            gradient_fill_count=gradient_fill_count,
            image_fill_count=image_fill_count,
            style_ref_fill_count=style_ref_fill_count,
            palette_reference=palette_reference,
            rogue_fills=cap.take(rogue) if rogue is not None else None,
            rogue_fill_count=len(rogue) if rogue is not None else None,
            font_families=cap.take(_by_frequency(families)),
            font_family_count=len(families),
            font_sizes=cap.take(sizes),
            font_size_count=len(sizes),
            font_weights=cap.take(sorted(font_weights)),
            min_font_size=sizes[0] if sizes else None,
            tiny_text_count=tiny_text_count,
            line_heights_px=cap.take(sorted(line_heights)),
            letter_spacings_px=cap.take(sorted(letter_spacings)),
            off_scale_font_size_count=(
                sum(1 for size in sizes if size not in typography_scale)
                if typography_scale is not None
                else None
            ),
            radius_values=cap.take(sorted(radii)),
            off_scale_radius_count=sum(
                1 for radius in radii if radius not in radius_scale
            ),
            shadow_signature_count=len(shadows),
            shadow_signatures=cap.take(sorted(shadows)),
            min_text_contrast=min_text_contrast,
            low_contrast_text_count=low_contrast_text_count,
            truncated=cap.hit,
        )


def _shadow_signature(effect: Any) -> str | None:
    """One shadow effect rendered as a comparable string.

    Args:
        effect: Raw effect mapping.

    Returns:
        ``TYPE|radius|offsetX|offsetY|#RRGGBB``, or None when the effect is not
        a visible shadow. Two frames carrying the same string carry the same
        elevation, which is what "the elevation scale has three steps" means.
    """
    if not isinstance(effect, dict) or effect.get("visible") is False:
        return None
    effect_type = effect.get("type")
    if not isinstance(effect_type, str) or "SHADOW" not in effect_type:
        return None
    offset = effect.get("offset")
    offset = offset if isinstance(offset, dict) else {}
    colour = _hex(effect.get("color")) or ""
    return (
        f"{effect_type}|{_number(effect.get('radius'))}"
        f"|{_number(offset.get('x'))}|{_number(offset.get('y'))}|{colour}"
    )


class FigmaContentInput(FigmaSummaryInput):
    """Input for ``figma.summarize_content``.

    Attributes:
        placeholder_terms: Extra strings that count as placeholder copy,
            matched against the whole stripped label rather than as
            substrings. Added to :data:`PLACEHOLDER_PATTERN`, never replacing
            it.
        state_terms: Replaces :data:`STATE_TERMS` for this call, for a design
            system whose states are not the usual ones.
    """

    placeholder_terms: list[str] | None = Field(default=None, min_length=1)
    state_terms: list[str] | None = Field(default=None, min_length=1)


class FigmaLabelVariantGroup(StrictModel):
    """Distinct labels that mean the same thing.

    Attributes:
        key: The normalised form they share.
        labels: The raw labels, in document order.
    """

    key: str
    labels: list[str]


class FigmaContentOutput(StrictModel):
    """The copy in a design, and where it contradicts itself.

    Attributes:
        text_node_count: TEXT nodes in scope, hidden ones included -- a
            forgotten TODO is a defect whether or not the layer is switched on.
        placeholder_text_count: TEXT nodes whose content was never written.
        placeholder_samples: Up to ``limit`` of that content.
        label_variant_groups: Groups of two or more distinct labels sharing a
            normalised key -- the same button written two ways.
        label_variant_samples: Up to ``limit`` of those groups.
        state_coverage: Each state term to how many frames and components carry
            it in their name. Always carries every term, including the zeros,
            so a card can assert on a state that is missing.
        states_present: The terms whose count is above zero, in term order.
        annotation_layer_count: Layers named like a redline, plus TEXT painted
            in markup red -- the same rule ``summarize_structure`` uses.
        truncated: Whether any list above was capped.
    """

    text_node_count: int
    placeholder_text_count: int
    placeholder_samples: list[str]
    label_variant_groups: int
    label_variant_samples: list[FigmaLabelVariantGroup]
    state_coverage: dict[str, int]
    states_present: list[str]
    annotation_layer_count: int
    truncated: bool


class SummarizeContent(SourceCommand[FigmaContentInput, FigmaContentOutput]):
    """Report placeholder copy, inconsistent labels and state coverage."""

    name = "summarize_content"
    input_model = FigmaContentInput
    output_model = FigmaContentOutput

    def run(
        self, source_input: FigmaContentInput, context: SourceContext
    ) -> FigmaContentOutput:
        """Walks the design once and reads its copy.

        Args:
            source_input: Validated content input.
            context: Source runtime context.

        Returns:
            Exact counts with bounded samples.
        """
        figma_file = load_file(source_input, context)
        cap = _Cap(source_input.limit)
        extra_placeholders = {
            term.strip().casefold() for term in (source_input.placeholder_terms or ())
        }
        state_terms = tuple(source_input.state_terms or STATE_TERMS)
        state_patterns = [(term, _term_pattern((term,))) for term in state_terms]
        state_coverage = dict.fromkeys(state_terms, 0)

        text_node_count = 0
        placeholder_samples: list[str] = []
        annotation_layer_count = 0
        labels: dict[str, list[str]] = {}

        for walked in walk_pages(figma_file, source_input.page):
            node = walked.node
            name = _text(node.get("name"))
            node_type = _text(node.get("type"))

            if ANNOTATION_NAME_PATTERN.search(name) or (
                node_type == "TEXT" and _is_annotation_red(node.get("fills"))
            ):
                annotation_layer_count += 1

            if node_type == FRAME_TYPE or node_type in COMPONENT_TYPES:
                for term, pattern in state_patterns:
                    if pattern.search(name):
                        state_coverage[term] += 1

            if node_type != "TEXT":
                continue
            text_node_count += 1
            characters = node.get("characters", node.get("text"))
            if not isinstance(characters, str):
                continue
            stripped = characters.strip()
            if not stripped:
                continue
            if (
                PLACEHOLDER_PATTERN.search(stripped)
                or stripped.casefold() in extra_placeholders
            ):
                placeholder_samples.append(stripped)
            key = _label_key(stripped)
            if key:
                variants = labels.setdefault(key, [])
                if stripped not in variants:
                    variants.append(stripped)

        groups = [
            FigmaLabelVariantGroup(key=key, labels=variants)
            for key, variants in labels.items()
            if len(variants) > 1
        ]
        return FigmaContentOutput(
            text_node_count=text_node_count,
            placeholder_text_count=len(placeholder_samples),
            placeholder_samples=cap.take(placeholder_samples),
            label_variant_groups=len(groups),
            label_variant_samples=cap.take(groups),
            state_coverage=state_coverage,
            states_present=[term for term in state_terms if state_coverage[term] > 0],
            annotation_layer_count=annotation_layer_count,
            truncated=cap.hit,
        )


class FigmaScreensInput(FigmaSummaryInput):
    """Input for ``figma.summarize_screens``.

    Attributes:
        max_text_chars: Most characters of copy carried for any one screen.
            Bounded per screen and not only per run, because the question this
            command answers is what each screen says: one wall-of-text screen
            must not spend the whole budget and leave the rest speechless.
    """

    max_text_chars: int = Field(default=1000, ge=100, le=20000)


class FigmaScreen(StrictModel):
    """One screen, and the copy a judge would read off it.

    Attributes:
        id: Screen node id.
        name: Screen name.
        page_name: The CANVAS the screen sits on.
        device: Which device the screen's name and width claim -- the same
            buckets ``summarize_layout`` counts.
        width: Screen width, or None when the geometry is malformed.
        height: Screen height, or None when the geometry is malformed.
        hidden: Whether the screen or an ancestor is explicitly invisible.
        child_count: Direct children, never truncated.
        child_names: Up to ``limit`` of those children as ``TYPE: name``.
        text_node_count: TEXT nodes in the screen's subtree, hidden ones
            included and never truncated -- the same rule
            ``summarize_content`` counts by.
        text: That copy in document order joined by newlines, cut at
            ``max_text_chars`` and again at whatever is left of the run's
            content budget.
        text_truncated: Whether either cut this screen's copy.
    """

    id: str
    name: str
    page_name: str
    device: str
    width: float | None
    height: float | None
    hidden: bool
    child_count: int
    child_names: list[str]
    text_node_count: int
    text: str
    text_truncated: bool


class FigmaScreensOutput(StrictModel):
    """The screens a design ships, and what each one says.

    Attributes:
        page_count: Pages in scope.
        screen_count: Screens in scope, never truncated.
        text_node_count: TEXT nodes in scope, on a screen or not, never
            truncated -- the same number ``summarize_content`` reports.
        screens: Up to ``limit`` screens, in document order.
        omitted_screen_count: Screens the ``limit`` dropped from that list.
        device_bucket_count: How many of desktop/tablet/mobile hold at least
            one screen -- the same definition ``summarize_layout`` uses.
        truncated: Whether any cap bit: the screen list, a ``child_names``
            list, one screen's copy, or the run's total content budget.
    """

    page_count: int
    screen_count: int
    text_node_count: int
    screens: list[FigmaScreen]
    omitted_screen_count: int
    device_bucket_count: int
    truncated: bool


class SummarizeScreens(SourceCommand[FigmaScreensInput, FigmaScreensOutput]):
    """Report every screen in a design and the copy it carries."""

    name = "summarize_screens"
    input_model = FigmaScreensInput
    output_model = FigmaScreensOutput

    def run(
        self, source_input: FigmaScreensInput, context: SourceContext
    ) -> FigmaScreensOutput:
        """Walks the design once and reads it one screen at a time.

        A screen is a FRAME sitting on a page, or a FRAME inside a SECTION: a
        section groups screens, it is not one. Nothing else at the top level
        is a screen -- a COMPONENT is a part, not a flow -- so a FigJam board
        reports no screens rather than a page of sticky notes.

        ``walk_pages`` is depth-first in document order, so a screen's whole
        subtree arrives before the next screen begins. The text under each is
        therefore attributable while descending, with no second walk and no
        parent chain: a node belongs to the last screen yielded until the walk
        returns to that screen's depth or leaves its page.

        The run's total content budget is spent in document order. The first
        screens carry their copy whole, the screen that exhausts the budget
        carries what is left of it, and the rest carry an empty string; every
        screen whose copy did not fit reports ``text_truncated``.

        Args:
            source_input: Validated screens input.
            context: Source runtime context.

        Returns:
            Exact counts with bounded per-screen evidence.
        """
        figma_file = load_file(source_input, context)
        cap = _Cap(source_input.limit)

        pages_seen: set[int] = set()
        text_node_count = 0
        # Each screen keeps its subtree's TEXT content in document order. None
        # marks a TEXT node carrying nothing readable: it counts, but it
        # contributes no line, so a count and its evidence stay honest.
        found: list[tuple[WalkedNode, list[str | None]]] = []
        current: list[str | None] | None = None
        current_page: dict[str, Any] | None = None
        current_depth = 0

        for walked in walk_pages(figma_file, source_input.page):
            node = walked.node
            node_type = _text(node.get("type"))
            pages_seen.add(id(walked.page))

            if current is not None and (
                walked.page is not current_page or walked.depth <= current_depth
            ):
                current = None
            if node_type == FRAME_TYPE and (
                walked.depth == 0
                or (
                    walked.depth == 1
                    and _text((walked.parent or {}).get("type")) == "SECTION"
                )
            ):
                current = []
                current_page = walked.page
                current_depth = walked.depth
                found.append((walked, current))
            if node_type == "TEXT":
                text_node_count += 1
                if current is not None:
                    characters = node.get("characters", node.get("text"))
                    current.append(characters if isinstance(characters, str) else None)

        buckets: set[str] = set()
        for walked, _ in found:
            bounds = _bounds(walked.node)
            buckets.add(_device_bucket(walked.node, bounds[2] if bounds else None))

        screens: list[FigmaScreen] = []
        remaining = context.max_content_chars
        for walked, parts in cap.take(found):
            node = walked.node
            bounds = _bounds(node)
            width = bounds[2] if bounds else None
            children = _children(node)
            joined = "\n".join(part for part in parts if part is not None)
            text = joined[: min(source_input.max_text_chars, remaining)]
            remaining -= len(text)
            screens.append(
                FigmaScreen(
                    id=_text(node.get("id")),
                    name=_text(node.get("name")),
                    page_name=_text(walked.page.get("name")),
                    device=_device_bucket(node, width),
                    width=width,
                    height=bounds[3] if bounds else None,
                    hidden=walked.hidden,
                    child_count=len(children),
                    child_names=cap.take(
                        f"{_text(child.get('type'))}: {_text(child.get('name'))}"
                        for child in children
                    ),
                    text_node_count=len(parts),
                    text=text,
                    text_truncated=len(text) < len(joined),
                )
            )

        return FigmaScreensOutput(
            page_count=len(pages_seen),
            screen_count=len(found),
            text_node_count=text_node_count,
            screens=screens,
            omitted_screen_count=len(found) - len(screens),
            device_bucket_count=sum(
                1 for bucket in DEVICE_BUCKETS if bucket in buckets
            ),
            truncated=cap.hit or any(screen.text_truncated for screen in screens),
        )


COMMANDS = (
    InspectDocument(),
    FindNodes(),
    ReadNode(),
    ExtractText(),
    SummarizeStructure(),
    SummarizeLayout(),
    SummarizeStyles(),
    SummarizeContent(),
    SummarizeScreens(),
)
