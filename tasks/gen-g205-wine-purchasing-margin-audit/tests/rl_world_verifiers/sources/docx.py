import re
import zipfile
from pathlib import Path
from typing import Any

from pydantic import Field, RootModel, field_validator

from ..models import StrictModel
from ..source_types import (
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

MAX_DOCX_BYTES = 64 * 1024 * 1024
MAX_DOCX_MEMBERS = 4_096
MAX_DOCX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
MAX_DOCX_XML_ELEMENTS = 500_000
MAX_DOCX_CONTENT_ELEMENTS = 200_000


class DocxExtractTextInput(StrictModel):
    """Input for docx.extract_text.

    Attributes:
        path: Workspace-relative DOCX path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires the path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.extract_text requires a .docx file")
        return value


class DocxExtractTextOutput(StrictModel):
    """Output from docx.extract_text.

    Attributes:
        text: Extracted body paragraph and table text.
    """

    text: str


class ExtractText(SourceCommand[DocxExtractTextInput, DocxExtractTextOutput]):
    """Extracts text from DOCX body paragraphs and tables."""

    name = "extract_text"
    input_model = DocxExtractTextInput
    output_model = DocxExtractTextOutput

    def run(self, source_input: DocxExtractTextInput, context: SourceContext) -> DocxExtractTextOutput:
        """Runs DOCX text extraction.

        Args:
            source_input: Validated DOCX extraction input.
            context: Source runtime context.

        Returns:
            Extracted DOCX text.

        """
        resolved = context.resolve_path(source_input.path)
        _validate_docx_package(resolved, trusted=False)
        return DocxExtractTextOutput(
            text=extract_docx(resolved, context.max_content_chars)
        )


class DocxInspectDocumentInput(StrictModel):
    """Input for ``docx.inspect_document``.

    Attributes:
        path: Workspace-relative DOCX path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires the path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.inspect_document requires a .docx file")
        return value


class DocumentHeading(StrictModel):
    """One heading paragraph with its outline level.

    Attributes:
        text: The heading's visible text.
        level: The trailing number of its ``Heading N`` style, or ``0`` for a
            heading style carrying no level.
    """

    text: str
    level: int


class DocxInspectDocumentOutput(StrictModel):
    """Document structure expressed as counts and heading text.

    Attributes:
        paragraph_count: Body paragraphs holding visible text. Empty spacing
            paragraphs and the paragraph an inline image sits in are excluded,
            so "at least five paragraphs" grades what a reader would count.
        word_count: Whitespace-separated words across body paragraphs and table
            cells, so a document whose content lives in a table is not reported
            as wordless.
        table_count: Body tables.
        section_count: Word sections, which carry page setup and headers.
        heading_count: Paragraphs styled ``Heading *``. Exact even when
            ``heading_texts`` is truncated.
        heading_texts: Heading text as a flat list, capped to the configured
            source content limit. Flat on purpose: ``$.heading_texts contains
            "Recommendation"`` is a single JSONPath match holding a list, while
            a multi-match path over ``headings[*].text`` changes the comparison
            semantics ``compare.actual_from_matches`` warns about.
        headings: The same headings with their outline levels, in document
            order, truncated alongside ``heading_texts``.
        image_count: Inline images.
        has_header: Whether any section header carries visible text.
        has_footer: Whether any section footer carries visible text.
    """

    paragraph_count: int
    word_count: int
    table_count: int
    section_count: int
    heading_count: int
    heading_texts: list[str]
    headings: list[DocumentHeading]
    image_count: int
    has_header: bool
    has_footer: bool


class InspectDocument(
    SourceCommand[DocxInspectDocumentInput, DocxInspectDocumentOutput]
):
    """Report headings, counts, images, and header/footer presence."""

    name = "inspect_document"
    input_model = DocxInspectDocumentInput
    output_model = DocxInspectDocumentOutput

    def run(
        self, source_input: DocxInspectDocumentInput, context: SourceContext
    ) -> DocxInspectDocumentOutput:
        """Runs DOCX structure inspection.

        Args:
            source_input: Validated document inspection input.
            context: Source runtime context.

        Returns:
            Document structure as counts and bounded heading text.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_docx_package(resolved, trusted=False)
        return inspect_docx_document(resolved, context.max_content_chars)


def inspect_docx_document(path: Path, limit: int) -> DocxInspectDocumentOutput:
    """Reads document structure and heading text.

    Args:
        path: Resolved DOCX file path.
        limit: Maximum number of heading characters to return.

    Returns:
        Document structure, with heading evidence capped to ``limit``.
    """
    from docx import Document

    doc = Document(str(path))
    paragraph_count = 0
    word_count = 0
    heading_count = 0
    heading_texts: list[str] = []
    headings: list[DocumentHeading] = []
    heading_total = 0

    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            paragraph_count += 1
            word_count += len(text.split())
        style_name = str(getattr(paragraph.style, "name", "") or "")
        if not text or not style_name.startswith("Heading"):
            continue
        heading_count += 1
        if heading_total >= limit:
            continue
        heading_total = append_limited(heading_texts, text, heading_total, limit)
        if heading_texts:
            headings.append(
                DocumentHeading(
                    text=heading_texts[-1], level=heading_level(style_name)
                )
            )

    for table in doc.tables:
        for paragraph, _table_style in _iter_table_paragraphs(table):
            word_count += len(paragraph.text.split())

    return DocxInspectDocumentOutput(
        paragraph_count=paragraph_count,
        word_count=word_count,
        table_count=len(doc.tables),
        section_count=len(doc.sections),
        heading_count=heading_count,
        heading_texts=heading_texts,
        headings=headings,
        image_count=len(doc.inline_shapes),
        has_header=any(
            has_visible_text(getattr(section, attribute))
            for section in doc.sections
            for attribute in ("header", "first_page_header", "even_page_header")
        ),
        has_footer=any(
            has_visible_text(getattr(section, attribute))
            for section in doc.sections
            for attribute in ("footer", "first_page_footer", "even_page_footer")
        ),
    )


def heading_level(style_name: str) -> int:
    """Reads the outline level from a ``Heading N`` style name.

    Args:
        style_name: Paragraph style name.

    Returns:
        The trailing number, or ``0`` for a heading style carrying none.
    """
    match = re.search(r"(\d+)\s*$", style_name)
    return int(match.group(1)) if match else 0


def _unique_table_cells(table: Any) -> list[Any]:
    """Return physical cells once even when a merged cell has several aliases."""
    cells: list[Any] = []
    # Hold the CT_Tc element itself, never id(): lxml element proxies are
    # transient, so once a row's proxies are freed their ids are recycled and a
    # later, distinct cell is skipped as "already seen" (measured under the
    # pinned python-docx 1.1.2: 3 of 8 cells kept on a 4x2 table, and the text
    # of one file varying between reads). Keeping the reference pins the proxy,
    # so identity is stable while merged-cell aliases still collapse to one cell.
    seen: list[Any] = []
    for row in table.rows:
        for cell in row.cells:
            tc = cell._tc
            if not any(tc is known for known in seen):
                seen.append(tc)
                cells.append(cell)
    return cells


def _table_style_context(
    table: Any, row_index: int, column_index: int
) -> dict[str, Any]:
    """Return base and applicable conditional table-style formatting."""
    from docx.oxml.ns import qn

    style = getattr(table, "style", None)
    look = table._tbl.tblPr.find(qn("w:tblLook"))

    def enabled(attribute: str, default: bool) -> bool:
        if look is None or look.get(qn(f"w:{attribute}")) is None:
            return default
        return look.get(qn(f"w:{attribute}")) not in {"0", "false", "off"}

    last_row = len(table.rows) - 1
    last_column = len(table.columns) - 1
    types: list[str] = []
    if row_index == 0 and enabled("firstRow", True):
        types.append("firstRow")
    if row_index == last_row and enabled("lastRow", False):
        types.append("lastRow")
    if column_index == 0 and enabled("firstColumn", True):
        types.append("firstCol")
    if column_index == last_column and enabled("lastColumn", False):
        types.append("lastCol")
    if not enabled("noHBand", False):
        types.append("band1Horz" if row_index % 2 == 0 else "band2Horz")
    if not enabled("noVBand", True):
        types.append("band1Vert" if column_index % 2 == 0 else "band2Vert")

    conditionals: list[Any] = []
    element = getattr(style, "_element", None)
    if element is not None:
        by_type = {
            child.get(qn("w:type")): child
            for child in element.findall(qn("w:tblStylePr"))
        }
        conditionals = [by_type[kind] for kind in types if kind in by_type]
    return {"style": style, "conditionals": conditionals}


def _iter_table_paragraphs(table: Any):
    """Yield paragraphs from a table in visible OOXML order."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    seen: list[Any] = []  # element references, not id() — see _unique_table_cells
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row.cells):
            tc = cell._tc
            if any(tc is known for known in seen):
                continue
            seen.append(tc)
            table_context = _table_style_context(table, row_index, column_index)
            for child in cell._tc.iterchildren():
                if isinstance(child, CT_P):
                    yield Paragraph(child, cell), table_context
                elif isinstance(child, CT_Tbl):
                    yield from _iter_table_paragraphs(Table(child, cell))


