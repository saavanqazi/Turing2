import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import Field, field_validator

from ..models import StrictModel
from ..source_types import (
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

CellValue = bool | int | float | str

EXCEL_MAX_ROW = 1048576
EXCEL_MAX_COLUMN = 16384


def require_workbook_extension(value: str, command_name: str) -> str:
    """Requires the path to target an XLSX/XLSM workbook.

    Args:
        value: Workspace-relative path from verifier.json.
        command_name: Public source command name for the validation error.

    Returns:
        The unchanged path when it has a supported workbook extension.

    Raises:
        ValueError: If the path does not end in ``.xlsx`` or ``.xlsm``.
    """
    if Path(value).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError(f"{command_name} requires a .xlsx or .xlsm file")
    return value


class XlsxExtractTextInput(StrictModel):
    """Input for xlsx.extract_text.

    Attributes:
        path: Workspace-relative XLSX or XLSM path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.extract_text")


class XlsxExtractTextOutput(StrictModel):
    """Output from xlsx.extract_text.

    Attributes:
        text: Extracted sheet names and non-empty cell rows.
    """

    text: str


class ExtractText(SourceCommand[XlsxExtractTextInput, XlsxExtractTextOutput]):
    """Extracts readable text from workbook sheets."""

    name = "extract_text"
    input_model = XlsxExtractTextInput
    output_model = XlsxExtractTextOutput

    def run(self, source_input: XlsxExtractTextInput, context: SourceContext) -> XlsxExtractTextOutput:
        """Runs XLSX text extraction.

        Args:
            source_input: Validated workbook extraction input.
            context: Source runtime context.

        Returns:
            Extracted workbook text.

        """
        resolved = context.resolve_path(source_input.path)
        return XlsxExtractTextOutput(text=extract_xlsx(resolved, context.max_content_chars))


class XlsxExtractFormulasInput(StrictModel):
    """Input for xlsx.extract_formulas.

    Attributes:
        path: Workspace-relative XLSX or XLSM path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.extract_formulas")


class XlsxExtractFormulasOutput(StrictModel):
    """Output from xlsx.extract_formulas.

    Attributes:
        text: One ``Sheet!Cell: =FORMULA`` line per formula cell in the
            workbook, or an empty string when the workbook holds none.
    """

    text: str


class ExtractFormulas(SourceCommand[XlsxExtractFormulasInput, XlsxExtractFormulasOutput]):
    """Lists every formula in a workbook, sheet-qualified, one per line."""

    name = "extract_formulas"
    input_model = XlsxExtractFormulasInput
    output_model = XlsxExtractFormulasOutput

    def run(self, source_input: XlsxExtractFormulasInput, context: SourceContext) -> XlsxExtractFormulasOutput:
        """Runs XLSX formula extraction.

        Args:
            source_input: Validated workbook extraction input.
            context: Source runtime context.

        Returns:
            Extracted workbook formula text.

        """
        resolved = context.resolve_path(source_input.path)
        return XlsxExtractFormulasOutput(text=extract_xlsx_formulas(resolved, context.max_content_chars))


class XlsxReadCellInput(StrictModel):
    """Input for xlsx.read_cell.

    Attributes:
        path: Workspace-relative XLSX or XLSM path.
        sheet: Workbook sheet name.
        cell: A1-style cell coordinate.
    """

    path: str
    sheet: str
    cell: str

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.read_cell")

    @field_validator("sheet")
    @classmethod
    def require_sheet_name(cls, value: str) -> str:
        """Requires a non-empty sheet name."""
        if not value.strip():
            raise ValueError("sheet must be non-empty")
        return value

    @field_validator("cell")
    @classmethod
    def require_cell_coordinate(cls, value: str) -> str:
        """Requires a valid A1-style cell coordinate within Excel's bounds."""
        from openpyxl.utils.cell import coordinate_to_tuple

        coordinate = value.strip().upper()
        if not coordinate:
            raise ValueError("cell must be an A1-style coordinate")
        try:
            row, column = coordinate_to_tuple(coordinate)
        except ValueError as exc:
            raise ValueError("cell must be an A1-style coordinate") from exc
        if row < 1 or column < 1:
            raise ValueError("cell must be an A1-style coordinate")
        if row > EXCEL_MAX_ROW or column > EXCEL_MAX_COLUMN:
            raise ValueError(
                f"cell must be within Excel's limits "
                f"(max row {EXCEL_MAX_ROW}, max column XFD)"
            )
        return coordinate


class XlsxReadCellOutput(StrictModel):
    """Output from xlsx.read_cell.

    ``value`` stays declared first so a rubric judge's evidence block keeps the
    shape it had before ``formula`` and ``has_formula`` were added.

    Attributes:
        value: JSON-safe value read from the requested workbook cell. ``None``
            when the cell holds a formula whose result was never computed into
            the file — the normal state of a workbook written by openpyxl.
        formula: The cell's ``=``-prefixed formula. ``None`` for a cell holding
            no formula, and for the rare formula object that exposes no text.
        has_formula: Whether the cell holds a formula at all. Weaker than
            ``formula``: ``=1`` satisfies it, so prefer asserting on the
            formula text itself.
        number_format: The cell's stored Excel number format, ``"General"``
            when none was applied. This is the only evidence for a prompt that
            requires presentation — currency, a percentage, two decimals —
            because ``value`` reads 1234.5 whether the cell shows ``$1,234.50``
            or ``1234.5``. Grade it with ``contains`` or ``regex_match``.
    """

    value: CellValue | None
    formula: str | None
    has_formula: bool
    number_format: str


class ReadCell(SourceCommand[XlsxReadCellInput, XlsxReadCellOutput]):
    """Reads one populated cell from a workbook sheet."""

    name = "read_cell"
    input_model = XlsxReadCellInput
    output_model = XlsxReadCellOutput

    def run(self, source_input: XlsxReadCellInput, context: SourceContext) -> XlsxReadCellOutput:
        """Runs XLSX cell reading.

        Args:
            source_input: Validated workbook cell input.
            context: Source runtime context.

        Returns:
            The cell's JSON-safe value alongside its formula, if any.

        Raises:
            SourceDataError: If the sheet is missing, or a cell holding no
                formula is blank.
        """
        resolved = context.resolve_path(source_input.path)
        reading = read_xlsx_cell(resolved, source_input.sheet, source_input.cell)
        return XlsxReadCellOutput(
            value=reading.value,
            formula=reading.formula,
            has_formula=reading.has_formula,
            number_format=reading.number_format,
        )


class XlsxInspectModelInput(StrictModel):
    """Input for ``xlsx.inspect_model``.

    ``focus_terms`` must come from the task prompt. They make large workbooks
    inspectable without coupling a verifier to a golden workbook layout.
    """

    path: str
    focus_terms: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        return require_workbook_extension(value, "xlsx.inspect_model")

    @field_validator("focus_terms")
    @classmethod
    def validate_focus_terms(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for term in value:
            cleaned = term.strip()
            if not cleaned:
                raise ValueError("focus_terms entries must be non-empty")
            if len(cleaned) > 160:
                raise ValueError("focus_terms entries must be at most 160 characters")
            if cleaned not in result:
                result.append(cleaned)
        return result


class ModelCell(StrictModel):
    """One non-empty cell exposed as structured workbook evidence."""

    coordinate: str
    value: CellValue | None
    formula: str | None
    has_formula: bool
    number_format: str
    locked: bool
    editable: bool
    references: list[str] = Field(default_factory=list)
    focus_terms: list[str] = Field(default_factory=list)


class FormulaDependency(StrictModel):
    """A formula cell's direct textual reference.

    ``resolved`` means the reference is an ordinary A1/range reference the
    extractor recognised. The raw reference remains available in every case so
    the rubric can avoid pretending unsupported Excel syntax was understood.
    """

    formula_cell: str
    reference: str
    resolved: bool


class ModelSheet(StrictModel):
    """Workbook sheet metadata and selected non-empty cells."""

    name: str
    hidden: bool
    protected: bool
    non_empty_cell_count: int
    formula_count: int
    cells: list[ModelCell] = Field(default_factory=list)


class XlsxInspectModelOutput(StrictModel):
    """Structured, bounded evidence about a workbook model.

    ``unresolved_references`` and ``broken_references`` answer different
    questions and must not be conflated. The first reports operand syntax this
    extractor does not model -- a named range, a structured table reference, a
    link to another workbook -- none of which is a defect. The second reports
    what Excel itself has lost: a formula still carrying ``#REF!`` because the
    range it pointed at was deleted. A workbook can have either without the
    other.

    Attributes:
        sheets: Per-sheet metadata and selected cells, bounded by the cap.
        formula_count: Formula cells across the workbook, never truncated.
        dependencies: Direct operands of the formulas that survived the cap.
        unresolved_references: Operands of the surviving formulas that are not
            ordinary A1/range references. Bounded by the cap, like the
            dependencies it is derived from.
        broken_references: ``Sheet!Cell`` ids of every formula carrying
            ``#REF!``, collected across the whole workbook rather than the
            surviving selection: a lost reference must not become invisible
            because the workbook was large.
        focus_terms: Prompt terms this run was bounded by.
        truncated: Whether any cell could not be represented within the cap.
    """

    sheets: list[ModelSheet]
    formula_count: int
    dependencies: list[FormulaDependency]
    unresolved_references: list[str]
    broken_references: list[str]
    focus_terms: list[str]
    truncated: bool


class InspectModel(SourceCommand[XlsxInspectModelInput, XlsxInspectModelOutput]):
    """Expose workbook structure, formulas, and effective input editability."""

    name = "inspect_model"
    input_model = XlsxInspectModelInput
    output_model = XlsxInspectModelOutput

    def run(
        self, source_input: XlsxInspectModelInput, context: SourceContext
    ) -> XlsxInspectModelOutput:
        resolved = context.resolve_path(source_input.path)
        return inspect_xlsx_model(
            resolved,
            context.max_content_chars,
            source_input.focus_terms,
        )


class XlsxInspectStructureInput(StrictModel):
    """Input for ``xlsx.inspect_structure``.

    Attributes:
        path: Workspace-relative XLSX or XLSM path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.inspect_structure")


class StructureSheet(StrictModel):
    """One worksheet's name, visibility, extent, and embedded object counts."""

    name: str
    hidden: bool
    max_row: int
    max_column: int
    chart_count: int
    image_count: int


class XlsxInspectStructureOutput(StrictModel):
    """Workbook structure expressed as counts and names.

    Deliberately distinct from ``xlsx.inspect_model``: that command reports
    formulas, dependencies and effective editability, is bounded by prompt
    ``focus_terms``, and is gated to rubric assertions because a workbook's
    model design is not a number. This one reports counts and names only, so
    "the workbook has four tabs" is a deterministic assertion instead of a
    judge call or one ``contains "[Sheet: <name>]"`` check per tab, which
    cannot express a count at all and needs the prompt to name every tab.

    Attributes:
        sheet_count: Worksheets in the workbook, hidden ones included.
        sheet_names: Worksheet names in workbook order.
        visible_sheet_count: Worksheets a reader sees without unhiding one.
        hidden_sheet_names: Names of the hidden and very-hidden worksheets.
        chart_count: Charts across the whole workbook.
        image_count: Embedded images across the whole workbook.
        sheets: Per-worksheet detail, in workbook order.
    """

    sheet_count: int
    sheet_names: list[str]
    visible_sheet_count: int
    hidden_sheet_names: list[str]
    chart_count: int
    image_count: int
    sheets: list[StructureSheet]


class InspectStructure(
    SourceCommand[XlsxInspectStructureInput, XlsxInspectStructureOutput]
):
    """Report worksheet tabs, extents, charts, and images as counts and names."""

    name = "inspect_structure"
    input_model = XlsxInspectStructureInput
    output_model = XlsxInspectStructureOutput

    def run(
        self, source_input: XlsxInspectStructureInput, context: SourceContext
    ) -> XlsxInspectStructureOutput:
        """Runs workbook structure inspection.

        Args:
            source_input: Validated workbook structure input.
            context: Source runtime context.

        Returns:
            Workbook structure as counts and names.
        """
        resolved = context.resolve_path(source_input.path)
        return inspect_xlsx_structure(resolved)


def inspect_xlsx_structure(path: Path) -> XlsxInspectStructureOutput:
    """Reads worksheet names, visibility, extents, and embedded object counts.

    Takes no content limit: every field is a count or a worksheet name, so this
    command returns no extracted document text to bound.

    Args:
        path: Resolved workbook path.

    Returns:
        Workbook structure as counts and names.
    """
    from openpyxl import load_workbook

    # NOT read_only, for the reason documented on extract_xlsx: read_only mode
    # trusts the stored <dimension> tag, which would make max_row/max_column
    # report the workbook's claim rather than its cells.
    wb = load_workbook(path, data_only=True)
    try:
        sheets = [
            StructureSheet(
                name=sheet_name,
                hidden=wb[sheet_name].sheet_state != "visible",
                max_row=int(wb[sheet_name].max_row or 0),
                max_column=int(wb[sheet_name].max_column or 0),
                chart_count=len(drawing_collection(wb[sheet_name], "_charts")),
                image_count=len(drawing_collection(wb[sheet_name], "_images")),
            )
            for sheet_name in wb.sheetnames
        ]
        return XlsxInspectStructureOutput(
            sheet_count=len(sheets),
            sheet_names=[sheet.name for sheet in sheets],
            visible_sheet_count=sum(1 for sheet in sheets if not sheet.hidden),
            hidden_sheet_names=[sheet.name for sheet in sheets if sheet.hidden],
            chart_count=sum(sheet.chart_count for sheet in sheets),
            image_count=sum(sheet.image_count for sheet in sheets),
            sheets=sheets,
        )
    finally:
        wb.close()


def drawing_collection(worksheet: Any, attribute: str) -> list[Any]:
    """Reads a worksheet's chart or image collection defensively.

    openpyxl exposes neither collection through a public accessor, so this
    reads the private attribute through ``getattr`` with a default. A future
    openpyxl that renames or drops it then degrades to a count of zero instead
    of raising ``AttributeError``, which is absent from ``agent_failure_types``
    and would turn one structural check into a whole-run infrastructure error.

    Args:
        worksheet: openpyxl worksheet.
        attribute: Private collection attribute name.

    Returns:
        The collection's members, or an empty list when it is absent.
    """
    collection = getattr(worksheet, attribute, None)
    return list(collection) if collection else []


def extract_xlsx(path: Path, limit: int) -> str:
    """Extracts sheet names and non-empty cell rows from a workbook.

    Args:
        path: Resolved workbook path.
        limit: Maximum number of characters to return.

    Returns:
        Extracted workbook text capped to ``limit`` characters.
    """
    from openpyxl import load_workbook

    # NOT read_only: read_only mode trusts the stored <dimension> tag, which can
    # be wrong/narrow and silently truncate rows. Normal mode scans real cells.
    wb = load_workbook(path, data_only=True)
    parts: list[str] = []
    total = 0
    try:
        for sheet_name in wb.sheetnames:
            total = append_limited(parts, f"[Sheet: {sheet_name}]", total, limit)
            if total >= limit:
                return "\n".join(parts)[:limit]
            for row in wb[sheet_name].iter_rows(values_only=True):
                cells = [str(cell) if cell is not None else "" for cell in row]
                if any(cell.strip() for cell in cells):
                    total = append_limited(parts, " | ".join(cells), total, limit)
                    if total >= limit:
                        return "\n".join(parts)[:limit]
        return "\n".join(parts)[:limit]
    finally:
        wb.close()


def inspect_xlsx_model(
    path: Path, limit: int, focus_terms: list[str]
) -> XlsxInspectModelOutput:
    """Extract bounded structural evidence without evaluating workbook formulas.

    Workbook formulas are data, not code: this routine never evaluates them
    and never opens a mutable copy of the artifact. It records direct formula
    references and effective Excel editability so an LLM rubric can assess
    prompt-grounded model design without depending on hard-coded cell names.
    """
    from openpyxl import load_workbook

    # Keep enough space for sheet summaries even when callers configure a
    # small source limit. The output still marks itself truncated whenever a
    # cell could not be represented.
    char_limit = max(limit, 1_000)
    wb_formula = load_workbook(path, data_only=False)
    wb_cached = load_workbook(path, data_only=True)
    lowered_terms = [(term, term.casefold()) for term in focus_terms]
    sheet_meta: dict[str, dict[str, Any]] = {}
    candidates: list[tuple[int, int, int, int, str, ModelCell]] = []
    dependencies_by_formula: dict[str, list[FormulaDependency]] = {}
    unresolved_by_formula: dict[str, list[str]] = {}
    broken_references: list[str] = []

    try:
        # Identify focus matches first so immediate label/value neighbourhoods
        # survive evidence capping even in large source-data tabs.
        focus_neighbours: set[tuple[str, int, int]] = set()
        for sheet_name in wb_formula.sheetnames:
            worksheet = wb_formula[sheet_name]
            for row in worksheet.iter_rows():
                for cell in row:
                    if cell.value is None and cell.data_type != "f":
                        continue
                    haystack = str(formula_text(cell.value) or cell.value or "").casefold()
                    if any(term_lower in haystack for _term, term_lower in lowered_terms):
                        row_index = int(cell.row or 0)
                        column_index = int(cell.column or 0)
                        for row_offset in range(-1, 2):
                            for column_offset in range(-1, 2):
                                neighbour_row = row_index + row_offset
                                neighbour_column = column_index + column_offset
                                if neighbour_row > 0 and neighbour_column > 0:
                                    focus_neighbours.add((sheet_name, neighbour_row, neighbour_column))

        for sheet_index, sheet_name in enumerate(wb_formula.sheetnames):
            worksheet = wb_formula[sheet_name]
            cached_sheet = wb_cached[sheet_name]
            protected = bool(worksheet.protection.sheet)
            non_empty_count = 0
            formula_count = 0
            sheet_meta[sheet_name] = {
                "index": sheet_index,
                "hidden": worksheet.sheet_state != "visible",
                "protected": protected,
                "non_empty_cell_count": 0,
                "formula_count": 0,
            }
            for row in worksheet.iter_rows():
                for cell in row:
                    is_formula = cell.data_type == "f"
                    if cell.value is None and not is_formula:
                        continue
                    non_empty_count += 1
                    formula = formula_text(cell.value) if is_formula else None
                    if is_formula:
                        formula_count += 1
                    cached_value = cached_sheet[cell.coordinate].value if is_formula else cell.value
                    safe_value = None if cached_value is None else json_safe_cell_value(cached_value)
                    matched_terms = [
                        term
                        for term, term_lower in lowered_terms
                        if term_lower in str(formula or safe_value or "").casefold()
                    ]
                    formula_id = f"{qualify_sheet_name(sheet_name)}!{cell.coordinate}"
                    # Collected here, in the whole-workbook scan, rather than
                    # from the surviving selection below: a deleted range is a
                    # defect whether or not its formula fits in the evidence.
                    if formula and "#REF!" in formula:
                        broken_references.append(formula_id)
                    refs = formula_references(formula) if formula else []
                    dependencies = [
                        FormulaDependency(
                            formula_cell=formula_id,
                            reference=reference,
                            resolved=is_resolved_formula_reference(reference),
                        )
                        for reference in refs
                    ]
                    if dependencies:
                        dependencies_by_formula[formula_id] = dependencies
                        unresolved_by_formula[formula_id] = [
                            item.reference for item in dependencies if not item.resolved
                        ]
                    locked = bool(cell.protection.locked)
                    model_cell = ModelCell(
                        coordinate=cell.coordinate,
                        value=safe_value,
                        formula=formula,
                        has_formula=is_formula,
                        number_format=str(cell.number_format or "General"),
                        locked=locked,
                        editable=not (protected and locked),
                        references=refs,
                        focus_terms=matched_terms,
                    )
                    # Prompt matches and their immediate label/value/formula
                    # neighbourhood must survive truncation ahead of unrelated
                    # formulas. Within the remaining evidence, formulas are
                    # stronger than ordinary content.
                    row_index = int(cell.row or 0)
                    column_index = int(cell.column or 0)
                    focused = bool(
                        matched_terms
                        or (sheet_name, row_index, column_index) in focus_neighbours
                    )
                    priority = 0 if focused else (1 if is_formula else 2)
                    candidates.append(
                        (
                            priority,
                            sheet_index,
                            row_index,
                            column_index,
                            sheet_name,
                            model_cell,
                        )
                    )
            sheet_meta[sheet_name]["non_empty_cell_count"] = non_empty_count
            sheet_meta[sheet_name]["formula_count"] = formula_count

        candidates.sort(key=lambda item: item[:4])
        selected_by_sheet: dict[str, list[ModelCell]] = {name: [] for name in wb_formula.sheetnames}
        selected_formula_ids: set[str] = set()
        total = 0
        truncated = False
        for _priority, _sheet_index, _row, _column, sheet_name, model_cell in candidates:
            estimated = len(json.dumps(model_cell.model_dump(), default=str, ensure_ascii=False)) + 1
            if total + estimated > char_limit:
                truncated = True
                continue
            selected_by_sheet[sheet_name].append(model_cell)
            total += estimated
            if model_cell.has_formula:
                selected_formula_ids.add(f"{qualify_sheet_name(sheet_name)}!{model_cell.coordinate}")

        dependencies = [
            dependency
            for formula_id in sorted(selected_formula_ids)
            for dependency in dependencies_by_formula.get(formula_id, [])
        ]
        unresolved = [
            reference
            for formula_id in sorted(selected_formula_ids)
            for reference in unresolved_by_formula.get(formula_id, [])
        ]
        sheets = [
            ModelSheet(
                name=sheet_name,
                hidden=sheet_meta[sheet_name]["hidden"],
                protected=sheet_meta[sheet_name]["protected"],
                non_empty_cell_count=sheet_meta[sheet_name]["non_empty_cell_count"],
                formula_count=sheet_meta[sheet_name]["formula_count"],
                cells=selected_by_sheet[sheet_name],
            )
            for sheet_name in wb_formula.sheetnames
        ]
        return XlsxInspectModelOutput(
            sheets=sheets,
            formula_count=sum(item["formula_count"] for item in sheet_meta.values()),
            dependencies=dependencies,
            unresolved_references=unresolved,
            broken_references=broken_references,
            focus_terms=focus_terms,
            truncated=truncated,
        )
    finally:
        wb_cached.close()
        wb_formula.close()


def formula_references(formula: str) -> list[str]:
    """Return direct range/name operands from an Excel formula conservatively."""
    if not formula:
        return []
    try:
        from openpyxl.formula import Tokenizer

        tokens = Tokenizer(formula).items
    except Exception:
        return []
    references: list[str] = []
    for token in tokens:
        if token.type == "OPERAND" and token.subtype == "RANGE":
            reference = str(token.value).strip()
            if reference and reference not in references:
                references.append(reference)
    return references


_A1_REFERENCE = re.compile(
    r"^(?:(?:'[^']*(?:''[^']*)*'|[A-Za-z_][A-Za-z0-9_. ]*)!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?$"
)


def is_resolved_formula_reference(reference: str) -> bool:
    """Whether a formula operand is an ordinary internal A1/range reference."""
    return bool(_A1_REFERENCE.fullmatch(reference.strip()))


def extract_xlsx_formulas(path: Path, limit: int) -> str:
    """Extracts every formula in a workbook as sheet-qualified lines.

    Each line is ``Sheet!Cell: =FORMULA`` so it stands alone and an author can
    assert on it with ``contains`` without depending on the order sheets happen
    to be listed in. An array formula gains a trailing ``[array <ref>]``.

    A workbook with no formulas yields an empty string rather than an error, so
    a ``not_contains`` assertion can pass for it.

    Args:
        path: Resolved workbook path.
        limit: Maximum number of characters to return.

    Returns:
        Extracted formula text capped to ``limit`` characters.
    """
    from openpyxl import load_workbook

    # data_only=False is the whole point: the cached-value load returns the
    # formula's last computed result, which is what extract_xlsx already shows.
    # NOT read_only, for the same reason extract_xlsx is not: a wrong stored
    # <dimension> would silently truncate the scan.
    wb = load_workbook(path, data_only=False)
    parts: list[str] = []
    total = 0
    try:
        for sheet_name in wb.sheetnames:
            qualifier = qualify_sheet_name(sheet_name)
            for row in wb[sheet_name].iter_rows():
                for cell in row:
                    if cell.data_type != "f":
                        continue
                    formula = formula_text(cell.value)
                    if formula is None:
                        continue
                    line = f"{qualifier}!{cell.coordinate}: {formula}"
                    array_ref = getattr(cell.value, "ref", None)
                    if isinstance(array_ref, str):
                        line = f"{line} [array {array_ref}]"
                    total = append_limited(parts, line, total, limit)
                    if total >= limit:
                        return "\n".join(parts)[:limit]
        return "\n".join(parts)[:limit]
    finally:
        wb.close()


def formula_text(value: Any) -> str | None:
    """Returns a cell's formula as ``=``-prefixed text.

    Args:
        value: Raw value of a cell whose ``data_type`` is ``"f"``. Usually the
            formula string, but an array formula arrives as an ``ArrayFormula``
            object carrying its source in ``.text``.

    Returns:
        The formula text, or ``None`` for a formula object that exposes no
        text. ``DataTableFormula`` is the real case: it has no ``.text``, and
        ``AttributeError`` is not an agent-output failure, so reading the
        attribute unguarded would fail the whole run instead of one check.
    """
    text = value if isinstance(value, str) else getattr(value, "text", None)
    if not isinstance(text, str):
        return None
    return text if text.startswith("=") else f"={text}"


def qualify_sheet_name(sheet_name: str) -> str:
    """Quotes a sheet name for ``Sheet!Cell`` only when Excel's syntax requires it.

    ``openpyxl.utils.quote_sheetname`` quotes unconditionally, which would
    render the common case as ``'Summary'!B8`` and leave an author guessing at
    the fragment to assert on.

    Args:
        sheet_name: Workbook sheet name.

    Returns:
        The sheet name, quoted and escaped only when it holds a character that
        would otherwise be ambiguous in an A1-style reference.
    """
    if not any(character in sheet_name for character in " '!"):
        return sheet_name
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


class CellReading(NamedTuple):
    """One workbook cell as ``xlsx.read_cell`` reports it.

    Attributes:
        value: JSON-safe cell value, or ``None`` for a formula with no result
            computed into the file.
        formula: ``=``-prefixed formula text, or ``None``.
        has_formula: Whether the cell holds a formula.
        number_format: Stored Excel number format, ``"General"`` when unset.
    """

    value: CellValue | None
    formula: str | None
    has_formula: bool
    number_format: str


def read_xlsx_cell(path: Path, sheet: str, cell: str) -> CellReading:
    """Reads one cell from a workbook, with its formula when it has one.

    A formula cell never raises for blankness. A workbook written by openpyxl
    stores no computed result, so requiring one punished exactly the model that
    did the right thing: writing ``=SUM(A1:A2)`` failed while pasting ``30``
    passed. Its ``value`` reports ``None`` and the caller grades ``formula``.

    Args:
        path: Resolved workbook path.
        sheet: Sheet name to read from.
        cell: A1-style cell coordinate to read.

    Returns:
        The cell's value, formula, and whether it holds one.

    Raises:
        SourceDataError: If the sheet is missing, or a cell holding no formula
            is blank.
    """
    from openpyxl import load_workbook

    # NOT read_only: random cell access under a wrong stored <dimension> can read
    # blank for a populated cell. Normal mode reads the real value.
    wb = load_workbook(path, data_only=False)
    try:
        if sheet not in wb.sheetnames:
            raise SourceDataError(f"xlsx.read_cell sheet not found: {sheet}")
        target = wb[sheet][cell]
        number_format = str(target.number_format or "General")
        if target.data_type != "f":
            # A cell holding no formula reads identically under either load
            # mode, so the cached-value load below would find nothing new.
            # `is None`, never `not value`: 0, False and "" are real values.
            if target.value is None:
                raise SourceDataError(f"xlsx.read_cell cell is blank: {sheet}!{cell}")
            return CellReading(
                value=json_safe_cell_value(target.value),
                formula=None,
                has_formula=False,
                number_format=number_format,
            )
        formula = formula_text(target.value)
    finally:
        wb.close()

    return CellReading(
        value=read_cached_cell_value(path, sheet, cell),
        formula=formula,
        has_formula=True,
        number_format=number_format,
    )


def read_cached_cell_value(path: Path, sheet: str, cell: str) -> CellValue | None:
    """Reads the result a spreadsheet application last computed into a cell.

    Args:
        path: Resolved workbook path.
        sheet: Sheet name to read from.
        cell: A1-style cell coordinate to read.

    Returns:
        The cached JSON-safe value, or ``None`` when the file carries none.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    try:
        raw_value = wb[sheet][cell].value
        return None if raw_value is None else json_safe_cell_value(raw_value)
    finally:
        wb.close()


def json_safe_cell_value(value: Any) -> CellValue:
    """Converts an OpenPyXL cell value into a JSON-safe scalar."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def append_limited(parts: list[str], text: str, total: int, limit: int) -> int:
    """Appends text and returns the updated character count.

    Args:
        parts: Text fragments collected so far.
        text: Text fragment to append.
        total: Current approximate character count.
        limit: Maximum desired character count.

    Returns:
        Updated approximate character count.
    """
    parts.append(text)
    return total + len(text) + 1


class XlsxReadCellSkipHeaderInput(StrictModel):
    """Input for deterministic header-relative XLSX cell reading.

    Attributes:
        path: Workspace-relative XLSX or XLSM path.
        sheet: Workbook sheet name.
        cell: A1-style coordinate relative to the first data row.
        header_rows: Exact number of leading header rows to skip.
    """

    path: str
    sheet: str
    cell: str
    header_rows: int = Field(ge=0, le=100)

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.read_cell_skip_header")

    @field_validator("sheet")
    @classmethod
    def require_sheet_name(cls, value: str) -> str:
        """Requires a non-empty sheet name."""
        if not value.strip():
            raise ValueError("sheet must be non-empty")
        return value

    @field_validator("cell")
    @classmethod
    def require_cell_coordinate(cls, value: str) -> str:
        """Requires a valid A1-style cell coordinate within Excel's bounds."""
        from openpyxl.utils.cell import coordinate_to_tuple

        coordinate = value.strip().upper()
        if not coordinate:
            raise ValueError("cell must be an A1-style coordinate")
        try:
            row, column = coordinate_to_tuple(coordinate)
        except ValueError as exc:
            raise ValueError("cell must be an A1-style coordinate") from exc
        if row < 1 or column < 1:
            raise ValueError("cell must be an A1-style coordinate")
        if row > EXCEL_MAX_ROW or column > EXCEL_MAX_COLUMN:
            raise ValueError(
                f"cell must be within Excel's limits "
                f"(max row {EXCEL_MAX_ROW}, max column XFD)"
            )
        return coordinate


class XlsxReadCellSkipHeaderOutput(StrictModel):
    """Output from xlsx.read_cell_skip_header.

    Attributes:
        value: JSON-safe value read from the cell (potentially adjusted).
        formula: The cell's ``=``-prefixed formula, if any.
        has_formula: Whether the cell holds a formula.
        number_format: The cell's stored Excel number format.
        header_detected: Whether the explicit offset is nonzero. Preserved for
            backward-compatible assertions.
        header_rows_skipped: Exact offset applied to the requested coordinate.
        actual_cell: The physical workbook coordinate that was read.
    """

    value: CellValue | None
    formula: str | None
    has_formula: bool
    number_format: str
    header_detected: bool
    header_rows_skipped: int
    actual_cell: str


class ReadCellSkipHeader(SourceCommand[XlsxReadCellSkipHeaderInput, XlsxReadCellSkipHeaderOutput]):
    """Read a data-relative cell after an explicit header-row offset."""

    name = "read_cell_skip_header"
    input_model = XlsxReadCellSkipHeaderInput
    output_model = XlsxReadCellSkipHeaderOutput

    def run(
        self, source_input: XlsxReadCellSkipHeaderInput, context: SourceContext
    ) -> XlsxReadCellSkipHeaderOutput:
        """Runs header-aware XLSX cell reading.

        Args:
            source_input: Validated workbook cell input.
            context: Source runtime context.

        Returns:
            The cell's value with header detection metadata.

        Raises:
            SourceDataError: If the sheet is missing, or the adjusted cell is blank.
        """
        from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

        resolved = context.resolve_path(source_input.path)
        row, col = coordinate_to_tuple(source_input.cell)

        shifted_row = row + source_input.header_rows
        if shifted_row > EXCEL_MAX_ROW:
            raise SourceDataError(
                f"header shift overflows Excel row limit ({EXCEL_MAX_ROW})"
            )
        actual_cell = f"{get_column_letter(col)}{shifted_row}"

        reading = read_xlsx_cell(resolved, source_input.sheet, actual_cell)

        return XlsxReadCellSkipHeaderOutput(
            value=reading.value,
            formula=reading.formula,
            has_formula=reading.has_formula,
            number_format=reading.number_format,
            header_detected=source_input.header_rows > 0,
            header_rows_skipped=source_input.header_rows,
            actual_cell=actual_cell,
        )


class XlsxCompareTableInput(StrictModel):
    """Input for ``xlsx.compare_table``.

    A simplified port of the OSWorld ``compare_table`` metric. Rules run in
    order; the check passes only if every rule passes. Sheets are addressed by
    an index token shared with OSWorld:

    * ``0`` (int) or ``"RI0"`` — sheet at index 0 of the result workbook.
    * ``"RNSheet1"`` — result sheet named ``Sheet1``.
    * ``"EI0"`` / ``"ENSheet1"`` — same, against the expected workbook.

    Attributes:
        path: Workspace-relative XLSX path for the produced deliverable.
        expected_path: Verifier-private XLSX path for the gold reference.
        rules: Ordered comparison rules. Supported ``type`` values:
            ``sheet_name``, ``sheet_data``, ``sheet_print``, ``sheet_fuzzy``,
            ``check_cell``, ``freeze``, ``zoom``.
    """

    path: str
    expected_path: str
    rules: list[dict[str, Any]]

    @field_validator("rules")
    @classmethod
    def require_nonvacuous_well_formed_rules(
        cls, rules: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Reject malformed or vacuous comparator rules at authoring time."""
        import functools
        import math
        import operator

        from openpyxl.utils.cell import coordinate_to_tuple
        from openpyxl.worksheet.cell_range import MultiCellRange

        if not rules:
            raise ValueError("compare_table requires at least one rule")
        if len(rules) > 100:
            raise ValueError("compare_table supports at most 100 rules")
        requirements = {
            "sheet_name": set(),
            "sheet_data": {"sheet_idx0", "sheet_idx1"},
            "sheet_print": {"sheet_idx0", "sheet_idx1"},
            "sheet_fuzzy": {"sheet_idx0", "sheet_idx1", "rules"},
            "check_cell": {"sheet_idx", "coordinate", "props"},
            "freeze": {"sheet_idx0", "sheet_idx1"},
            "zoom": {"sheet_idx", "method", "ref"},
        }
        optional = {
            "sheet_name": set(),
            "sheet_data": {"precision"},
            "sheet_print": {"ignore_case"},
            "sheet_fuzzy": set(),
            "check_cell": set(),
            "freeze": set(),
            "zoom": set(),
        }

        def valid_sheet_token(token: Any) -> bool:
            if isinstance(token, bool):
                return False
            if isinstance(token, int):
                return token >= 0
            return (
                isinstance(token, str)
                and len(token) <= 260
                and bool(re.fullmatch(r"(?:RI|EI)\d+|(?:RN|EN).+", token))
            )

        def validate_scalar_rule(rule: Any, label: str) -> None:
            if not isinstance(rule, dict) or "method" not in rule or "ref" not in rule:
                raise ValueError(f"{label} must contain method and ref")
            method = rule["method"]
            if not isinstance(method, str):
                raise ValueError(f"{label}.method must be a string")
            if method in {"eq", "ne", "le", "lt", "ge", "gt"}:
                return
            if method.startswith("approx:"):
                try:
                    tolerance = float(method.split(":", 1)[1])
                    if tolerance < 0 or not math.isfinite(tolerance):
                        raise ValueError
                except ValueError as exc:
                    raise ValueError(f"{label} has an invalid approximation") from exc
                return
            if method == "re" or method.startswith("re."):
                if not isinstance(rule["ref"], str):
                    raise ValueError(f"{label}.ref must be a regex string")
                allowed_flags = {
                    "A",
                    "ASCII",
                    "I",
                    "IGNORECASE",
                    "M",
                    "MULTILINE",
                    "S",
                    "DOTALL",
                    "U",
                    "UNICODE",
                    "X",
                    "VERBOSE",
                }
                flags = method.split(".")[1:]
                if any(name not in allowed_flags for name in flags):
                    raise ValueError(f"{label} has an invalid regex flag")
                try:
                    flag = functools.reduce(
                        operator.or_,
                        (getattr(re, name) for name in flags),
                        re.RegexFlag(0),
                    )
                    re.compile(rule["ref"], flag)
                except (AttributeError, TypeError, ValueError, re.error) as exc:
                    raise ValueError(f"{label} has an invalid regex") from exc
                return
            raise ValueError(f"{label} has unsupported method {method!r}")

        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"rule {index} must be an object")
            rule_type = rule.get("type")
            if rule_type not in requirements:
                raise ValueError(f"rule {index} has unsupported type {rule_type!r}")
            missing = requirements[rule_type] - rule.keys()
            if missing:
                raise ValueError(f"rule {index} is missing {sorted(missing)!r}")
            allowed = {"type"} | requirements[rule_type] | optional[rule_type]
            unknown = rule.keys() - allowed
            if unknown:
                raise ValueError(f"rule {index} has unknown fields {sorted(unknown)!r}")
            for key in ("sheet_idx", "sheet_idx0", "sheet_idx1"):
                if key in rule and not valid_sheet_token(rule[key]):
                    raise ValueError(f"rule {index} has invalid {key}")

            def token_side(token: int | str) -> str:
                if isinstance(token, int) or token.startswith(("RI", "RN")):
                    return "result"
                return "expected"

            if rule_type in {"sheet_data", "sheet_print", "sheet_fuzzy", "freeze"}:
                sides = {
                    token_side(rule["sheet_idx0"]),
                    token_side(rule["sheet_idx1"]),
                }
                if sides != {"result", "expected"}:
                    raise ValueError(
                        f"rule {index} must compare one result sheet with one expected sheet"
                    )
            if rule_type in {"check_cell", "zoom"} and token_side(
                rule["sheet_idx"]
            ) != "result":
                raise ValueError(
                    f"rule {index} must inspect a result sheet, not the trusted workbook"
                )
            if rule_type == "sheet_data" and (
                isinstance(rule.get("precision", 4), bool)
                or not isinstance(rule.get("precision", 4), int)
                or not 0 <= rule.get("precision", 4) <= 15
            ):
                raise ValueError(f"rule {index} precision must be between 0 and 15")
            if rule_type == "sheet_print" and not isinstance(
                rule.get("ignore_case", False), bool
            ):
                raise ValueError(f"rule {index} ignore_case must be boolean")
            if rule_type == "sheet_fuzzy":
                subrules = rule["rules"]
                if not isinstance(subrules, list) or not subrules:
                    raise ValueError(f"rule {index} requires non-empty fuzzy rules")
                if len(subrules) > 100:
                    raise ValueError(f"rule {index} has too many fuzzy rules")
                fuzzy_cells = 0
                for sub_index, subrule in enumerate(subrules):
                    if not isinstance(subrule, dict) or not {
                        "range",
                        "type",
                    } <= subrule.keys():
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} is malformed"
                        )
                    allowed_subrule_fields = {
                        "range",
                        "type",
                        "normalization",
                        "ignore_chars",
                        "ignore_case",
                        "threshold",
                    }
                    unknown_subrule_fields = subrule.keys() - allowed_subrule_fields
                    if unknown_subrule_fields:
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has unknown fields"
                        )
                    if subrule["type"] not in {
                        "includes",
                        "included_by",
                        "fuzzy_match",
                        "exact_match",
                    }:
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has unsupported type"
                        )
                    try:
                        ranges = list(MultiCellRange(subrule["range"]).ranges)
                        cells = sum(
                            (item.max_row - item.min_row + 1)
                            * (item.max_col - item.min_col + 1)
                            for item in ranges
                        )
                        fuzzy_cells += cells
                        if not ranges or fuzzy_cells > MAX_COMPARE_CELLS:
                            raise ValueError
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has invalid range"
                        ) from exc
                    normalization = subrule.get("normalization", [])
                    if (
                        not isinstance(normalization, list)
                        or len(normalization) > 100
                        or any(
                            not isinstance(pair, (list, tuple))
                            or len(pair) != 2
                            or not all(isinstance(value, str) for value in pair)
                            for pair in normalization
                        )
                    ):
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has invalid normalization"
                        )
                    ignore_chars = subrule.get("ignore_chars", "")
                    if not isinstance(ignore_chars, (str, list, tuple)) or (
                        not isinstance(ignore_chars, str)
                        and not all(isinstance(value, str) for value in ignore_chars)
                    ):
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has invalid ignore_chars"
                        )
                    if not isinstance(subrule.get("ignore_case", False), bool):
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has invalid ignore_case"
                        )
                    threshold = subrule.get("threshold", 85.0)
                    if (
                        isinstance(threshold, bool)
                        or not isinstance(threshold, (int, float))
                        or not math.isfinite(float(threshold))
                        or not 0 <= float(threshold) <= 100
                    ):
                        raise ValueError(
                            f"rule {index} fuzzy rule {sub_index} has invalid threshold"
                        )
            if rule_type == "check_cell":
                try:
                    row, column = coordinate_to_tuple(rule["coordinate"])
                    if not 1 <= row <= 1_048_576 or not 1 <= column <= 16_384:
                        raise ValueError
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"rule {index} has invalid coordinate") from exc
                props = rule["props"]
                if not isinstance(props, dict) or set(props) != {"value"}:
                    raise ValueError(
                        f"rule {index} props must contain exactly one value rule"
                    )
                validate_scalar_rule(props["value"], f"rule {index} value")
            if rule_type == "zoom":
                validate_scalar_rule(rule, f"rule {index} zoom")
        return rules

    @field_validator("path", "expected_path")
    @classmethod
    def require_extension(cls, value: str) -> str:
        """Requires each path to target an XLSX/XLSM workbook.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a supported workbook extension.

        Raises:
            ValueError: If the path is not ``.xlsx``/``.xlsm``.
        """
        return require_workbook_extension(value, "xlsx.compare_table")


MAX_COMPARE_WORKBOOK_BYTES = 128 * 1024 * 1024
MAX_COMPARE_WORKBOOK_MEMBERS = 2_000
MAX_COMPARE_WORKBOOK_RATIO = 200
MAX_COMPARE_XML_ELEMENTS = 500_000
MAX_COMPARE_XML_CELLS = 200_000


def _validate_compare_workbook(path: Path, *, trusted: bool) -> None:
    """Reject malformed or expansion-heavy workbooks before comparison."""
    import zipfile

    error_type = SourceCapabilityError if trusted else SourceDataError
    try:
        if path.stat().st_size > MAX_COMPARE_WORKBOOK_BYTES:
            raise ValueError("workbook exceeds the compressed-size limit")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_COMPARE_WORKBOOK_MEMBERS:
                raise ValueError("workbook has too many archive members")
            total = 0
            xml_elements = 0
            xml_cells = 0
            for member in members:
                if member.file_size > MAX_COMPARE_WORKBOOK_BYTES:
                    raise ValueError("workbook member exceeds the expanded-size limit")
                if member.file_size and not member.compress_size:
                    raise ValueError("workbook has an invalid compressed member")
                if (
                    member.compress_size
                    and member.file_size / member.compress_size
                    > MAX_COMPARE_WORKBOOK_RATIO
                ):
                    raise ValueError("workbook has a suspicious compression ratio")
                actual = 0
                if not member.is_dir():
                    with archive.open(member) as stream:
                        while chunk := stream.read(1024 * 1024):
                            actual += len(chunk)
                            total += len(chunk)
                            if actual > MAX_COMPARE_WORKBOOK_BYTES:
                                raise ValueError("workbook member exceeds its streamed limit")
                            if total > MAX_COMPARE_WORKBOOK_BYTES:
                                raise ValueError("workbook exceeds the expanded-size limit")
                if actual != member.file_size:
                    raise ValueError("workbook member size changed during expansion")
                if member.filename.lower().endswith((".xml", ".rels")):
                    from defusedxml.ElementTree import iterparse

                    with archive.open(member) as stream:
                        for event, element in iterparse(
                            stream, events=("start", "end")
                        ):
                            if event == "start":
                                xml_elements += 1
                                if element.tag.rsplit("}", 1)[-1] == "c":
                                    xml_cells += 1
                                if xml_elements > MAX_COMPARE_XML_ELEMENTS:
                                    raise ValueError("workbook XML is too structurally complex")
                                if xml_cells > MAX_COMPARE_XML_CELLS:
                                    raise ValueError("workbook declares too many cells")
                            else:
                                element.clear()
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=False, data_only=False)
        try:
            for worksheet in workbook.worksheets:
                populated = [
                    cell
                    for cell in worksheet._cells.values()
                    if cell.value is not None
                ]
                if len(populated) > MAX_COMPARE_CELLS:
                    raise ValueError("worksheet exceeds the comparison cell limit")
                if populated and (
                    max(cell.row for cell in populated)
                    * max(cell.column for cell in populated)
                    > MAX_COMPARE_CELLS
                ):
                    raise ValueError("worksheet exceeds the comparison range limit")
                if any(
                    len(str(cell.value)) > MAX_COMPARE_CELL_CHARS
                    for cell in populated
                ):
                    raise ValueError("workbook cell text exceeds the comparison limit")
        finally:
            workbook.close()
    except Exception as exc:
        kind = "trusted workbook reference" if trusted else "workbook artifact"
        raise error_type(f"{kind} is unreadable: {path.name}: {exc}") from exc


def _validate_expected_rule_targets(
    expected_path: Path, rules: list[dict[str, Any]]
) -> None:
    """Prove every expected-side selector names a real trusted worksheet."""
    from openpyxl import load_workbook

    workbook = load_workbook(expected_path, read_only=True, data_only=False)
    try:
        expected_names = workbook.sheetnames
    finally:
        workbook.close()

    for index, rule in enumerate(rules):
        for key in ("sheet_idx0", "sheet_idx1"):
            token = rule.get(key)
            if not isinstance(token, str) or not token.startswith(("EI", "EN")):
                continue
            _book, name = _parse_sheet_idx(token, [], expected_names)
            if not name or name not in expected_names:
                raise SourceCapabilityError(
                    f"trusted workbook rule {index} names a missing expected sheet"
                )


class XlsxCompareTableOutput(StrictModel):
    """Result of running ordered table-comparison rules.

    Attributes:
        score: ``1.0`` when every rule passes, else ``0.0``.
        match: The same verdict as a boolean.
        failed_rule_index: Index of the first failing rule, or ``-1`` when all
            pass. Lets a task author see which rule rejected the deliverable.
    """

    score: float
    match: bool
    failed_rule_index: int


class CompareTable(SourceCommand[XlsxCompareTableInput, XlsxCompareTableOutput]):
    """Compare two workbooks with ordered value, geometry, and view rules."""

    name = "compare_table"
    input_model = XlsxCompareTableInput
    output_model = XlsxCompareTableOutput

    def run(
        self, source_input: XlsxCompareTableInput, context: SourceContext
    ) -> XlsxCompareTableOutput:
        """Runs the workbook comparison rules.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            The pass/fail verdict and the first failing rule index.
        """
        resolved = context.resolve_path(source_input.path)
        expected = context.resolve_trusted_reference_path(source_input.expected_path)
        _validate_compare_workbook(resolved, trusted=False)
        _validate_compare_workbook(expected, trusted=True)
        _validate_expected_rule_targets(expected, source_input.rules)
        try:
            failed = compare_table(resolved, expected, source_input.rules)
        except Exception as exc:
            raise SourceDataError(
                f"workbook artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return XlsxCompareTableOutput(
            score=1.0 if failed < 0 else 0.0,
            match=failed < 0,
            failed_rule_index=failed,
        )


def _parse_sheet_idx(
    sheet_idx: int | str,
    result_names: list[str],
    expected_names: list[str],
) -> tuple[str, str]:
    """Resolves a sheet-index token to ``(book, sheet_name)``.

    Args:
        sheet_idx: Sheet token (see :class:`XlsxCompareTableInput`).
        result_names: Sheet names of the result workbook.
        expected_names: Sheet names of the expected workbook.

    Returns:
        ``("result"|"expected", sheet_name)``. An out-of-range or unknown token
        yields an empty sheet name, which downstream loads treat as missing.

    Raises:
        SourceDataError: If the token prefix is unrecognized.
    """
    if isinstance(sheet_idx, int):
        name = result_names[sheet_idx] if 0 <= sheet_idx < len(result_names) else ""
        return "result", name
    if sheet_idx.startswith("RI"):
        try:
            index = int(sheet_idx[2:])
            return "result", result_names[index] if index >= 0 else ""
        except (ValueError, IndexError):
            return "result", ""
    if sheet_idx.startswith("RN"):
        return "result", sheet_idx[2:]
    if sheet_idx.startswith("EI"):
        try:
            index = int(sheet_idx[2:])
            return "expected", expected_names[index] if index >= 0 else ""
        except (ValueError, IndexError):
            return "expected", ""
    if sheet_idx.startswith("EN"):
        return "expected", sheet_idx[2:]
    raise SourceDataError(f"Unrecognized sheet index: {sheet_idx!r}")


class _WorkbookPair(NamedTuple):
    """Loaded result/expected workbooks and their ordered sheet names."""

    result_wb: Any
    expected_wb: Any
    result_names: list[str]
    expected_names: list[str]


def _select(pair: _WorkbookPair, book: str) -> Any:
    """Return the openpyxl workbook for one side of a comparison."""
    return pair.result_wb if book == "result" else pair.expected_wb


def _match_value_to_rule(value: Any, rule: dict[str, Any]) -> bool:
    """Evaluates a scalar ``{method, ref}`` rule, mirroring OSWorld semantics.

    Args:
        value: The observed value.
        rule: ``{"method": str, "ref": Any}``. ``method`` is one of the
            comparison operators (``eq``, ``ne``, ``le``, ``lt``, ``ge``,
            ``gt``), ``re[.FLAG]`` for a regex search, or ``approx:THRESHOLD``.

    Returns:
        Whether ``value`` satisfies the rule.
    """
    import functools
    import operator

    method = rule["method"]
    ref = rule["ref"]
    if isinstance(value, str) and len(value) > MAX_COMPARE_CELL_CHARS:
        raise SourceDataError("workbook cell text exceeds the comparison limit")
    if method.startswith("re"):
        flags = method.split(".")[1:]
        flag = functools.reduce(
            operator.or_, (getattr(re, fl) for fl in flags), re.RegexFlag(0)
        )
        observed = "" if value is None else str(value)
        return re.search(ref, observed, flag) is not None
    if method in {"eq", "ne", "le", "lt", "ge", "gt"}:
        try:
            return bool(getattr(operator, method)(value, ref))
        except TypeError:
            return False
    if method.startswith("approx:"):
        try:
            return abs(float(value) - float(ref)) <= float(method.split(":")[1])
        except (ValueError, TypeError):
            return False
    raise SourceDataError(f"Unsupported check_cell/zoom method: {method!r}")


MAX_COMPARE_CELLS = 100_000
MAX_COMPARE_CELL_CHARS = 100_000


def _bounded_cell_text(value: Any) -> str:
    """Convert one cell to text without admitting oversized comparison input."""
    text = "" if value is None else str(value)
    if len(text) > MAX_COMPARE_CELL_CHARS:
        raise SourceDataError("workbook cell text exceeds the comparison limit")
    return text


def _normalized_sheet_values(sheet: Any, precision: int) -> dict[str, Any]:
    """Return nonblank formula-aware cell values with bounded cardinality."""
    populated = [cell for cell in sheet._cells.values() if cell.value is not None]
    if len(populated) > MAX_COMPARE_CELLS:
        raise SourceDataError("worksheet exceeds the comparison cell limit")
    values: dict[str, Any] = {}
    for cell in populated:
        value = cell.value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = round(value, precision)
        elif len(str(value)) > MAX_COMPARE_CELL_CHARS:
            raise SourceDataError("workbook cell text exceeds the comparison limit")
        values[cell.coordinate] = value
    return values


def _sheet_data_equal(sheet1: Any, sheet2: Any, precision: int) -> bool:
    """Compare nonblank values, preserving formulas as distinct content."""
    return _normalized_sheet_values(sheet1, precision) == _normalized_sheet_values(
        sheet2, precision
    )


def _sheet_print_lines(sheet: Any) -> list[str]:
    """Render all populated rows, including the first/header row."""
    populated = [cell for cell in sheet._cells.values() if cell.value is not None]
    if not populated:
        return []
    max_row = max(cell.row for cell in populated)
    max_column = max(cell.column for cell in populated)
    if max_row * max_column > MAX_COMPARE_CELLS:
        raise SourceDataError("worksheet print range exceeds the comparison limit")
    return [
        "\t".join(
            _bounded_cell_text(sheet.cell(row, column).value)
            for column in range(1, max_column + 1)
        )
        for row in range(1, max_row + 1)
    ]


def compare_table(path: Path, expected_path: Path, rules: list[dict[str, Any]]) -> int:
    """Runs ordered comparison rules over two workbooks.

    This is a simplified, dependency-light port of the OSWorld ``compare_table``
    metric: it keeps the value, print, fuzzy, cell, freeze, and zoom rule types
    that rely only on openpyxl, and omits the chart/pivot/style/
    filter/sparkline types that need heavier tooling.

    Args:
        path: Resolved deliverable XLSX path.
        expected_path: Resolved gold XLSX path.
        rules: Ordered comparison rules.

    Returns:
        ``-1`` when every rule passes, else the index of the first failing rule.

    Raises:
        SourceDataError: For an unsupported rule type or sheet token.
    """
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.cell_range import MultiCellRange
    from rapidfuzz import fuzz

    result_wb = openpyxl.load_workbook(filename=str(path), data_only=False)
    try:
        expected_wb = openpyxl.load_workbook(
            filename=str(expected_path), data_only=False
        )
    except Exception:
        result_wb.close()
        raise
    pair = _WorkbookPair(
        result_wb=result_wb,
        expected_wb=expected_wb,
        result_names=list(result_wb.sheetnames),
        expected_names=list(expected_wb.sheetnames),
    )

    def resolve(token: int | str) -> tuple[str, str]:
        return _parse_sheet_idx(token, pair.result_names, pair.expected_names)

    def sheet(token: int | str) -> Any:
        book, name = resolve(token)
        if not name:
            return None
        workbook = _select(pair, book)
        return workbook[name] if name in workbook.sheetnames else None

    def printed(token: int | str) -> list[str] | None:
        target = sheet(token)
        return None if target is None else _sheet_print_lines(target)

    try:
        # Input-model validation normally makes this unreachable, but retain a
        # non-vacuity guard for direct helper callers.
        if not rules:
            raise SourceDataError("compare_table requires at least one rule")

        for index, rule in enumerate(rules):
            rule_type = rule["type"]
            if rule_type == "sheet_name":
                passed = pair.result_names == pair.expected_names
            elif rule_type == "sheet_data":
                sheet1 = sheet(rule["sheet_idx0"])
                sheet2 = sheet(rule["sheet_idx1"])
                passed = (
                    sheet1 is not None
                    and sheet2 is not None
                    and _sheet_data_equal(sheet1, sheet2, rule.get("precision", 4))
                )
            elif rule_type == "sheet_print":
                lines1 = printed(rule["sheet_idx0"])
                lines2 = printed(rule["sheet_idx1"])
                if rule.get("ignore_case", False) and lines1 and lines2:
                    lines1 = [line.casefold() for line in lines1]
                    lines2 = [line.casefold() for line in lines2]
                passed = lines1 is not None and lines1 == lines2
            elif rule_type == "sheet_fuzzy":
                passed = _compare_sheet_fuzzy(
                    rule, sheet, MultiCellRange, get_column_letter, fuzz
                )
            elif rule_type == "check_cell":
                passed = _check_cell(rule, sheet)
            elif rule_type == "freeze":
                sheet1 = sheet(rule["sheet_idx0"])
                sheet2 = sheet(rule["sheet_idx1"])
                passed = (
                    sheet1 is not None
                    and sheet2 is not None
                    and sheet1.freeze_panes == sheet2.freeze_panes
                )
            elif rule_type == "zoom":
                target = sheet(rule["sheet_idx"])
                passed = target is not None and _match_value_to_rule(
                    target.sheet_view.zoomScale or 100.0, rule
                )
            else:
                raise SourceDataError(
                    f"Unsupported compare_table rule: {rule_type!r}"
                )

            if not passed:
                return index
        return -1
    finally:
        result_wb.close()
        expected_wb.close()


def _cell_text(sheet: Any, coordinate: str) -> str:
    """Reads a cell's value as text, treating a missing cell as empty.

    Args:
        sheet: An openpyxl worksheet.
        coordinate: Excel coordinate such as ``"B2"``.

    Returns:
        The cell value stringified, or ``""`` when empty.
    """
    return _bounded_cell_text(sheet[coordinate].value)


def _compare_sheet_fuzzy(
    rule: dict[str, Any],
    sheet: Any,
    multi_cell_range: Any,
    get_column_letter: Any,
    fuzz: Any,
) -> bool:
    """Evaluates a ``sheet_fuzzy`` rule over cell ranges.

    Args:
        rule: The fuzzy rule with ``sheet_idx0``/``sheet_idx1`` and ``rules``.
        sheet: Callable resolving a sheet token to a worksheet.
        multi_cell_range: ``MultiCellRange`` class.
        get_column_letter: openpyxl column-letter helper.
        fuzz: rapidfuzz ``fuzz`` module.

    Returns:
        Whether every sub-rule holds across every addressed cell.
    """
    sheet1 = sheet(rule["sheet_idx0"])
    sheet2 = sheet(rule["sheet_idx1"])
    if sheet1 is None or sheet2 is None:
        return False
    for sub in rule["rules"]:
        for cell_range in multi_cell_range(sub["range"]):
            for row, col in cell_range.cells:
                coordinate = f"{get_column_letter(col)}{row}"
                value1 = _cell_text(sheet1, coordinate)
                value2 = _cell_text(sheet2, coordinate)
                for old, new in sub.get("normalization", []):
                    value1 = value1.replace(old, new)
                    value2 = value2.replace(old, new)
                if "ignore_chars" in sub:
                    ignore = set(sub["ignore_chars"])
                    value1 = "".join(ch for ch in value1 if ch not in ignore)
                    value2 = "".join(ch for ch in value2 if ch not in ignore)
                if sub.get("ignore_case", False):
                    value1, value2 = value1.casefold(), value2.casefold()
                sub_type = sub["type"]
                if sub_type == "includes":
                    matched = value2 in value1
                elif sub_type == "included_by":
                    matched = value1 in value2
                elif sub_type == "fuzzy_match":
                    matched = fuzz.ratio(value1, value2) >= sub.get("threshold", 85.0)
                elif sub_type == "exact_match":
                    matched = value1 == value2
                else:
                    raise SourceDataError(f"Unsupported sheet_fuzzy type: {sub_type!r}")
                if not matched:
                    return False
    return True


def _check_cell(rule: dict[str, Any], sheet: Any) -> bool:
    """Evaluates a ``check_cell`` rule against a single cell's value.

    Only the ``value`` property is supported (style checks require the heavier
    OSWorld tooling that this port omits).

    Args:
        rule: The ``check_cell`` rule with ``sheet_idx``, ``coordinate``,
            ``props``.
        sheet: Callable resolving a sheet token to a worksheet.

    Returns:
        Whether every ``value`` sub-rule holds.

    Raises:
        SourceDataError: If a non-``value`` property is requested.
    """
    target = sheet(rule["sheet_idx"])
    if target is None:
        return False
    cell = target[rule["coordinate"]]
    for prop, sub_rule in rule["props"].items():
        if prop != "value":
            raise SourceDataError(
                f"xlsx.compare_table check_cell supports only 'value', got {prop!r}"
            )
        if not _match_value_to_rule(cell.value, sub_rule):
            return False
    return True



# --- read_rows: the row-keyed reader ``compare.table_equals`` grades ---------

#: Most data rows ``xlsx.read_rows`` returns before it refuses the table.
MAX_READ_ROWS = 10000

#: Most header columns a logical table may declare.
MAX_READ_COLUMNS = 512

_A1_RANGE = re.compile(r"^([A-Za-z]{1,3})([1-9]\d*):([A-Za-z]{1,3})([1-9]\d*)$")


class XlsxReadRowsInput(StrictModel):
    """Input for ``xlsx.read_rows``.

    Attributes:
        path: Workspace-relative workbook path.
        sheet: Sheet name, or a zero-based sheet index.
        header_row: 1-based row holding the header. ``None`` (default)
            auto-detects it as the first populated row that is not a title
            line -- a row one cell wide (a title in ``A1``, a merged banner,
            a column-group label) or at most half as wide as the row under it
            (``Repriced catalog | 2026-06-01`` over a seven-column register)
            is skipped. A sheet whose rows are all title-shaped (a
            one-column list) falls back to its first populated row.
        table_range: Optional A1-style rectangle (``B3:H40``) bounding the
            logical table; cells outside it are never read. The header is the
            range's first row unless ``header_row`` is given.
        stop_at_blank_row: When true (default) the first fully blank row after
            the body starts ends the logical table, so notes or a legend
            written below a blank separator are not rows. Blank rows directly
            under the header are skipped (a spacer row is a rendering).
        ignore_columns_outside_header: When true (default) cells right of the
            table's rectangular block -- past at least one blank column -- are
            ignored. Cells in the column adjoining the last header column are
            still part of the block and surface under ``_extra``: an unheaded
            overflow column is table content, a note in a far cell is not.
            When false every non-empty cell right of the header on a body row
            is reported under ``_extra``.
        percent_format_as_percent: When true (default) a numeric cell whose
            number format contains ``%`` is emitted as Excel shows it
            (``0.238`` under ``0.0%`` -> ``"23.8%"``), which
            ``compare.table_equals`` parses as the number 23.8. The stored
            fraction is the file's truth; the ``%`` format is the producer's
            declaration that the quantity is a percentage, and the two
            renderings a reasonable gold could use are made comparable.
    """

    path: str
    sheet: str | int
    header_row: int | None = Field(default=None, ge=1, le=EXCEL_MAX_ROW)
    table_range: str | None = None
    stop_at_blank_row: bool = True
    ignore_columns_outside_header: bool = True
    percent_format_as_percent: bool = True

    @field_validator("path")
    @classmethod
    def require_workbook_extension(cls, value: str) -> str:
        """Requires the path to target an XLSX/XLSM workbook."""
        return require_workbook_extension(value, "xlsx.read_rows")

    @field_validator("sheet")
    @classmethod
    def require_sheet(cls, value: str | int) -> str | int:
        """Requires a non-empty sheet name or a non-negative sheet index."""
        if isinstance(value, bool):
            raise ValueError("xlsx.read_rows sheet must be a name or a zero-based index")
        if isinstance(value, int):
            if value < 0:
                raise ValueError("xlsx.read_rows sheet index must be >= 0")
            return value
        if not value.strip():
            raise ValueError("xlsx.read_rows sheet name must not be empty")
        return value

    @field_validator("table_range")
    @classmethod
    def require_a1_range(cls, value: str | None) -> str | None:
        """Requires an A1-style rectangle such as ``A1:G20``."""
        if value is None:
            return None
        if not _A1_RANGE.match(value.strip()):
            raise ValueError("xlsx.read_rows table_range must be an A1-style range like A1:G20")
        return value.strip()


class XlsxReadRowsOutput(StrictModel):
    """Rows of one logical table, keyed by header name.

    Attributes:
        sheet: Name of the sheet that was read.
        header_row: 1-based row the header was taken from.
        columns: Header names in sheet order, as written (strip / casefold is
            the comparator's business, exactly as for ``csv.read_rows``).
        rows: One header-keyed mapping per body row, in sheet order. Every
            cell is a string: numbers as their shortest round-trip decimal
            (``117``, ``117.4``, never ``117.0``), dates ISO-8601, booleans
            ``TRUE``/``FALSE``, blank ``""``. A formula cell carries the
            result the producer cached into the file, or ``""`` when none was
            cached (openpyxl-written formulas). Cells in the column adjoining
            the last header column appear under ``_extra``.
        row_count: Number of body rows returned.
        truncated: Whether the body was cut at ``MAX_READ_ROWS``.
        rows_after_table: Table-shaped rows below the logical table, where
            table-shaped means two or more populated cells inside the table's
            column block; 0 says the sheet carries exactly one table. Lock it
            to 0 to reject a second "revised" register stacked under a blank
            row, a totals block or a stray data row that ``rows`` cannot see.
            The contract that lock imposes: nothing but one-cell notes may sit
            below the table, so a two-cell footnote (``Prepared by | Ops``)
            counts as 1. A task whose gold layout carries such a footnote
            bounds the table with ``table_range`` instead of dropping the
            lock. The threshold stays at two cells rather than a fraction of
            the header width because a stacked revision that fills only its id
            and one graded column is exactly two cells wide.
    """

    sheet: str
    header_row: int
    columns: list[str]
    rows: list[dict[str, str]]
    row_count: int
    truncated: bool
    rows_after_table: int = 0


def _cell_text(value: Any, number_format: str, percent: bool) -> str:
    """Renders one stored cell value the way ``csv.read_rows`` would carry it."""
    from decimal import Decimal

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return str(value)
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
        if percent and "%" in number_format:
            number = number * 100
            return f"{_plain_decimal(number)}%"
        return _plain_decimal(number)
    if isinstance(value, datetime):
        # Excel has no date type: a date is a serial with a date format, and
        # openpyxl hands it back as a midnight datetime. Report it as the date.
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def _plain_decimal(number: Any) -> str:
    """Shortest plain (non-exponent) rendering of a Decimal, no trailing zeros."""
    text = format(number.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def read_xlsx_rows(path: Path, source_input: XlsxReadRowsInput) -> XlsxReadRowsOutput:
    """Reads one logical table from a worksheet.

    The walk touches only the cells the file materialises (``ws._cells``), so
    a single styled cell at ``G1048576`` -- which inflates ``<dimension>`` and
    makes a used-range ``iter_rows`` walk cost tens of seconds and gigabytes --
    costs nothing here: the table is bounded by the last non-empty row that is
    contiguous with the body, never by the sheet's declared dimension.

    Args:
        path: Resolved workbook path.
        source_input: Validated ``read_rows`` input.

    Returns:
        The table's header and rows.

    Raises:
        SourceDataError: If the sheet is missing, the header row is empty, a
            header name is duplicated (the same name over two columns is a
            hedge, not a table -- the cell the gold names would be ambiguous),
            or the table exceeds the row / column bounds.
    """
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string, get_column_letter

    # data_only=True: a formula cell yields the cached result the producer
    # wrote, which is what the delivered workbook shows a reader.
    wb = load_workbook(path, data_only=True)
    try:
        if isinstance(source_input.sheet, int):
            if source_input.sheet >= len(wb.sheetnames):
                raise SourceDataError(
                    f"xlsx.read_rows sheet index {source_input.sheet} out of range "
                    f"(workbook has {len(wb.sheetnames)} sheets)"
                )
            sheet_name = wb.sheetnames[source_input.sheet]
        else:
            if source_input.sheet not in wb.sheetnames:
                raise SourceDataError(f"xlsx.read_rows sheet not found: {source_input.sheet}")
            sheet_name = source_input.sheet
        ws = wb[sheet_name]
        # Materialised cells only. ``_cells`` is openpyxl's own store for a
        # normal-mode worksheet: every cell the file wrote (value or style),
        # nothing the <dimension> merely claims.
        materialised = getattr(ws, "_cells", None)
        if materialised is None:
            cells = {(c.row, c.column): c for row in ws.iter_rows() for c in row}
        else:
            cells = dict(materialised)
    finally:
        wb.close()

    min_row, max_row, min_col, max_col = 1, EXCEL_MAX_ROW, 1, EXCEL_MAX_COLUMN
    if source_input.table_range:
        m = _A1_RANGE.match(source_input.table_range)
        assert m is not None
        c1, r1, c2, r2 = m.groups()
        min_col, max_col = sorted((column_index_from_string(c1.upper()), column_index_from_string(c2.upper())))
        min_row, max_row = sorted((int(r1), int(r2)))

    def in_range(key: tuple[int, int]) -> bool:
        r, c = key
        return min_row <= r <= max_row and min_col <= c <= max_col

    populated = {k: c.value for k, c in cells.items() if in_range(k) and not _is_blank(c.value)}
    if not populated:
        raise SourceDataError(f"xlsx.read_rows sheet has no non-empty cells: {sheet_name}")

    header_row = source_input.header_row
    if header_row is None:
        # Auto-detect. A title row is a populated row that is too narrow to be
        # the header of the table under it: either one cell wide ("Repriced
        # catalog" in A1, a merged A1:G1 banner -- only its top-left cell
        # carries the value -- or a column-group label with a gap after it),
        # or at most half as wide as the next populated row (a title beside
        # its date, ``Repriced catalog | 2026-06-01`` over a seven-column
        # register). The header is the first populated row that is not a
        # title; a sheet of only title-shaped rows (a one-column list) falls
        # back to its first populated row. Half is deliberately conservative:
        # a real header is never half its own body row unless the body
        # carries at least as many unheaded overflow columns as headed ones,
        # so an overflow hedge in the first data row cannot displace it.
        candidates = sorted({r for r, _ in populated})

        def run_width(row: int) -> int:
            column = min(c for (rr, c) in populated if rr == row)
            width = 0
            while (row, column + width) in populated:
                width += 1
            return width

        header_row = candidates[0]
        widths = {r: run_width(r) for r in candidates}
        for index, r in enumerate(candidates):
            width = widths[r]
            if width < 2:
                continue  # a one-cell title, banner or group label
            below = candidates[index + 1] if index + 1 < len(candidates) else None
            if below is not None and width * 2 <= widths[below]:
                continue  # too narrow to head the row under it: a title line
            header_row = r
            break
    header_cols = sorted(c for (r, c) in populated if r == header_row)
    if not header_cols:
        raise SourceDataError(f"xlsx.read_rows header row {header_row} is empty: {sheet_name}")
    # The header is the contiguous run starting at its first non-empty cell;
    # a note past a blank column (``I1`` beside an A..G header) is not a column.
    first = header_cols[0]
    last = first
    while (header_row, last + 1) in populated:
        last += 1
    if last - first + 1 > MAX_READ_COLUMNS:
        raise SourceDataError(f"xlsx.read_rows header exceeds {MAX_READ_COLUMNS} columns: {sheet_name}")
    columns = [str(populated[(header_row, c)]).strip() if isinstance(populated[(header_row, c)], str)
               else _cell_text(populated[(header_row, c)], "General", False)
               for c in range(first, last + 1)]
    raw_columns = [str(populated[(header_row, c)]) for c in range(first, last + 1)]
    seen: dict[str, list[int]] = {}
    for offset, name in enumerate(columns):
        seen.setdefault(name.casefold(), []).append(first + offset)
    duplicates = {name: cols for name, cols in seen.items() if len(cols) > 1}
    if duplicates:
        detail = "; ".join(
            f"{columns[cols[0] - first]!r} at {', '.join(get_column_letter(c) for c in cols)}"
            for cols in duplicates.values()
        )
        raise SourceDataError(
            f"xlsx.read_rows duplicate header in {sheet_name}!{header_row}: {detail} "
            "(a header name must name exactly one column)"
        )

    body_rows = sorted({r for (r, _) in populated if r > header_row})
    rows: list[dict[str, str]] = []
    truncated = False
    started = False
    previous = header_row
    for r in body_rows:
        if started and source_input.stop_at_blank_row and r != previous + 1:
            break  # a blank row ended the logical table
        previous = r
        # Row-level blankness is judged on the table block (header columns and
        # the adjoining overflow column), never on far cells.
        block = {c for (rr, c) in populated if rr == r and first <= c <= last + 1}
        if not block:
            # Far-cell-only row: a note outside the table. It neither starts
            # nor continues the body, but does not break contiguity either
            # (it has no cells in the block, so it is a blank body row).
            if started and source_input.stop_at_blank_row:
                break
            continue
        started = True
        if len(rows) >= MAX_READ_ROWS:
            truncated = True
            break
        record: dict[str, str] = {}
        for offset, name in enumerate(raw_columns):
            cell = cells.get((r, first + offset))
            value = None if cell is None else cell.value
            fmt = "General" if cell is None else str(cell.number_format or "General")
            record[name] = _cell_text(value, fmt, source_input.percent_format_as_percent)
        # Overflow: the adjoining column and every contiguous non-empty
        # column after it belong to the block; past a blank column is outside.
        overflow: list[str] = []
        c = last + 1
        while (r, c) in populated and c <= max_col:
            cell = cells[(r, c)]
            overflow.append(_cell_text(cell.value, str(cell.number_format or "General"), source_input.percent_format_as_percent))
            c += 1
        if not source_input.ignore_columns_outside_header:
            far = sorted(cc for (rr, cc) in populated if rr == r and cc > c)
            overflow.extend(_cell_text(cells[(r, cc)].value, "General", False) for cc in far)
        if overflow:
            record["_extra"] = ",".join(overflow)
        rows.append(record)
    if truncated:
        raise SourceDataError(
            f"xlsx.read_rows table exceeds {MAX_READ_ROWS} rows: {sheet_name}!{header_row}"
        )
    # Content below the logical table (a second "revised" register stacked
    # under a blank row, a totals block, notes) is reported, never graded here:
    # a verifier that wants the sheet to hold exactly one table locks
    # ``$.rows_after_table`` to 0.
    below: dict[int, int] = {}
    for (rr, c) in populated:
        if rr > previous and first <= c <= last + 1:
            below[rr] = below.get(rr, 0) + 1
    # Only table-shaped rows (two or more block cells) count; a one-cell
    # note under the table ("Prices per unit") is not a second table.
    rows_after_table = sum(1 for n in below.values() if n >= 2) if started else 0
    return XlsxReadRowsOutput(
        sheet=sheet_name,
        header_row=header_row,
        columns=raw_columns,
        rows=rows,
        row_count=len(rows),
        rows_after_table=rows_after_table,
        truncated=False,
    )


class ReadRows(SourceCommand[XlsxReadRowsInput, XlsxReadRowsOutput]):
    """Reads one logical table from a worksheet as header-keyed rows.

    The xlsx sibling of ``csv.read_rows``: ``compare.table_equals`` grades
    ``$.rows`` by row key and header name, so a register delivered as a
    workbook is graded by what it says rather than by 126 positional
    ``read_cell`` sidecars that break when the table moves one column right.

    Table boundary: the header is the contiguous run of non-empty cells in the
    header row; the body is the contiguous run of non-blank rows below it
    (leading spacer rows skipped) and ends at the first blank row; cells past a
    blank column to the right are outside the table, cells adjoining it are
    overflow (``_extra``). Duplicate header names are refused rather than
    resolved last-wins. Only materialised cells are walked, so an inflated
    ``<dimension>`` costs nothing.

    The header row is auto-detected past title lines (a one-cell title or
    merged banner, a title beside its date), because a business register is
    usually written under a heading; ``header_row`` pins it when a layout
    defeats the heuristic. What the body ends at is reported, not silently
    dropped: ``rows_after_table`` counts the table-shaped rows below it, so a
    check locking ``$.rows_after_table`` to 0 rejects a second "revised"
    register stacked under a blank row -- content the graded ``rows`` cannot
    see.

    Stored value is the truth: a cell storing ``117.4`` under number format
    ``0`` (displayed ``117``) is reported as ``117.4``. The one display
    convention honoured is the ``%`` format, because it changes the unit of
    the stored number rather than its precision.
    """

    name = "read_rows"
    input_model = XlsxReadRowsInput
    output_model = XlsxReadRowsOutput

    def run(self, source_input: XlsxReadRowsInput, context: SourceContext) -> XlsxReadRowsOutput:
        """Runs the row read.

        Args:
            source_input: Validated row-read input.
            context: Source runtime context.

        Returns:
            The table's header and rows.
        """
        resolved = context.resolve_path(source_input.path)
        return read_xlsx_rows(resolved, source_input)


COMMANDS = (
    ExtractText(),
    ExtractFormulas(),
    ReadCell(),
    ReadCellSkipHeader(),
    InspectModel(),
    InspectStructure(),
    CompareTable(),
    ReadRows(),
)
