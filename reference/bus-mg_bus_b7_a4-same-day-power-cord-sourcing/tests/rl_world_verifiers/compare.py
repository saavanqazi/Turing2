import math
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from functools import lru_cache

from jsonpath_ng.ext import parse as _jsonpath_parse_uncached

# jsonpath_ng rebuilds its PLY LALR parse table on EVERY parse call, and every
# check's path is parsed twice per replay (schema validation + extraction) —
# measured 2026-09-02 as 96% of a replay's wall time (3.75s -> 0.072s warm with
# this cache, verdicts byte-identical). parse() is pure in its string argument,
# so memoization cannot change behavior.
jsonpath_parse = lru_cache(maxsize=4096)(_jsonpath_parse_uncached)

from .models import CELL_TYPES, Tolerance, has_non_finite  # noqa: F401 - CELL_TYPES is this module's vocabulary too
from .source_types import SourceDataError


def extract_jsonpath_values(data: Any, path: str) -> list[Any]:
    """Extracts all values matching a JSONPath expression.

    Args:
        data: JSON-like data to inspect.
        path: JSONPath expression to evaluate.

    Returns:
        List of matched values. The root path ``$`` returns the full input as a
        single matched value.
    """
    if path == "$":
        return [data]
    return [match.value for match in jsonpath_parse(path).find(data)]


def actual_from_matches(matches: list[Any]) -> Any:
    """Converts JSONPath matches into the result actual value.

    Args:
        matches: JSONPath matched values.

    Returns:
        ``None`` for no matches, the single value for one match, or the full
        match list for multiple matches.

    Note:
        The single-vs-list result depends on the match count, which changes
        comparison semantics: ``equals X`` compares the whole list when a path
        matches multiple nodes, while ``contains X`` then means "X is one of the
        matched nodes". Anchor JSONPaths that must be singular (e.g. ``[0]``) so a
        comparison is not silently evaluated against a list.
    """
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def compare(
    *,
    actual: Any,
    expected: Any,
    comparison: str,
    tolerance: Tolerance | None = None,
) -> bool:
    """Compares an actual value against an expected value.

    Args:
        actual: Value produced by a source command and optional JSONPath.
        expected: Expected value from the verifier assertion.
        comparison: Comparator name from the verifier schema.
        tolerance: Optional absolute/relative tolerance.

    Returns:
        True when the comparison succeeds; otherwise false.

    Raises:
        TypeError: If values have unsupported types for the comparator, or if
            either side is a non-finite float.
        ValueError: If the comparator name is unsupported.
    """
    # Before dispatch, so every comparator is covered -- not just the ones that
    # route through assert_number_pair. `inf > threshold` is True and every NaN
    # comparison is False, so `not_equals`/`not_in_array` would accept NaN against
    # any expectation. benchmark_runner guards the same way.
    assert_finite(actual, comparison)

    if comparison == "equals":
        return values_equal(actual, expected)
    if comparison == "not_equals":
        return not values_equal(actual, expected)
    if comparison == "approx_equals":
        assert_number_pair(actual, expected, comparison)
        return math.isclose(
            actual,
            expected,
            abs_tol=1e-9 if tolerance is None or tolerance.absolute is None else tolerance.absolute,
            rel_tol=1e-9 if tolerance is None or tolerance.relative is None else tolerance.relative,
        )
    if comparison == "contains":
        return contains(actual, expected)
    if comparison == "not_contains":
        return not contains(actual, expected)
    if comparison == "regex_match":
        # A multi-node JSONPath ($.heading_texts[*]) yields a list: the
        # check then reads "some element matches", the way contains on a
        # list reads "X is one of the elements".
        if isinstance(actual, list):
            return any(
                isinstance(item, str) and bool(re.search(str(expected), item))
                for item in actual
            )
        assert_string(actual, comparison)
        return bool(re.search(str(expected), actual))
    if comparison == "not_regex_match":
        if isinstance(actual, list):
            assert_list_of_strings(actual, comparison)
            return not any(re.search(str(expected), item) for item in actual)
        assert_string(actual, comparison)
        return not bool(re.search(str(expected), actual))
    if comparison == "table_equals":
        return table_equals(actual, expected)
    if comparison == "object_equals":
        return object_equals(actual, expected)
    if comparison == "in_array":
        assert_list(expected, comparison)
        return any(values_equal(actual, item) for item in expected)
    if comparison == "not_in_array":
        assert_list(expected, comparison)
        return not any(values_equal(actual, item) for item in expected)
    if comparison == "greater_than":
        assert_number_pair(actual, expected, comparison)
        return bool(actual > expected)
    if comparison == "less_than":
        assert_number_pair(actual, expected, comparison)
        return bool(actual < expected)
    if comparison == "greater_than_equal":
        assert_number_pair(actual, expected, comparison)
        return bool(actual >= expected)
    if comparison == "less_than_equal":
        assert_number_pair(actual, expected, comparison)
        return bool(actual <= expected)
    raise ValueError(f"unsupported comparison: {comparison}")


