import csv as stdlib_csv
import io
import json
from pathlib import Path

from pydantic import Field, RootModel, field_validator

from ..models import StrictModel
from ..source_types import (
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_CSV_ROWS = 1_000_000
MAX_CSV_COLUMNS = 10_000
MAX_CSV_CELLS = 5_000_000


class CsvExtractTextInput(StrictModel):
    """Input for csv.extract_text.

    Attributes:
        path: Workspace-relative CSV path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_csv_extension(cls, value: str) -> str:
        """Requires the path to target a CSV file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a CSV extension.

        Raises:
            ValueError: If the path does not end in ``.csv``.
        """
        if Path(value).suffix.lower() != ".csv":
            raise ValueError("csv.extract_text requires a .csv file")
        return value


class CsvExtractTextOutput(StrictModel):
    """Output from csv.extract_text.

    Attributes:
        text: Raw UTF-8 CSV text.
        truncated: Whether the file was longer than the source content limit
            and the text above is only its leading portion. A negative
            assertion cannot be satisfied by content that was cut, so the
            runner needs to know a read was partial.
    """

    text: str
    truncated: bool = False


class ExtractText(SourceCommand[CsvExtractTextInput, CsvExtractTextOutput]):
    """Reads raw UTF-8 CSV files."""

    name = "extract_text"
    input_model = CsvExtractTextInput
    output_model = CsvExtractTextOutput

    def run(self, source_input: CsvExtractTextInput, context: SourceContext) -> CsvExtractTextOutput:
        """Reads CSV text from a workspace file.

        Args:
            source_input: Validated CSV extraction input.
            context: Source runtime context.

        Returns:
            Raw CSV text capped to the configured source content limit.

        """
        resolved = context.resolve_path(source_input.path)
        raw = _read_csv_text(resolved)
        limit = context.max_content_chars
        return CsvExtractTextOutput(text=raw[:limit], truncated=len(raw) > limit)


class CsvInspectTableInput(StrictModel):
    """Input for ``csv.inspect_table``.

    Attributes:
        path: Workspace-relative CSV path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_csv_extension(cls, value: str) -> str:
        """Requires the path to target a CSV file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a CSV extension.

        Raises:
            ValueError: If the path does not end in ``.csv``.
        """
        if Path(value).suffix.lower() != ".csv":
            raise ValueError("csv.inspect_table requires a .csv file")
        return value


class CsvInspectTableOutput(StrictModel):
    """CSV table shape expressed as counts and header names.

    Attributes:
        header: The first row's field names, capped to the configured source
            content limit.
        column_count: Fields in the header row. Exact even when ``header`` is
            truncated.
        row_count: Data rows, excluding the header, which is the number a
            prompt means by "one row per region".
        total_row_count: Every non-empty row, header included.
        delimiter: The delimiter the file actually uses, sniffed from its
            content, falling back to ``","``.
        header_truncated: Whether the evidence budget omitted or cut fields.
    """

    header: list[str]
    column_count: int
    row_count: int
    total_row_count: int
    delimiter: str
    header_truncated: bool = False


class InspectTable(SourceCommand[CsvInspectTableInput, CsvInspectTableOutput]):
    """Report a CSV's header, column count, and row count."""

    name = "inspect_table"
    input_model = CsvInspectTableInput
    output_model = CsvInspectTableOutput

    def run(
        self, source_input: CsvInspectTableInput, context: SourceContext
    ) -> CsvInspectTableOutput:
        """Runs CSV shape inspection.

        Args:
            source_input: Validated table inspection input.
            context: Source runtime context.

        Returns:
            CSV shape as counts and header names.
        """
        resolved = context.resolve_path(source_input.path)
        return inspect_csv_table(resolved, context.max_content_chars)


def inspect_csv_table(path: Path, limit: int) -> CsvInspectTableOutput:
    """Reads a CSV's delimiter, header, and row counts.

    Args:
        path: Resolved CSV file path.
        limit: Maximum number of header characters to return.

    Returns:
        CSV shape as counts and header names.
    """
    text = _read_csv_text(path)
    if not text.strip():
        return CsvInspectTableOutput(
            header=[], column_count=0, row_count=0, total_row_count=0, delimiter=","
        )

    delimiter = sniff_delimiter(text)
    rows = _parse_csv_rows(text, delimiter)
    if not rows:
        return CsvInspectTableOutput(
            header=[],
            column_count=0,
            row_count=0,
            total_row_count=0,
            delimiter=delimiter,
        )

    header: list[str] = []
    total = 0
    for field in rows[0]:
        budget = max(0, limit - total)
        empty_size = len(json.dumps("", ensure_ascii=False)) + 1
        if budget < empty_size:
            break
        low, high = 0, len(field)
        while low < high:
            midpoint = (low + high + 1) // 2
            encoded_size = len(
                json.dumps(field[:midpoint], ensure_ascii=False)
            ) + 1
            if encoded_size <= budget:
                low = midpoint
            else:
                high = midpoint - 1
        bounded = field[:low]
        header.append(bounded)
        total += len(json.dumps(bounded, ensure_ascii=False)) + 1
    return CsvInspectTableOutput(
        header=header,
        column_count=len(rows[0]),
        row_count=len(rows) - 1,
        total_row_count=len(rows),
        delimiter=delimiter,
        header_truncated=len(header) != len(rows[0])
        or any(
            value != original
            for value, original in zip(header, rows[0], strict=False)
        ),
    )


def sniff_delimiter(sample: str) -> str:
    """Detects a CSV delimiter, falling back to a comma.

    ``Sniffer`` raises on input it cannot decide, most commonly a single-column
    file that contains no delimiter at all. Reporting a comma there still gives
    a usable one-column shape instead of failing the whole check.

    Args:
        sample: CSV text to inspect.

    Returns:
        The detected delimiter, or ``","``.
    """
    try:
        return stdlib_csv.Sniffer().sniff(sample[:8192], delimiters=",;\t|").delimiter
    except stdlib_csv.Error:
        return ","


class CsvCompareFilesInput(StrictModel):
    """Input for ``csv.compare_files``.

    Attributes:
        path: Workspace-relative CSV path for the produced deliverable.
        expected_paths: One or more verifier-private gold CSV paths. The
            deliverable passes when it matches any one of them, which lets a
            task accept several equally-correct orderings or encodings.
        strict: Compare lines without trimming surrounding whitespace.
        ignore_case: Lowercase both files before comparing.
    """

    path: str
    expected_paths: list[str]
    strict: bool = True
    ignore_case: bool = False

    @field_validator("path")
    @classmethod
    def require_csv_extension(cls, value: str) -> str:
        """Requires the deliverable path to target a CSV file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a CSV extension.

        Raises:
            ValueError: If the path does not end in ``.csv``.
        """
        if Path(value).suffix.lower() != ".csv":
            raise ValueError("csv.compare_files requires a .csv file")
        return value

    @field_validator("expected_paths")
    @classmethod
    def require_csv_expected(cls, value: list[str]) -> list[str]:
        """Requires every gold path to target a CSV file.

        Args:
            value: Verifier-private gold paths from verifier.json.

        Returns:
            The unchanged paths when each has a CSV extension.

        Raises:
            ValueError: If the list is empty or a path is not ``.csv``.
        """
        if not value:
            raise ValueError("csv.compare_files requires at least one expected path")
        for item in value:
            if Path(item).suffix.lower() != ".csv":
                raise ValueError("csv.compare_files requires .csv expected files")
        return value


class CsvCompareFilesOutput(StrictModel):
    """Result of comparing a CSV deliverable to gold files.

    Attributes:
        score: ``1.0`` when the deliverable matches any gold file, else ``0.0``.
        match: The same verdict as a boolean.
    """

    score: float
    match: bool


class CompareFiles(SourceCommand[CsvCompareFilesInput, CsvCompareFilesOutput]):
    """Compare a CSV deliverable line-for-line against gold CSV files."""

    name = "compare_files"
    input_model = CsvCompareFilesInput
    output_model = CsvCompareFilesOutput

    def run(
        self, source_input: CsvCompareFilesInput, context: SourceContext
    ) -> CsvCompareFilesOutput:
        """Runs the CSV comparison.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            The comparison verdict.
        """
        resolved = context.resolve_path(source_input.path)
        expected = [
            context.resolve_trusted_reference_path(p)
            for p in source_input.expected_paths
        ]
        try:
            expected_lines = []
            for reference in expected:
                text = _read_csv_text(reference)
                rows = _parse_csv_rows(text, sniff_delimiter(text)) if text.strip() else []
                if not rows:
                    raise SourceDataError("trusted CSV reference contains no rows")
                expected_lines.append(text.splitlines())
        except (OSError, SourceDataError) as exc:
            raise SourceCapabilityError(
                f"trusted CSV reference is unreadable: {exc}"
            ) from exc
        score = _compare_csv_lines(
            _read_csv_lines(resolved),
            expected_lines,
            strict=source_input.strict,
            ignore_case=source_input.ignore_case,
        )
        return CsvCompareFilesOutput(score=score, match=score >= 1.0)


def _read_csv_text(path: Path) -> str:
    """Decode one bounded CSV and return it with ``\n`` line endings.

    Every csv command reads through here, so a producer's encoding is handled
    once: BOM sniffing and the script heuristics of :func:`_decode_csv_bytes`,
    then ``\r\n`` / ``\r`` folded to ``\n``. The fold keeps the universal-newline
    text that ``Path.read_text`` used to return: every shipped ``csv.extract_text``
    regex was written against it (``(?m)^...$`` must keep matching a CRLF file),
    ``csv.reader`` accepts either form, and ``compare_files`` splits lines
    itself, so nothing downstream can tell the difference except a regex that
    would otherwise fail on a carriage return.
    """
    if path.stat().st_size > MAX_CSV_BYTES:
        raise SourceDataError(f"CSV exceeds {MAX_CSV_BYTES} bytes: {path.name}")
    with path.open("rb") as handle:
        raw = handle.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise SourceDataError(f"CSV exceeds {MAX_CSV_BYTES} bytes: {path.name}")
    return _normalize_newlines(_decode_csv_bytes(raw, path.name))


def _normalize_newlines(text: str) -> str:
    """Fold CRLF and bare CR to LF, the way universal-newline reads do."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_csv_bytes(raw: bytes, name: str) -> str:
    """Decode CSV bytes using BOMs and conservative script heuristics."""
    bom_encodings = (
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xfe\xff", "utf-16-be"),
        (b"\xff\xfe", "utf-16-le"),
    )
    for marker, encoding in bom_encodings:
        if raw.startswith(marker):
            try:
                return raw.decode(encoding).lstrip("\ufeff")
            except UnicodeDecodeError as exc:
                raise SourceDataError(f"CSV could not be decoded: {name}") from exc
    if raw and raw.count(b"\x00") / len(raw) >= 0.2:
        even_nuls = raw[::2].count(0)
        odd_nuls = raw[1::2].count(0)
        encoding = "utf-16-be" if even_nuls > odd_nuls else "utf-16-le"
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise SourceDataError(f"CSV could not be decoded: {name}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    candidates: list[tuple[str, str]] = []
    for encoding in ("gbk", "cp1251", "windows-1252", "latin-1"):
        try:
            candidates.append((encoding, raw.decode(encoding)))
        except UnicodeDecodeError:
            continue
    for encoding, text in candidates:
        non_ascii_letters = [character for character in text if ord(character) > 127]
        if encoding == "gbk":
            cjk = sum("\u3400" <= character <= "\u9fff" for character in text)
            if cjk >= 2 and cjk >= len(non_ascii_letters) * 0.6:
                return text
        if encoding == "cp1251":
            cyrillic = sum("\u0400" <= character <= "\u052f" for character in text)
            if cyrillic >= 2 and cyrillic >= len(non_ascii_letters) * 0.6:
                return text
    for preferred in ("windows-1252", "latin-1"):
        for encoding, text in candidates:
            if encoding == preferred:
                return text
    raise SourceDataError(f"CSV could not be decoded: {name}")


def _parse_csv_rows(text: str, delimiter: str) -> list[list[str]]:
    """Parse bounded nonblank CSV rows and reject malformed quoting."""
    rows: list[list[str]] = []
    cells = 0
    try:
        for row in stdlib_csv.reader(
            io.StringIO(text), delimiter=delimiter, strict=True
        ):
            if not any(field.strip() for field in row):
                continue
            if len(row) > MAX_CSV_COLUMNS:
                raise SourceDataError(
                    f"CSV row exceeds {MAX_CSV_COLUMNS} columns"
                )
            rows.append(row)
            cells += len(row)
            if len(rows) > MAX_CSV_ROWS or cells > MAX_CSV_CELLS:
                raise SourceDataError("CSV table exceeds supported dimensions")
        return rows
    except stdlib_csv.Error as exc:
        raise SourceDataError(f"malformed CSV: {exc}") from exc


def _read_csv_lines(path: Path) -> list[str]:
    """Read validated CSV text as physical lines for OSWorld-style comparison."""
    text = _read_csv_text(path)
    if text.strip():
        _parse_csv_rows(text, sniff_delimiter(text))
    return text.splitlines()


def _compare_csv_lines(
    result_lines: list[str],
    expected_line_sets: list[list[str]],
    *,
    strict: bool,
    ignore_case: bool,
) -> float:
    """Compare already-validated text without reopening trusted files."""
    for expected_lines in expected_line_sets:
        left: list[str] = result_lines
        right: list[str] = expected_lines
        if not strict:
            left = [line.strip() for line in left]
            right = [line.strip() for line in right]
        if ignore_case:
            left = [line.casefold() for line in left]
            right = [line.casefold() for line in right]
        if left == right:
            return 1.0
    return 0.0


def compare_csv(
    path: Path,
    expected_paths: list[Path],
    *,
    strict: bool = True,
    ignore_case: bool = False,
) -> float:
    """Compares a CSV deliverable against one or more gold CSV files.

    Args:
        path: Resolved deliverable CSV path.
        expected_paths: Resolved gold CSV paths; a match against any passes.
        strict: Compare lines without trimming surrounding whitespace.
        ignore_case: Lowercase both files before comparing.

    Returns:
        ``1.0`` when the deliverable matches any gold file, else ``0.0``.
    """
    return _compare_csv_lines(
        _read_csv_lines(path),
        [_read_csv_lines(expected_path) for expected_path in expected_paths],
        strict=strict,
        ignore_case=ignore_case,
    )


class CsvReadRowsInput(StrictModel):
    """Input for ``csv.read_rows``.

    Attributes:
        path: Workspace-relative CSV path.
    """

    path: str
    delimiter: str | None = Field(
        default=None,
        description=(
            "Field delimiter the contract states (e.g. \",\"). When given, the "
            "file is parsed with exactly this delimiter instead of sniffing, so "
            "a semicolon- or tab-delimited register is read as one column and "
            "fails the column lock with a named reason."
        ),
    )

    @field_validator("path")
    @classmethod
    def require_csv_extension(cls, value: str) -> str:
        """Requires the path to target a CSV file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a CSV extension.

        Raises:
            ValueError: If the path does not end in ``.csv``.
        """
        if Path(value).suffix.lower() != ".csv":
            raise ValueError("csv.read_rows requires a .csv file")
        return value

    @field_validator("delimiter")
    @classmethod
    def require_single_character_delimiter(cls, value: str | None) -> str | None:
        """Requires a stated delimiter to be exactly one character.

        ``csv.reader`` raises ``TypeError`` on a longer delimiter, which the
        grading path swallowed into a bare ``assertion failed``, and an empty
        string silently fell back to sniffing — so ``"ab"`` and ``""`` were both
        accepted specs that graded something other than what they said. Both are
        authoring mistakes, so they are rejected where the spec is validated.

        Args:
            value: The stated delimiter, or None to sniff.

        Returns:
            The unchanged delimiter when it is a single character.

        Raises:
            ValueError: If the delimiter is not exactly one character.
        """
        if value is None:
            return None
        if len(value) != 1:
            raise ValueError(
                "csv.read_rows delimiter must be exactly one character, "
                f"got {value!r}"
            )
        return value


class CsvReadRowsOutput(RootModel[list[dict[str, str]]]):
    """One header-keyed mapping per data row, in file order.

    The root is the row list itself, so verifier JSONPaths address cells
    directly: ``$[?(@.control_id=='R-01')].finding``.
    """


class ReadRows(SourceCommand[CsvReadRowsInput, CsvReadRowsOutput]):
    """Parses a CSV into one header-keyed mapping per data row.

    Cell assertions become structural instead of positional: a check addresses
    a cell by header name and row key, so quoting, embedded delimiters, escaped
    quotes, BOM, CRLF, encoding and column order are all handled by the parser
    rather than simulated in a regex. This is the source ``compare.table_equals``
    grades; ``csv.compare_files`` beside it answers a different question (is
    the file line-for-line one of the gold files).

    Keys and cells are the file's text, untouched: header padding, quoting,
    case and cell whitespace are the comparator's business
    (``compare.normalize_header`` strips surrounding whitespace and one
    surrounding quote pair, casefolds, and every column lock and cell lookup in
    ``compare.table_equals`` matches through it; cells are graded under the
    equivalence contract's cell type), so a JSONPath over this output still
    sees exactly what was written.

    The read shares the module's decoder and parser with ``extract_text`` and
    ``inspect_table``: :func:`_read_csv_text` (BOM / UTF-16 / legacy-codepage
    detection, size bound) and :func:`_parse_csv_rows` (strict quoting,
    whitespace-only rows dropped, dimension caps), so the three commands agree
    on what the file contains and a malformed file is reported as
    ``malformed CSV`` instead of being silently mis-read.
    """

    name = "read_rows"
    input_model = CsvReadRowsInput
    output_model = CsvReadRowsOutput

    def run(
        self, source_input: CsvReadRowsInput, context: SourceContext
    ) -> CsvReadRowsOutput:
        """Runs CSV row parsing.

        Args:
            source_input: Validated row-read input.
            context: Source runtime context.

        Returns:
            One mapping per data row, keyed by the header row's field names.

        Raises:
            SourceDataError: If the file cannot be decoded, exceeds the CSV
                bounds, or is not well-formed CSV.
        """
        resolved = context.resolve_path(source_input.path)
        text = _read_csv_text(resolved)
        if len(text) > context.max_content_chars:
            # Slicing here used to cut mid-row and hand ``table_equals`` a
            # garbled half-row plus a missing tail, which it then reported as
            # the agent's population error. A register the evidence budget
            # cannot hold is reported as exactly that.
            raise SourceDataError(
                f"CSV exceeds max_content_chars ({len(text)} > "
                f"{context.max_content_chars} chars): {resolved.name}"
            )
        if not text.strip():
            return CsvReadRowsOutput([])
        delimiter = source_input.delimiter or sniff_delimiter(text)
        parsed = _parse_csv_rows(text, delimiter)
        if not parsed:
            return CsvReadRowsOutput([])
        header, records = parsed[0], parsed[1:]
        # Two header fields that normalise to the same key would collide in
        # the row mapping (last-wins), so a graded column could silently read
        # the wrong physical column. Name the duplicate instead: same defect
        # class as the xlsx duplicate-header hole. Named with the field as the
        # author WROTE it, normalised only for the collision test, so the
        # reason points at the text in the file.
        seen: dict[str, str] = {}
        for raw in header:
            key = raw.strip().casefold()
            if key in seen:
                raise SourceDataError(
                    f"duplicate header column {raw!r} (also {seen[key]!r})"
                )
            seen[key] = raw
        rows: list[dict[str, str]] = []
        for record in records:
            clean: dict[str, str] = {}
            for index, key in enumerate(header):
                # A short row still carries every header key, so a graded
                # column is "" rather than absent.
                clean[key] = record[index] if index < len(record) else ""
            overflow = record[len(header):]
            # Cells beyond the header: keep them findable, never crash.
            # A trailing delimiter (``CL-12,5.1,SUBSTANTIVE,OBLIGATION_TEXT,``)
            # yields only empty overflow cells — that is a rendering, not
            # content, so no ``_extra`` column is emitted for it and the
            # closed column lock in ``table_equals`` cannot fail on it
            # (verifier-analysis-20260903, V-25).
            if any(cell.strip() for cell in overflow):
                clean["_extra"] = ",".join(overflow)
            rows.append(clean)
        return CsvReadRowsOutput(rows)


COMMANDS = (ExtractText(), InspectTable(), CompareFiles(), ReadRows())