def _iter_container_paragraphs(container: Any):
    """Yield direct and recursively table-contained paragraphs."""
    for paragraph in container.paragraphs:
        yield paragraph, None
    for table in container.tables:
        yield from _iter_table_paragraphs(table)


def _iter_body_paragraphs_in_order(document: Any):
    """Yield body paragraphs, including nested tables, in package order."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document), None
        elif isinstance(child, CT_Tbl):
            yield from _iter_table_paragraphs(Table(child, document))


def _header_footer_containers(document: Any):
    """Yield every distinct default/first/even header and footer part once."""
    attributes = (
        "header",
        "first_page_header",
        "even_page_header",
        "footer",
        "first_page_footer",
        "even_page_footer",
    )
    seen: list[Any] = []  # element references, not id() — see _unique_table_cells
    for section in document.sections:
        for attribute in attributes:
            container = getattr(section, attribute)
            element = container._element
            if not any(element is known for known in seen):
                seen.append(element)
                yield container


def _iter_document_paragraphs(document: Any, *, body_order: bool = False):
    """Yield body and all header/footer variants without omitting tables."""
    body = (
        _iter_body_paragraphs_in_order(document)
        if body_order
        else _iter_container_paragraphs(document)
    )
    yield from body
    for container in _header_footer_containers(document):
        yield from _iter_container_paragraphs(container)


def _iter_tables(container: Any):
    """Yield top-level and nested tables from one document container."""
    for table in container.tables:
        yield table
        for cell in _unique_table_cells(table):
            yield from _iter_tables(cell)


def _all_document_tables(document: Any) -> list[Any]:
    """Collect body/header/footer tables, including nested tables."""
    tables = list(_iter_tables(document))
    for container in _header_footer_containers(document):
        tables.extend(_iter_tables(container))
    return tables


def has_visible_text(header_footer: Any) -> bool:
    """Whether a section header or footer carries visible text.

    A Word section always has a header and a footer object, so presence proves
    nothing; a letterhead or a page number is what the prompt actually means.

    Args:
        header_footer: python-docx header or footer object.

    Returns:
        True when any of its paragraphs holds non-whitespace text.
    """
    return any(
        paragraph.text.strip()
        for paragraph, _table_style in _iter_container_paragraphs(header_footer)
    )


def extract_docx(path: Path, limit: int) -> str:
    """Extracts body paragraph and table text from a DOCX file.

    Args:
        path: Resolved DOCX file path.
        limit: Maximum number of characters to return.

    Returns:
        Extracted text capped to ``limit`` characters.
    """
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []
    total = 0
    for paragraph, _table_style in _iter_document_paragraphs(doc, body_order=True):
        if not paragraph.text.strip():
            continue
        total = append_limited(parts, paragraph.text, total, limit)
        if total >= limit:
            break
    return "\n".join(parts)


def append_limited(parts: list[str], text: str, total: int, limit: int) -> int:
    """Append no more text than fits in the aggregate newline-joined budget."""
    separator = 1 if parts else 0
    remaining = max(0, limit - total - separator)
    if not remaining:
        return min(total, limit)
    bounded = text[:remaining]
    parts.append(bounded)
    return total + separator + len(bounded)


def _validate_docx_package(path: Path, *, trusted: bool) -> None:
    """Bound and classify candidate or trusted DOCX package failures."""
    from docx import Document

    error_type = SourceCapabilityError if trusted else SourceDataError
    kind = "trusted DOCX reference" if trusted else "DOCX artifact"
    try:
        if path.stat().st_size > MAX_DOCX_BYTES:
            raise ValueError(f"compressed package exceeds {MAX_DOCX_BYTES} bytes")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise ValueError(f"package has more than {MAX_DOCX_MEMBERS} members")
            expanded = 0
            xml_elements = 0
            content_elements = 0
            for member in members:
                if member.file_size > MAX_DOCX_MEMBER_BYTES:
                    raise ValueError(
                        f"package member exceeds {MAX_DOCX_MEMBER_BYTES} bytes"
                    )
                if member.file_size and not member.compress_size:
                    raise ValueError("package member has invalid compressed size")
                if (
                    member.compress_size
                    and member.file_size / member.compress_size
                    > MAX_DOCX_COMPRESSION_RATIO
                ):
                    raise ValueError("package member has suspicious compression ratio")
                actual = 0
                if not member.is_dir():
                    with archive.open(member) as stream:
                        while chunk := stream.read(1024 * 1024):
                            actual += len(chunk)
                            expanded += len(chunk)
                            if actual > MAX_DOCX_MEMBER_BYTES:
                                raise ValueError("package member exceeds its streamed limit")
                            if expanded > MAX_DOCX_EXPANDED_BYTES:
                                raise ValueError(
                                    f"expanded package exceeds {MAX_DOCX_EXPANDED_BYTES} bytes"
                                )
                if actual != member.file_size:
                    raise ValueError("package member size changed during expansion")
                if member.filename.lower().endswith((".xml", ".rels")):
                    from defusedxml.ElementTree import iterparse

                    with archive.open(member) as stream:
                        for event, element in iterparse(
                            stream, events=("start", "end")
                        ):
                            if event == "start":
                                xml_elements += 1
                                local_name = element.tag.rsplit("}", 1)[-1]
                                if local_name in {"p", "tbl", "tr", "tc"}:
                                    content_elements += 1
                                if xml_elements > MAX_DOCX_XML_ELEMENTS:
                                    raise ValueError("package XML is too structurally complex")
                                if content_elements > MAX_DOCX_CONTENT_ELEMENTS:
                                    raise ValueError("document content is too structurally complex")
                            else:
                                element.clear()
        Document(str(path))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise error_type(f"{kind} is unreadable: {path.name}: {exc}") from exc
    except Exception as exc:
        raise error_type(f"{kind} is unreadable: {path.name}: {exc}") from exc


class DocxCompareFilesInput(StrictModel):
    """Input for ``docx.compare_files``.

    Attributes:
        path: Workspace-relative DOCX path for the produced deliverable.
        expected_path: Verifier-private DOCX path for the gold reference.
        ignore_blanks: Collapse runs of whitespace before comparing, so a
            re-wrapped paragraph is not flagged.
        ignore_case: Lowercase both documents before comparing.
        ignore_order: Sort paragraphs before comparing, for deliverables whose
            paragraph order is unconstrained.
        content_only: Compare the flattened body text only and always report a
            fuzzy ratio, ignoring per-paragraph structure.
        fuzzy_match: Report a fuzzy similarity ratio in ``[0, 1]`` instead of an
            exact ``0``/``1`` verdict.
        delete_empty_lines: Drop empty paragraphs before comparing.
    """

    path: str
    expected_path: str
    ignore_blanks: bool = True
    ignore_case: bool = False
    ignore_order: bool = False
    content_only: bool = False
    fuzzy_match: bool = False
    delete_empty_lines: bool = False

    @field_validator("path", "expected_path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires each path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.compare_files requires .docx files")
        return value


class DocxCompareScoreOutput(StrictModel):
    """A comparison score with its pass/fail interpretation.

    Attributes:
        score: Similarity in ``[0, 1]``. Exact comparisons report ``1.0`` or
            ``0.0``; fuzzy comparisons report the ratio.
        match: Whether ``score`` equals ``1.0``, so a verifier can assert a hard
            pass without restating the threshold.
    """

    score: float
    match: bool


class CompareFiles(SourceCommand[DocxCompareFilesInput, DocxCompareScoreOutput]):
    """Compare a DOCX deliverable's text against a gold DOCX."""

    name = "compare_files"
    input_model = DocxCompareFilesInput
    output_model = DocxCompareScoreOutput

    def run(
        self, source_input: DocxCompareFilesInput, context: SourceContext
    ) -> DocxCompareScoreOutput:
        """Runs DOCX text comparison.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            The comparison score and its pass/fail verdict.
        """
        resolved = context.resolve_path(source_input.path)
        expected = context.resolve_trusted_reference_path(source_input.expected_path)
        _validate_docx_package(resolved, trusted=False)
        _validate_docx_package(expected, trusted=True)
        try:
            expected_text = _docx_paragraph_texts(
                expected, delete_empty_lines=False
            )
        except Exception as exc:
            raise SourceCapabilityError(
                f"trusted DOCX reference is unreadable: {expected.name}: {exc}"
            ) from exc
        if not any(text.strip() for text in expected_text):
            raise SourceCapabilityError(
                "trusted DOCX reference contains no comparable text"
            )
        try:
            score = compare_docx_files(
                resolved,
                expected,
                ignore_blanks=source_input.ignore_blanks,
                ignore_case=source_input.ignore_case,
                ignore_order=source_input.ignore_order,
                content_only=source_input.content_only,
                fuzzy_match=source_input.fuzzy_match,
                delete_empty_lines=source_input.delete_empty_lines,
            )
        except Exception as exc:
            raise SourceDataError(
                f"DOCX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return DocxCompareScoreOutput(score=score, match=score >= 1.0)


class DocxCompareTablesInput(StrictModel):
    """Input for ``docx.compare_tables``.

    Attributes:
        path: Workspace-relative DOCX path for the produced deliverable.
        expected_path: Verifier-private DOCX path for the gold reference.
        ignore_case_rows: Row indices whose cells compare case-insensitively,
            for header rows whose casing is unconstrained.
    """

    path: str
    expected_path: str
    ignore_case_rows: list[int] = Field(default_factory=list)

    @field_validator("ignore_case_rows")
    @classmethod
    def require_nonnegative_rows(cls, value: list[int]) -> list[int]:
        """Use explicit zero-based row indexes; negative aliases are unsupported."""
        if any(index < 0 for index in value):
            raise ValueError("ignore_case_rows must contain non-negative row indices")
        return value

    @field_validator("path", "expected_path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires each path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.compare_tables requires .docx files")
        return value


class CompareTables(SourceCommand[DocxCompareTablesInput, DocxCompareScoreOutput]):
    """Compare every table cell of a DOCX deliverable against a gold DOCX."""

    name = "compare_tables"
    input_model = DocxCompareTablesInput
    output_model = DocxCompareScoreOutput

    def run(
        self, source_input: DocxCompareTablesInput, context: SourceContext
    ) -> DocxCompareScoreOutput:
        """Runs DOCX table comparison.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            ``1.0`` when every table cell matches, else ``0.0``.
        """
        resolved = context.resolve_path(source_input.path)
        expected = context.resolve_trusted_reference_path(source_input.expected_path)
        _validate_docx_package(resolved, trusted=False)
        _validate_docx_package(expected, trusted=True)
        try:
            trusted_score = compare_docx_tables(
                expected, expected, ignore_case_rows=source_input.ignore_case_rows
            )
            if trusted_score < 1.0:
                raise SourceCapabilityError(
                    "trusted DOCX reference contains no comparable tables"
                )
        except SourceCapabilityError:
            raise
        except Exception as exc:
            raise SourceCapabilityError(
                f"trusted DOCX reference is unreadable: {expected.name}: {exc}"
            ) from exc
        try:
            score = compare_docx_tables(
                resolved, expected, ignore_case_rows=source_input.ignore_case_rows
            )
        except Exception as exc:
            raise SourceDataError(
                f"DOCX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return DocxCompareScoreOutput(score=score, match=score >= 1.0)


class DocxCheckFontNamesInput(StrictModel):
    """Input for ``docx.check_font_names``.

    Attributes:
        path: Workspace-relative DOCX path.
        font_name: Font name every run must carry.
    """

    path: str
    font_name: str

    @field_validator("path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires the path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.check_font_names requires a .docx file")
        return value


class CheckFontNames(SourceCommand[DocxCheckFontNamesInput, DocxCompareScoreOutput]):
    """Check every run in a DOCX carries the required font name."""

    name = "check_font_names"
    input_model = DocxCheckFontNamesInput
    output_model = DocxCompareScoreOutput

    def run(
        self, source_input: DocxCheckFontNamesInput, context: SourceContext
    ) -> DocxCompareScoreOutput:
        """Runs the DOCX font-name check.

        Args:
            source_input: Validated font-name input.
            context: Source runtime context.

        Returns:
            ``1.0`` when every run uses ``font_name``, else ``0.0``.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_docx_package(resolved, trusted=False)
        try:
            score = compare_font_names(resolved, source_input.font_name)
        except Exception as exc:
            raise SourceDataError(
                f"DOCX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return DocxCompareScoreOutput(score=score, match=score >= 1.0)


class DocxCompareLineSpacingInput(StrictModel):
    """Input for ``docx.compare_line_spacing``.

    Attributes:
        path: Workspace-relative DOCX path for the produced deliverable.
        expected_path: Verifier-private DOCX path for the gold reference.
    """

    path: str
    expected_path: str

    @field_validator("path", "expected_path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires each path to target a DOCX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a DOCX extension.

        Raises:
            ValueError: If the path does not end in ``.docx``.
        """
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.compare_line_spacing requires .docx files")
        return value


class CompareLineSpacing(
    SourceCommand[DocxCompareLineSpacingInput, DocxCompareScoreOutput]
):
    """Compare per-paragraph line spacing of a DOCX deliverable against a gold DOCX."""

    name = "compare_line_spacing"
    input_model = DocxCompareLineSpacingInput
    output_model = DocxCompareScoreOutput

    def run(
        self, source_input: DocxCompareLineSpacingInput, context: SourceContext
    ) -> DocxCompareScoreOutput:
        """Runs the DOCX line-spacing comparison.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            ``1.0`` when text and per-paragraph line spacing match, else ``0.0``.
        """
        resolved = context.resolve_path(source_input.path)
        expected = context.resolve_trusted_reference_path(source_input.expected_path)
        _validate_docx_package(resolved, trusted=False)
        _validate_docx_package(expected, trusted=True)
        try:
            trusted_score = compare_line_spacing(expected, expected)
            if trusted_score < 1.0:
                raise SourceCapabilityError(
                    "trusted DOCX reference contains no comparable line spacing"
                )
        except SourceCapabilityError:
            raise
        except Exception as exc:
            raise SourceCapabilityError(
                f"trusted DOCX reference is unreadable: {expected.name}: {exc}"
            ) from exc
        try:
            score = compare_line_spacing(resolved, expected)
        except Exception as exc:
            raise SourceDataError(
                f"DOCX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return DocxCompareScoreOutput(score=score, match=score >= 1.0)


def _docx_paragraph_texts(
    path: Path,
    *,
    delete_empty_lines: bool,
) -> list[str]:
    """Reads a DOCX's paragraph texts with optional ordering/blank handling.

    Args:
        path: Resolved DOCX file path.
        delete_empty_lines: Drop blank paragraphs before returning.

    Returns:
        The document's paragraph texts.
    """
    from docx import Document

    paragraphs = [
        paragraph.text
        for paragraph, _table_style in _iter_document_paragraphs(
            Document(str(path)), body_order=True
        )
    ]
    if delete_empty_lines:
        paragraphs = [p for p in paragraphs if p.strip()]
    return paragraphs


def compare_docx_files(
    path: Path,
    expected_path: Path,
    *,
    ignore_blanks: bool = True,
    ignore_case: bool = False,
    ignore_order: bool = False,
    content_only: bool = False,
    fuzzy_match: bool = False,
    delete_empty_lines: bool = False,
) -> float:
    """Compares the body text of two DOCX files.

    Args:
        path: Resolved deliverable DOCX path.
        expected_path: Resolved gold DOCX path.
        ignore_blanks: Collapse whitespace runs before comparing.
        ignore_case: Lowercase both documents before comparing.
        ignore_order: Sort paragraphs before comparing.
        content_only: Compare flattened body text with a fuzzy ratio.
        fuzzy_match: Report a fuzzy ratio instead of an exact verdict.
        delete_empty_lines: Drop empty paragraphs before comparing.

    Returns:
        A similarity score in ``[0, 1]``.
    """
    from rapidfuzz import fuzz

    doc1 = _docx_paragraph_texts(
        path, delete_empty_lines=delete_empty_lines
    )
    doc2 = _docx_paragraph_texts(
        expected_path, delete_empty_lines=delete_empty_lines
    )

    if ignore_order:
        def normalized_key(text: str) -> str:
            if ignore_blanks or content_only:
                text = re.sub(r"\s+", " ", text).strip()
            return text.casefold() if ignore_case else text

        doc1 = sorted(doc1, key=normalized_key)
        doc2 = sorted(doc2, key=normalized_key)

    if content_only:
        text1 = re.sub(r"\s+", " ", "\n".join(doc1)).strip()
        text2 = re.sub(r"\s+", " ", "\n".join(doc2)).strip()
        if ignore_case:
            text1, text2 = text1.casefold(), text2.casefold()
        return fuzz.ratio(text1, text2) / 100.0

    if ignore_blanks:
        text1 = re.sub(r"\s+", " ", "\n".join(doc1)).strip()
        text2 = re.sub(r"\s+", " ", "\n".join(doc2)).strip()
        if ignore_case:
            text1, text2 = text1.casefold(), text2.casefold()
        if fuzzy_match:
            return fuzz.ratio(text1, text2) / 100.0
        return 1.0 if text1 == text2 else 0.0

    if len(doc1) != len(doc2):
        return 0.0
    if not doc1:
        return 1.0
    if fuzzy_match:
        total = 0.0
        for p1, p2 in zip(doc1, doc2, strict=True):
            if ignore_case:
                p1, p2 = p1.casefold(), p2.casefold()
            total += fuzz.ratio(p1, p2) / 100.0
        return total / len(doc1)
    for p1, p2 in zip(doc1, doc2, strict=True):
        if ignore_case:
            p1, p2 = p1.casefold(), p2.casefold()
        if p1 != p2:
            return 0.0
    return 1.0


def compare_docx_tables(
    path: Path, expected_path: Path, *, ignore_case_rows: list[int] | None = None
) -> float:
    """Compares every table cell of two DOCX files.

    Args:
        path: Resolved deliverable DOCX path.
        expected_path: Resolved gold DOCX path.
        ignore_case_rows: Row indices compared case-insensitively.

    Returns:
        ``1.0`` when table count, shape, and every cell match, else ``0.0``.
    """
    from docx import Document

    tables1 = _all_document_tables(Document(str(path)))
    tables2 = _all_document_tables(Document(str(expected_path)))
    if not tables1 or len(tables1) != len(tables2):
        return 0.0

    ignore_rows = set(ignore_case_rows or [])
    compared_cells = 0
    for table1, table2 in zip(tables1, tables2, strict=True):
        if len(table1.rows) != len(table2.rows) or len(table1.columns) != len(
            table2.columns
        ):
            return 0.0
        for row_index in range(len(table1.rows)):
            for column_index in range(len(table1.columns)):
                text1 = table1.cell(row_index, column_index).text.strip()
                text2 = table2.cell(row_index, column_index).text.strip()
                if row_index in ignore_rows:
                    text1, text2 = text1.casefold(), text2.casefold()
                if text1 != text2:
                    return 0.0
                compared_cells += 1
    return 1.0 if compared_cells else 0.0


def _style_chain(style: Any):
    """Yield a style and each base style once."""
    seen: set[str] = set()
    current = style
    while current is not None:
        style_id = str(getattr(current, "style_id", id(current)))
        if style_id in seen:
            break
        seen.add(style_id)
        yield current
        current = getattr(current, "base_style", None)


def _rfonts_spec(element: Any) -> tuple[str, str] | None:
    """Read a literal or theme font declaration from an OOXML element."""
    from docx.oxml.ns import qn

    rpr = getattr(element, "rPr", None)
    if rpr is None:
        rpr = element.find(qn("w:rPr"))
    if rpr is None:
        return None
    rfonts = getattr(rpr, "rFonts", None)
    if rfonts is None:
        rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        value = rfonts.get(qn(f"w:{attribute}"))
        if value:
            return "literal", value
    for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        value = rfonts.get(qn(f"w:{attribute}"))
        if value:
            return "theme", value
    return None


def _theme_font(document: Any, token: str) -> str | None:
    """Resolve major/minor theme tokens to the theme's Latin typeface."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import parse_xml

    try:
        theme = document.part.part_related_by(RT.THEME)
        root = parse_xml(theme.blob)
        family = "majorFont" if token.casefold().startswith("major") else "minorFont"
        drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
        fonts = root.findall(
            f".//{{{drawing}}}fontScheme/{{{drawing}}}{family}/{{{drawing}}}latin"
        )
        return fonts[0].get("typeface") if fonts else None
    except (AttributeError, KeyError):
        return None


def _font_from_owner(document: Any, owner: Any) -> str | None:
    """Resolve one run/style font declaration, including a theme token."""
    direct = getattr(getattr(owner, "font", None), "name", None)
    if direct:
        return direct
    element = getattr(owner, "_element", owner)
    spec = _rfonts_spec(element) if element is not None else None
    if spec is None:
        return None
    kind, value = spec
    return value if kind == "literal" else _theme_font(document, value)


def _document_default_font(document: Any) -> str | None:
    """Read the run-properties default, falling back to the Normal style."""
    for style in _style_chain(document.styles["Normal"]):
        if value := _font_from_owner(document, style):
            return value
    from docx.oxml.ns import qn

    defaults = document.styles.element.find(qn("w:docDefaults"))
    run_defaults = (
        defaults.find(qn("w:rPrDefault")) if defaults is not None else None
    )
    spec = _rfonts_spec(run_defaults) if run_defaults is not None else None
    if spec is None:
        return None
    kind, value = spec
    return value if kind == "literal" else _theme_font(document, value)


def _effective_font(
    document: Any, run: Any, paragraph: Any, table_style: Any
) -> str | None:
    """Resolve direct, character, paragraph, table, and document font styles."""
    if value := _font_from_owner(document, run):
        return value
    for style in _style_chain(getattr(run, "style", None)):
        if value := _font_from_owner(document, style):
            return value
    for style in _style_chain(getattr(paragraph, "style", None)):
        if value := _font_from_owner(document, style):
            return value
    if isinstance(table_style, dict):
        for conditional in table_style.get("conditionals", []):
            if value := _font_from_owner(document, conditional):
                return value
        table_style = table_style.get("style")
    for style in _style_chain(table_style):
        if value := _font_from_owner(document, style):
            return value
    return _document_default_font(document)


def compare_font_names(path: Path, font_name: str) -> float:
    """Check the effective font of every substantive visible DOCX run."""
    from docx import Document

    document = Document(str(path))
    expected = font_name.casefold()
    inspected = 0
    for paragraph, table_style in _iter_document_paragraphs(
        document, body_order=True
    ):
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            inspected += 1
            actual = _effective_font(document, run, paragraph, table_style)
            if actual is None or actual.casefold() != expected:
                return 0.0
    return 1.0 if inspected else 0.0


def _spacing_value(paragraph_format: Any) -> tuple[str, float] | None:
    """Canonicalize direct line spacing as a multiple or rule plus twips."""
    from docx.enum.text import WD_LINE_SPACING

    spacing = paragraph_format.line_spacing
    rule = paragraph_format.line_spacing_rule
    if isinstance(spacing, float):
        return "multiple", round(spacing, 6)
    if spacing is not None:
        rules = {
            WD_LINE_SPACING.EXACTLY: "exact",
            WD_LINE_SPACING.AT_LEAST: "atLeast",
        }
        return rules.get(rule, "exact"), float(spacing.twips)
    aliases = {
        WD_LINE_SPACING.SINGLE: 1.0,
        WD_LINE_SPACING.ONE_POINT_FIVE: 1.5,
        WD_LINE_SPACING.DOUBLE: 2.0,
    }
    if rule in aliases:
        return "multiple", aliases[rule]
    return None


def _xml_spacing(owner: Any) -> tuple[str, float] | None:
    """Read a paragraph-spacing declaration from a style-like XML owner."""
    from docx.oxml.ns import qn

    element = getattr(owner, "_element", getattr(owner, "element", owner))
    if element is None:
        return None
    ppr = element.find(qn("w:pPr"))
    spacing = ppr.find(qn("w:spacing")) if ppr is not None else None
    if spacing is None or spacing.get(qn("w:line")) is None:
        return None
    line = float(spacing.get(qn("w:line")))
    rule = spacing.get(qn("w:lineRule"), "auto")
    return (
        ("multiple", round(line / 240.0, 6))
        if rule == "auto"
        else (rule, line)
    )


def _default_xml_spacing(document: Any) -> tuple[str, float] | None:
    """Read line spacing from Word's document-default paragraph properties."""
    from docx.oxml.ns import qn

    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is None:
        return None
    for paragraph_defaults in defaults.findall(qn("w:pPrDefault")):
        if value := _xml_spacing(paragraph_defaults):
            return value
    return None


def _effective_line_spacing(
    document: Any, paragraph: Any, table_style: Any
) -> tuple[str, float]:
    """Resolve direct, paragraph, conditional-table, base-table, and defaults."""
    if value := _spacing_value(paragraph.paragraph_format):
        return value
    for style in _style_chain(getattr(paragraph, "style", None)):
        if value := _spacing_value(style.paragraph_format):
            return value
    if isinstance(table_style, dict):
        for conditional in table_style.get("conditionals", []):
            if value := _xml_spacing(conditional):
                return value
        table_style = table_style.get("style")
    for style in _style_chain(table_style):
        if value := _xml_spacing(style):
            return value
    if value := _default_xml_spacing(document):
        return value
    return "multiple", 1.0


def _paragraph_spacing_records(document: Any) -> list[tuple[str, tuple[str, float]]]:
    """Pair normalized visible paragraph text with its effective spacing."""
    records: list[tuple[str, tuple[str, float]]] = []
    for paragraph, table_style in _iter_document_paragraphs(
        document, body_order=True
    ):
        text = re.sub(r"\s+", " ", paragraph.text).strip()
        if text:
            records.append(
                (text, _effective_line_spacing(document, paragraph, table_style))
            )
    return records


def compare_line_spacing(path: Path, expected_path: Path) -> float:
    """Compare aligned visible paragraph text and effective line spacing."""
    from docx import Document

    records1 = _paragraph_spacing_records(Document(str(path)))
    records2 = _paragraph_spacing_records(Document(str(expected_path)))
    if not records1 or len(records1) != len(records2):
        return 0.0
    return 1.0 if records1 == records2 else 0.0


class DocxReadTableInput(StrictModel):
    """Input for ``docx.read_table``.

    Attributes:
        path: Workspace-relative DOCX path.
        table_index: Zero-based index into the body tables in document order;
            ``None`` searches every body table (first match wins).
        header_match: Optional regex tried against each candidate row rendered
            as ``cell|cell|...``; the first matching row is the header and the
            rows after it in that table are the data. Without it the first row
            of the selected table is the header.
        columns: Optional positional column names. When given they key the
            grid columns instead of the header text, and a header row is only
            consumed when ``header_match`` matches (so a table with no header
            row still reads). Grid columns beyond ``columns`` keep the header
            text as their key.
    """

    path: str
    table_index: int | None = None
    header_match: str | None = None
    columns: list[str] | None = None

    @field_validator("path")
    @classmethod
    def require_docx_extension(cls, value: str) -> str:
        """Requires the path to target a DOCX file."""
        if Path(value).suffix.lower() != ".docx":
            raise ValueError("docx.read_table requires a .docx file")
        return value

    @field_validator("table_index")
    @classmethod
    def require_nonnegative_index(cls, value: int | None) -> int | None:
        """Use explicit zero-based table indexes; negative aliases are unsupported."""
        if value is not None and value < 0:
            raise ValueError("table_index must be a non-negative integer")
        return value

    @field_validator("header_match")
    @classmethod
    def require_valid_regex(cls, value: str | None) -> str | None:
        """The header pattern must compile."""
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"header_match is not a valid regex: {exc}") from exc
        return value


class DocxReadTableOutput(RootModel[list[dict[str, str]]]):
    """One header-keyed mapping per data row, in table order.

    The root is the row list itself, so ``compare.table_equals`` grades it the
    way it grades ``csv.read_rows`` output, and a JSONPath addresses a cell
    directly: ``$[?(@.category=='Pre-award (Pre)')].findings``.
    """


def _grid_cells(row: Any) -> list[tuple[Any, bool]]:
    """Grid cells of a row as ``(cell, is_alias)`` — an alias is a later grid
    column of a horizontally merged cell (python-docx repeats the proxy).
    Identity is the ``CT_Tc`` element, never ``id()`` (see ``_unique_table_cells``)."""
    out: list[tuple[Any, bool]] = []
    seen: list[Any] = []
    for cell in row.cells:
        tc = cell._tc
        alias = any(tc is known for known in seen)
        if not alias:
            seen.append(tc)
        out.append((cell, alias))
    return out


def _row_text(row: Any) -> str:
    return "|".join(cell.text.strip() for cell, alias in _grid_cells(row) if not alias)


def read_docx_table(
    path: Path,
    *,
    table_index: int | None,
    header_match: str | None,
    columns: list[str] | None,
) -> list[dict[str, str]]:
    """Read one DOCX table as header-keyed rows (see :class:`ReadTable`)."""
    from docx import Document

    document = Document(str(path))
    tables = list(document.tables)
    if not tables:
        raise SourceDataError(f"DOCX artifact has no table: {path.name}")
    if table_index is not None:
        if table_index >= len(tables):
            raise SourceDataError(
                f"DOCX artifact has {len(tables)} table(s), no table_index {table_index}: {path.name}"
            )
        tables = [tables[table_index]]

    pattern = re.compile(header_match) if header_match else None
    chosen: Any = None
    header_row: int | None = None
    if pattern is not None:
        for table in tables:
            for index, row in enumerate(table.rows):
                if pattern.search(_row_text(row)):
                    chosen, header_row = table, index
                    break
            if chosen is not None:
                break
        if chosen is None and columns is None:
            raise SourceDataError(
                f"no table row matches header_match {header_match!r}: {path.name}"
            )
    if chosen is None:
        chosen = tables[0]
        header_row = None if columns is not None else 0

    rows = list(chosen.rows)
    keys: list[str] = []
    if header_row is not None:
        for grid_index, (cell, alias) in enumerate(_grid_cells(rows[header_row])):
            text = cell.text.strip()
            keys.append(text if not alias else f"{text} ({grid_index + 1})")
    if columns is not None:
        keys = list(columns) + keys[len(columns):]
    data = rows[header_row + 1:] if header_row is not None else rows
    if not keys and data:
        keys = [f"column_{i + 1}" for i in range(len(data[0].cells))]

    result: list[dict[str, str]] = []
    for row in data:
        cells = _grid_cells(row)
        if not any(cell.text.strip() for cell, alias in cells if not alias):
            # An all-blank row carries no fact (a pre-allocated spacer); skip it
            # the way ``csv.read_rows`` skips a blank line, so a population lock
            # does not name '' as an extra row.
            continue
        record: dict[str, str] = {}
        for grid_index, (cell, alias) in enumerate(cells):
            if alias:
                continue
            key = keys[grid_index] if grid_index < len(keys) else f"column_{grid_index + 1}"
            record[key] = cell.text
        for key in keys[len(cells):]:
            record[key] = ""
        result.append(record)
    return result


class ReadTable(SourceCommand[DocxReadTableInput, DocxReadTableOutput]):
    """Read one DOCX table as header-keyed rows for ``compare.table_equals``.

    A table in a memo is a keyed fact — this category has that many findings
    — not a rendering, so the check should address a cell by column name and
    row id, tolerate a reworded header, an extra column, reordered rows, a
    merged title row or a preceding table, and name the failing cell. This is
    the DOCX counterpart of ``csv.read_rows``: it yields rows and leaves every
    equivalence decision (typed cells, casefold, population and column locks)
    to the comparator, and it needs no verifier-private reference file.
    """

    name = "read_table"
    input_model = DocxReadTableInput
    output_model = DocxReadTableOutput

    def run(
        self, source_input: DocxReadTableInput, context: SourceContext
    ) -> DocxReadTableOutput:
        resolved = context.resolve_path(source_input.path)
        _validate_docx_package(resolved, trusted=False)
        try:
            rows = read_docx_table(
                resolved,
                table_index=source_input.table_index,
                header_match=source_input.header_match,
                columns=source_input.columns,
            )
        except SourceDataError:
            raise
        except Exception as exc:
            raise SourceDataError(
                f"DOCX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return DocxReadTableOutput(rows)


COMMANDS = (
    ExtractText(),
    InspectDocument(),
    CompareFiles(),
    CompareTables(),
    CheckFontNames(),
    CompareLineSpacing(),
    ReadTable(),
)