def contains(actual: Any, expected: Any) -> bool:
    """Checks string substring or list element containment.

    Args:
        actual: Container value.
        expected: Expected contained value.

    Returns:
        True when ``actual`` contains ``expected``.

    Raises:
        TypeError: If ``actual`` is not a string or list.
    """
    if isinstance(actual, str):
        if not isinstance(expected, str):
            raise TypeError(
                f"contains on a string requires string expected: expected_type={type(expected).__name__}"
            )
        return expected in actual
    if isinstance(actual, list):
        return any(values_equal(expected, item) for item in actual)
    raise TypeError(f"contains requires string or list actual: actual_type={type(actual).__name__}")


def assert_number_pair(actual: Any, expected: Any, comparison: str) -> None:
    """Requires both actual and expected values to be JSON numbers.

    Args:
        actual: Actual value.
        expected: Expected value.
        comparison: Comparator name for error messages.

    Raises:
        TypeError: If either value is not a number.
    """
    if not is_json_number(actual) or not is_json_number(expected):
        raise TypeError(
            f"{comparison} requires JSON numbers: "
            f"actual_type={type(actual).__name__} expected_type={type(expected).__name__}"
        )


def assert_finite(actual: Any, comparison: str) -> None:
    """Rejects a non-finite float anywhere in the actual value.

    The expected side is static config and is validated once at spec load by
    ``models.validate_deterministic_expected``, so it is not re-walked here.

    Args:
        actual: Actual value.
        comparison: Comparator name for error messages.

    Raises:
        TypeError: If the value contains a non-finite float.
    """
    if has_non_finite(actual):
        raise TypeError(f"{comparison} got a non-finite actual value: {actual!r}")


def is_json_number(value: Any) -> bool:
    """Checks whether a value is a finite JSON number in Python form.

    Args:
        value: Value to inspect.

    Returns:
        True for finite int/float values except booleans.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def values_equal(a: Any, b: Any) -> bool:
    """Equality that does NOT treat booleans as their integer values.

    Python evaluates ``True == 1`` and ``False == 0`` as true, which would let a
    document value of ``1`` satisfy ``equals: true`` (and vice versa). This treats
    booleans and numbers as distinct types, so a bool only matches a bool.

    Args:
        a: First value.
        b: Second value.

    Returns:
        True when the values are equal under bool-aware typing.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return bool(a == b)


def assert_list_of_strings(actual: Any, comparison: str) -> None:
    """Every element of a list actual must be a string for a regex comparator."""
    if not all(isinstance(item, str) for item in actual):
        raise TypeError(f"{comparison} on a list requires string elements")


def assert_string(actual: Any, comparison: str) -> None:
    """Requires actual to be a string.

    Args:
        actual: Actual value.
        comparison: Comparator name for error messages.

    Raises:
        TypeError: If actual is not a string.
    """
    if not isinstance(actual, str):
        raise TypeError(f"{comparison} requires string actual: actual_type={type(actual).__name__}")


def assert_list(expected: Any, comparison: str) -> None:
    """Requires expected to be a list.

    Args:
        expected: Expected value.
        comparison: Comparator name for error messages.

    Raises:
        TypeError: If expected is not a list.
    """
    if not isinstance(expected, list):
        raise TypeError(f"{comparison} requires expected to be an array")


# --- Compound comparators ----------------------------------------------------
#
# Ported from the b21 vendored engine (fin-b21 task bundles) and hardened:
# a failure raises :class:`ComparisonMismatch` naming the exact defect instead
# of returning a bare False, numeric cells match under one absolute tolerance
# instead of an enumerated rendering set, and the expected object can lock the
# delivered column set (``columns``) and the delivered row order
# (``row_set_ordered``).
#
# 2026-09-03 — the equivalence contract (verifier-analysis-20260903, V-12/V-13/
# V-23/V-24/V-25). Every graded value is normalised on BOTH sides before it is
# compared, under one of four cell types:
#
#   number  parsed then compared within the absolute tolerance. ``$480.00``,
#           ``480.00 ``, ``1 200``, ``1,200``, ``5%`` and ``+5`` all parse.
#   text    strip, casefold, collapse internal whitespace runs.
#   id      strip only — leading zeros and case are preserved.
#   date    ISO-8601 or a day/month-name/year rendering, canonicalised to
#           ``YYYY-MM-DD``; an unparseable side falls back to ``text``.
#
# A ``table_equals`` column is typed by ``cell_types``; an untyped column is the
# id column (``id``) or inferred cell by cell from the gold (a canonical numeral
# is ``number``, everything else ``text``). Header names are matched after strip
# + casefold on both sides while ``csv.read_rows`` keeps its keys raw. A fully
# empty unnamed column (a trailing delimiter) is not a column. ``object_equals``
# keys carry the same optional ``type`` and, for list values, ``unordered``.
#
# Every failure reason keeps naming the cell — ``row A-03: verdict expected X,
# got Y`` / ``key k: expected X, got Y`` — with the delivered value verbatim:
# sample_solve's classifier parses exactly that shape.

#: Default absolute tolerance for numeric table cells and object values.
DEFAULT_NUMERIC_TOLERANCE = 1e-3

#: Most defects one ComparisonMismatch message spells out before eliding.
MISMATCH_DETAIL_LIMIT = 6

#: A canonical decimal numeral: the gold shape that is INFERRED as a number.
#: Leading-zero strings ("07") do not match, so an untyped column keeps them as
#: text — transcription ids, not quantities. A ``cell_types`` declaration of
#: ``number`` overrides the inference.
_NUMERIC_CELL = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")

#: A comma-grouped number whose groups are all exactly three digits — the only
#: shape whose commas are stripped before numeric parsing ("1,200"; never the
#: malformed "6,00", which is not a number).
_GROUPED_NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")

#: The same rule for space grouping ("1 200", including NBSP / narrow NBSP).
_SPACE_GROUPED_NUMBER = re.compile(r"^\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d+)?$")

#: A parseable unsigned numeral after de-grouping: ``480``, ``480.00``,
#: ``5.``, ``.5``, ``1e3``.
_PLAIN_NUMBER = re.compile(r"^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

#: Delivered column names that are not columns when every cell under them is
#: empty: the unnamed header slot a trailing delimiter creates and the
#: ``_extra`` overflow key ``csv.read_rows`` emits for a ragged row.
_PHANTOM_COLUMNS = frozenset({"", "_extra"})

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_DAY_MONTHNAME_YEAR = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th)?[\s./-]+([A-Za-z]{3,9})\.?[\s./,-]+(\d{4})$"
)
_MONTHNAME_DAY_YEAR = re.compile(
    r"^([A-Za-z]{3,9})\.?[\s./-]+(\d{1,2})(?:st|nd|rd|th)?[\s./,-]+(\d{4})$"
)


class ComparisonMismatch(SourceDataError):
    """A compound-comparator failure whose message names the exact defect.

    Subclasses :class:`SourceDataError` so the existing failure plumbing in
    ``verifiers.verify_deterministic`` lands the message verbatim in the
    result's ``reason`` field: ``is_agent_output_failure`` already classifies
    ``SourceDataError`` as bad agent output (not infrastructure) and
    ``agent_output_failure_reason`` returns ``str(exc)`` for it. No change to
    verifiers.py is needed and every other comparator keeps its behavior.
    """


def mismatch(defects: list[str]) -> ComparisonMismatch:
    """Builds one ComparisonMismatch naming every collected defect, capped.

    Args:
        defects: Human-readable defect descriptions in evaluation order.

    Returns:
        ComparisonMismatch whose message joins the first
        :data:`MISMATCH_DETAIL_LIMIT` defects and counts the rest.
    """
    shown = defects[:MISMATCH_DETAIL_LIMIT]
    if len(defects) > MISMATCH_DETAIL_LIMIT:
        shown.append(f"and {len(defects) - MISMATCH_DETAIL_LIMIT} more defects")
    return ComparisonMismatch("; ".join(shown))


# --- Normalisers --------------------------------------------------------------


def normalize_header(name: Any) -> str:
    """A column name as it is matched: stripped, unquoted and casefolded.

    ``csv.read_rows`` keeps its keys raw; ``table_equals`` looks them up through
    this, so ``clause_id, affected_section`` (space after the comma) and
    ``CLAUSE_ID`` both resolve to the spec's ``affected_section`` / ``clause_id``.

    One surrounding pair of matching quotes is removed after the strip. A
    spreadsheet export that quotes every header field AND writes a space after
    each delimiter (``"ticket_id", "decision"``) is not quoted CSV to
    ``csv.reader``: from the second field on the quote is no longer at field
    start, so the literal quotes stay in the parsed name and the delivered key
    is ``' "decision"'``. Stripping them here rather than in the reader keeps
    ``csv.read_rows`` reporting the file's own text and repairs every name
    match at once — the graded-cell lookup, the closed ``columns`` lock and the
    ``columns_ordered`` order check all resolve names through this one
    function, and every reason still names the delivered column as it was
    written (``raw_names``) and the expected column as the spec wrote it.

    Args:
        name: Raw column name from either side.

    Returns:
        The lookup key.
    """
    text = str(name).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text.casefold()


def normalize_text(value: Any) -> str:
    """The ``text`` normaliser: strip, casefold, collapse whitespace runs.

    Args:
        value: Delivered or expected value.

    Returns:
        The canonical text.
    """
    return " ".join(str(value).split()).casefold()


def normalize_id(value: Any) -> str:
    """The ``id`` normaliser: strip only. Case and leading zeros are preserved.

    Args:
        value: Delivered or expected value.

    Returns:
        The canonical id.
    """
    return str(value).strip()


def numeric_text(value: Any) -> str | None:
    """The plain numeral inside a rendered number, or ``None`` if there is none.

    Strips surrounding whitespace, one leading ``$``, one sign, one trailing
    ``%``, and thousands grouping by comma or space (only when every group is
    exactly three digits: ``1,200`` / ``1 200``, never ``6,00``). The result is
    an unsigned-or-minus decimal/exponent numeral that :class:`Decimal` parses.

    Args:
        value: Delivered or expected cell value.

    Returns:
        The cleaned numeral text, or ``None`` when the value is not a number.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if not isinstance(value, str):
        return str(value)
    text = value.replace("\u00a0", " ").replace("\u202f", " ").strip()
    sign = ""
    if text and text[0] in "+-":
        sign, text = text[0], text[1:].lstrip()
    if text[:1] == "$":
        text = text[1:].lstrip()
        if not sign and text and text[0] in "+-":
            sign, text = text[0], text[1:].lstrip()
    if text[-1:] == "%":
        text = text[:-1].rstrip()
    if _GROUPED_NUMBER.match(text):
        text = text.replace(",", "")
    elif _SPACE_GROUPED_NUMBER.match(text):
        text = re.sub(r"[ \u00a0\u202f]", "", text)
    if not _PLAIN_NUMBER.match(text):
        return None
    return ("-" if sign == "-" else "") + text


def parse_cell_number(actual: Any) -> Decimal | None:
    """Parses one delivered value as a number, or ``None`` when it is not one.

    Accepts every rendering :func:`numeric_text` recognises — ``480``,
    ``480.00 ``, ``$480.00``, ``1,200``, ``1 200``, ``5%``, ``+5``, ``.5``,
    ``1e3`` — and JSON int/float values via their string form. Booleans never
    parse; a leading-zero delivered numeral (``05``) parses as its quantity.

    Args:
        actual: Delivered cell value.

    Returns:
        The delivered quantity as a Decimal, or ``None``.
    """
    text = numeric_text(actual)
    if text is None:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def canonical_date(value: Any) -> str | None:
    """A date rendering canonicalised to ``YYYY-MM-DD``, or ``None``.

    Accepts ISO-8601 (``2026-06-09``, ``2026-6-9``, a trailing time part is
    dropped) and a day / month-name / year rendering in either order
    (``9 June 2026``, ``09-Jun-2026``, ``June 9, 2026``). All-numeric slash
    dates (``11/30/2025``) are ambiguous between D/M/Y and M/D/Y and are not
    parsed: the caller falls back to text comparison.

    Args:
        value: Delivered or expected value.

    Returns:
        The canonical date, or ``None`` when the value is not a recognised date.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    year = month = day = None
    iso = _ISO_DATE.match(text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
    else:
        dmy = _DAY_MONTHNAME_YEAR.match(text)
        mdy = _MONTHNAME_DAY_YEAR.match(text)
        if dmy and dmy.group(2).casefold() in _MONTHS:
            day, month, year = int(dmy.group(1)), _MONTHS[dmy.group(2).casefold()], int(dmy.group(3))
        elif mdy and mdy.group(1).casefold() in _MONTHS:
            month, day, year = _MONTHS[mdy.group(1).casefold()], int(mdy.group(2)), int(mdy.group(3))
    if year is None:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def infer_cell_type(expected: Any) -> str:
    """The type an untyped gold cell is graded under.

    A canonical numeral (``480``, ``480.00``, ``-5``, also rendered as
    ``$480.00`` or ``5%``) is ``number``; everything else — including a
    leading-zero string such as ``07`` — is ``text``.

    Args:
        expected: Gold cell value.

    Returns:
        ``"number"`` or ``"text"``.
    """
    if is_json_number(expected):
        return "number"
    text = numeric_text(expected) if isinstance(expected, str) else None
    return "number" if text is not None and _NUMERIC_CELL.match(text) else "text"


def value_matches(actual: Any, expected: Any, cell_type: str, tolerance: Decimal) -> bool:
    """One delivered value against its expected value under a cell type.

    ``number``: both sides parse (:func:`parse_cell_number`) and differ by at
    most ``tolerance``; a gold that does not parse is compared as ``text``.
    ``text``: equal after :func:`normalize_text`. ``id``: equal after strip.
    ``date``: equal after :func:`canonical_date`, falling back to ``text``
    when either side is not a recognised date. A non-string delivered value
    only ever matches a ``number``.

    Args:
        actual: Delivered value.
        expected: Expected value (a string cell or a JSON scalar).
        cell_type: One of :data:`CELL_TYPES`.
        tolerance: Absolute tolerance for ``number``.

    Returns:
        True when the values are equivalent under the type.
    """
    if cell_type == "number":
        wanted = parse_cell_number(expected)
        if wanted is not None:
            delivered = parse_cell_number(actual)
            return delivered is not None and abs(delivered - wanted) <= tolerance
        cell_type = "text"
    if isinstance(actual, bool) or not isinstance(actual, (str, int, float)):
        return False
    if cell_type == "date":
        got, want = canonical_date(actual), canonical_date(expected)
        if got is not None and want is not None:
            return got == want
        cell_type = "text"
    if cell_type == "id":
        return normalize_id(actual) == normalize_id(expected)
    return normalize_text(actual) == normalize_text(expected)


def cell_matches(actual: Any, expected: str, tolerance: Decimal) -> bool:
    """One table cell against its expected string under the inferred type.

    Kept for callers of the pre-contract signature: the cell is graded as
    :func:`infer_cell_type` says — a numeric gold within ``tolerance``, any
    other gold as normalised text.

    Args:
        actual: Delivered cell value.
        expected: Expected canonical cell string.
        tolerance: Absolute tolerance for numeric expected cells.

    Returns:
        True when the cell matches.
    """
    return value_matches(actual, expected, infer_cell_type(expected), tolerance)


def _to_tolerance(value: Any) -> Decimal:
    """A spec tolerance (number or ``None``) as a Decimal, defaulting."""
    return Decimal(str(DEFAULT_NUMERIC_TOLERANCE if value is None else value))


# --- table_equals -------------------------------------------------------------


def table_equals(actual: Any, expected: Any) -> bool:
    """A keyed table against expected cells, with optional population, column
    and order locks. A failing table raises :class:`ComparisonMismatch`
    naming every defect, so the verdict is diagnosable from the reason alone.

    ``actual`` is ``csv.read_rows`` output — one header-keyed mapping per data
    row, in file order. ``expected``::

        {"id_column": "leg_id",                                  # required
         "rows": {"L01": {"market_value": "480.00", ...}, ...},  # required
         "row_set": ["L01", ..., "L14"],   # optional population lock
         "row_set_ordered": true,          # optional: delivered order == row_set order
         "columns": ["leg_id", ...],       # optional closed column set
         "cell_types": {"leg_id": "id", "market_value": "number",
                        "note": "text", "settled": "date"},  # optional, see below
         "numeric_tolerance": 0.001,       # optional cell tolerance, default 1e-3
         "id_pattern": "\b(ContDet|Pre|OnBoard)\b"}  # optional: key rows by a code inside the id cell

    Cell types (the 2026-09-03 equivalence contract): a column named in
    ``cell_types`` is graded under that type; the id column defaults to ``id``
    (strip only); any other untyped cell is graded as ``number`` when the gold
    is a canonical numeral and as ``text`` (strip + casefold + collapsed
    whitespace) otherwise. Ids on both sides pass through the id column's
    normaliser before rows are keyed, so ``row_set`` and ``rows`` match
    padded ids. Column names are matched after strip + casefold.

    Semantics, in order:
      * every delivered row must carry the id column;
      * a duplicated id inside the graded scope fails (a graded record must
        resolve to exactly one row);
      * with ``row_set``, the delivered id list must equal it as a multiset —
        the row-count lock and membership in one;
      * with ``row_set_ordered``, the delivered id order must equal
        ``row_set`` exactly (the first divergence index is named); order is
        only judged once the population matches;
      * with ``columns``, every delivered row's key set must equal it (each
        offending column is named once, on the first row it appears). An
        unnamed or ``_extra`` column that is empty on every row — a trailing
        delimiter — is not a column;
      * every expected row must be present and every graded cell must match
        under its type. Columns the spec does not grade are not read.

    Args:
        actual: Delivered table rows.
        expected: Expected table object as documented above.

    Returns:
        True when every lock and graded cell matches.

    Raises:
        ComparisonMismatch: If the table fails, naming each defect.
        TypeError: If the expected value is not an object.
    """
    if not isinstance(expected, dict):
        raise TypeError("table_equals expected value must be an object")
    id_column = expected.get("id_column")
    rows: dict[str, dict[str, str]] = expected.get("rows", {})
    row_set = expected.get("row_set")
    columns = expected.get("columns")
    tolerance = _to_tolerance(expected.get("numeric_tolerance"))
    cell_types = {
        normalize_header(column): cell_type
        for column, cell_type in (expected.get("cell_types") or {}).items()
    }
    id_key = normalize_header(id_column)
    id_type = cell_types.get(id_key, "id")
    if not isinstance(actual, list) or not all(isinstance(row, dict) for row in actual):
        raise ComparisonMismatch(
            "delivered value is not a table of row objects "
            f"(got {type(actual).__name__})"
        )

    id_pattern = expected.get("id_pattern")
    id_regex = re.compile(str(id_pattern)) if id_pattern else None

    def norm_id(value: Any) -> str:
        # id_pattern: the id is the first capture group (or the whole
        # match) found in the cell, casefolded — so a label rendered as
        # "Contract detail (ContDet)", "ContDet" or "ContDet - Contract detail"
        # keys the same record when the pattern names the code. A cell with
        # no match keeps its full normalised text, so it can never alias a
        # graded record.
        if id_regex is not None:
            found = id_regex.search(str(value))
            if found:
                token = found.group(1) if found.groups() else found.group(0)
                return normalize_text(token)
        return normalize_id(value) if id_type == "id" else (
            normalize_text(value) if id_type == "text" else str(value).strip()
        )

    defects: list[str] = []

    # Delivered rows keyed by normalised header; raw header names kept for the
    # reasons that name a delivered column.
    table: list[dict[str, Any]] = []
    raw_names: dict[str, str] = {}
    ambiguous: set[str] = set()
    for index, row in enumerate(actual):
        normalised: dict[str, Any] = {}
        seen: dict[str, str] = {}
        for raw_key, value in row.items():
            key = normalize_header(raw_key)
            if key in seen and seen[key] != raw_key and key not in ambiguous:
                ambiguous.add(key)
                defects.append(
                    f"ambiguous column {raw_key!r} duplicates {seen[key]!r} "
                    f"after header normalisation on row #{index + 1}"
                )
            seen[key] = str(raw_key)
            normalised[key] = value
            raw_names.setdefault(key, str(raw_key))
        table.append(normalised)
    phantom = {
        key for key in _PHANTOM_COLUMNS
        if any(key in row for row in table)
        and all(str(row.get(key, "") or "").strip() == "" for row in table)
    }
    for row in table:
        for key in phantom:
            row.pop(key, None)

    # Gold ids in their authored form, for reasons that name a graded record.
    gold_form: dict[str, str] = {}
    for record_id in list(rows) + list(row_set or []):
        gold_form.setdefault(norm_id(record_id), str(record_id))

    delivered_ids: list[str] = []
    delivered_form: dict[str, str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(table):
        if id_key not in row:
            defects.append(f"column {id_column} absent on row {index + 1}")
            continue
        record_id = norm_id(row[id_key])
        delivered_ids.append(record_id)
        delivered_form.setdefault(record_id, str(row[id_key]).strip())
        by_id[record_id] = row

    def label(record_id: str) -> str:
        return gold_form.get(record_id) or delivered_form.get(record_id) or record_id

    graded_scope = {norm_id(record_id) for record_id in list(rows) + list(row_set or [])}
    delivered_counts = Counter(delivered_ids)
    for record_id in sorted(graded_scope, key=label):
        if delivered_counts[record_id] > 1:
            defects.append(f"duplicate id {label(record_id)}")

    expected_ids = [norm_id(record_id) for record_id in row_set] if row_set is not None else None
    population_ok = expected_ids is None or delivered_counts == Counter(expected_ids)
    if expected_ids is not None and not population_ok:
        expected_counts = Counter(expected_ids)
        extra = sorted((delivered_counts - expected_counts).elements(), key=str)
        missing = sorted((expected_counts - delivered_counts).elements(), key=str)
        defects.append(
            "population mismatch: "
            f"extra {[delivered_form.get(i, i) for i in extra]}, "
            f"missing {[label(i) for i in missing]}"
        )
    if expected.get("row_set_ordered") and expected_ids is not None and population_ok:
        for index, (want, got) in enumerate(zip(expected_ids, delivered_ids)):
            if want != got:
                defects.append(
                    f"row order mismatch at index {index}: "
                    f"expected {label(want)}, got {delivered_form.get(got, got)}"
                )
                break

    if columns is not None and expected.get("columns_ordered"):
        # The delivered header order is the key order of every row mapping
        # (``csv.read_rows`` keeps header order; JSON objects keep it too).
        wanted_order = [normalize_header(column) for column in columns]
        for index, row in enumerate(table):
            got_order = [key for key in row if key != "_extra"]
            if got_order != wanted_order and set(got_order) == set(wanted_order):
                first = next(
                    (i for i, (w, g) in enumerate(zip(wanted_order, got_order)) if w != g),
                    0,
                )
                defects.append(
                    "column order mismatch at position "
                    f"{first + 1}: expected {columns[first]}, got "
                    f"{raw_names.get(got_order[first], got_order[first])}"
                )
            break
    if columns is not None:
        wanted_names = {normalize_header(column): str(column) for column in columns}
        wanted = set(wanted_names)
        reported: set[tuple[str, str]] = set()
        for index, row in enumerate(table):
            row_label = label(norm_id(row[id_key])) if id_key in row else f"#{index + 1}"
            for column in sorted(set(row) - wanted):
                if ("extra", column) not in reported:
                    reported.add(("extra", column))
                    defects.append(
                        f"unexpected column {raw_names.get(column, column)} on row {row_label}"
                    )
            for column in sorted(wanted - set(row)):
                if ("missing", column) not in reported:
                    reported.add(("missing", column))
                    defects.append(f"column {wanted_names[column]} absent on row {row_label}")

    for record_id, cells in rows.items():
        row = by_id.get(norm_id(record_id))
        if row is None:
            defects.append(f"row {record_id} missing")
            continue
        for column, cell in cells.items():
            key = normalize_header(column)
            if key not in row:
                defects.append(f"column {column} absent on row {record_id}")
                continue
            cell_type = cell_types.get(key) or infer_cell_type(cell)
            if not value_matches(row[key], cell, cell_type, tolerance):
                defects.append(
                    f"row {record_id}: {column} expected {cell}, got {row[key]}"
                )

    if defects:
        raise mismatch(defects)
    return True


# --- object_equals ------------------------------------------------------------


def infer_value_type(expected: Any) -> str | None:
    """The type an untyped ``object_equals`` value is graded under.

    A JSON number, or a string that is a canonical numeral, is ``number``; any
    other string is ``text``; booleans, ``null`` and objects have no contract
    type and keep bool-aware exact equality (``None``).

    Args:
        expected: Expected scalar.

    Returns:
        ``"number"``, ``"text"`` or ``None``.
    """
    if is_json_number(expected):
        return "number"
    if isinstance(expected, str):
        return infer_cell_type(expected)
    return None


def _scalar_matches(got: Any, value: Any, value_type: str | None, tolerance: Decimal) -> bool:
    """One object value (or list element) against its expected scalar."""
    if value_type is None:
        return values_equal(got, value)
    return value_matches(got, value, value_type, tolerance)


def object_equals(actual: Any, expected: Any) -> bool:
    """A JSON object against per-key expected values, with reasons per key.

    ``expected``::

        {"keys": {"net_market_value": {"value": 2904.0, "tolerance": null},
                  "status": {"value": "TIED", "type": "text"},
                  "codes": {"value": ["A", "B"], "unordered": true},
                  ...},
         "closed": true}

    Each key is graded under a cell type — the declared ``type`` or, when
    absent, :func:`infer_value_type` of the expected value: a numeric
    expected compares within an absolute tolerance (the spec's, or
    :data:`DEFAULT_NUMERIC_TOLERANCE` when the spec gives none, ``null``
    included) and accepts a delivered numeric STRING after parsing; a string
    expected compares as normalised text; booleans and ``null`` keep bool-aware
    exact equality. A list expected compares element-wise with every element
    normalised under the key's type, or as a multiset with ``unordered``.
    With ``closed`` the delivered key set must equal the graded key set — the
    key-set guard and the per-key gates in one check.

    Args:
        actual: Delivered JSON object.
        expected: Expected object as documented above.

    Returns:
        True when the key set (if closed) and every graded key match.

    Raises:
        ComparisonMismatch: If the object fails, naming each defective key.
        TypeError: If the expected value is not an object.
    """
    if not isinstance(expected, dict):
        raise TypeError("object_equals expected value must be an object")
    keys: dict[str, dict[str, Any]] = expected.get("keys", {})
    if not isinstance(actual, dict):
        raise ComparisonMismatch(
            f"delivered value is not an object (got {type(actual).__name__})"
        )
    defects: list[str] = []
    if expected.get("closed"):
        extra = sorted(set(actual) - set(keys))
        missing = sorted(set(keys) - set(actual))
        if extra or missing:
            defects.append(f"key set mismatch: extra {extra}, missing {missing}")
    for key, spec in keys.items():
        if key not in actual:
            defects.append(f"key {key} missing")
            continue
        value = spec.get("value")
        got = actual[key]
        declared = spec.get("type")
        tolerance = _to_tolerance(spec.get("tolerance"))

        if isinstance(value, list):
            if not isinstance(got, list) or len(got) != len(value):
                defects.append(f"key {key}: expected {value!r}, got {got!r}")
                continue
            types = [declared or infer_value_type(item) for item in value]
            if spec.get("unordered"):
                unmatched = list(range(len(got)))
                for item, item_type in zip(value, types):
                    hit = next(
                        (i for i in unmatched if _scalar_matches(got[i], item, item_type, tolerance)),
                        None,
                    )
                    if hit is None:
                        defects.append(f"key {key}: expected {value!r}, got {got!r}")
                        break
                    unmatched.remove(hit)
                continue
            for index, (item, item_type) in enumerate(zip(value, types)):
                if not _scalar_matches(got[index], item, item_type, tolerance):
                    defects.append(
                        f"key {key}[{index}]: expected {item!r}, got {got[index]!r}"
                    )
            continue

        value_type = declared or infer_value_type(value)
        if value_type == "json_number":
            # Strict: the delivered value must BE a JSON number, not a numeral
            # string. Tolerance still applies to the number itself.
            if not is_json_number(got):
                defects.append(
                    f"key {key}: expected JSON number {value}, got "
                    f"{type(got).__name__} {got!r}"
                )
            elif not value_matches(got, value, "number", tolerance):
                defects.append(f"key {key}: expected {value}, got {got}")
            continue
        if has_non_finite(got):
            defects.append(f"key {key}: expected {value!r}, got non-finite {got!r}")
            continue
        if value_type == "number" and parse_cell_number(value) is not None:
            delivered = parse_cell_number(got)
            if delivered is None:
                defects.append(f"key {key}: expected {value}, got non-number {got!r}")
            elif not value_matches(got, value, "number", tolerance):
                defects.append(f"key {key}: expected {value}, got {got}")
                if isinstance(got, str):
                    defects.append(
                        f"key {key}: delivered as numeric string {got!r}, parsed as {delivered}"
                    )
        elif not _scalar_matches(got, value, value_type, tolerance):
            defects.append(f"key {key}: expected {value!r}, got {got!r}")
    if defects:
        raise mismatch(defects)
    return True
