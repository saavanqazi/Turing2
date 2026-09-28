import json
import operator
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from ..models import StrictModel
from ..source_types import SourceCommand, SourceContext, SourceDataError

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 1_000
MAX_PDF_EXTRACTED_CHARS = 5_000_000
PDF_WORKER_TIMEOUT_SECONDS = 45
PDF_WORKER_MEMORY_BYTES = 768 * 1024 * 1024

_PDF_TEXT_WORKER = r'''\
import json
import os
import resource
import sys


def set_limit(kind, value):
    soft, hard = resource.getrlimit(kind)
    bounded = value if hard == resource.RLIM_INFINITY else min(value, hard)
    if soft > bounded:
        resource.setrlimit(kind, (bounded, hard))
    resource.setrlimit(kind, (bounded, bounded))


try:
    # macOS exposes RLIMIT_AS but rejects lowering it; production grading is
    # Linux, where this is the hard parser-memory boundary.
    if sys.platform != "darwin":
        set_limit(resource.RLIMIT_AS, int(sys.argv[5]))
    set_limit(resource.RLIMIT_CPU, 30)
    set_limit(resource.RLIMIT_FSIZE, 0)
    set_limit(resource.RLIMIT_NOFILE, 64)
    set_limit(resource.RLIMIT_CORE, 0)
    import fitz  # pyright: ignore[reportMissingImports]

    path, mode = sys.argv[1], sys.argv[2]
    limit, aggregate_limit = int(sys.argv[3]), int(sys.argv[4])
    label_stem = sys.argv[6] if len(sys.argv) > 6 else ""
    if os.stat(path).st_size > 64 * 1024 * 1024:
        raise ValueError("PDF artifact exceeds the file-size limit")
    document = fitz.open(path)
    if not document.is_pdf or document.needs_pass or not len(document):
        raise ValueError("unsupported PDF document")
    if len(document) > 1000:
        raise ValueError("PDF page count exceeds limit")
    page_count = len(document)
    total_extracted = 0
    parts = []
    answers = []
    returned = 0
    truncated = False
    character_count = 0
    hits = []
    if mode == "labels":
        import re as _re
        number = r"(?<![\d.])(-?\d[\d,]*(?:\.\d+)?)(?!\.?\d)"
        hedge = r"\s*\(?\s*(?:or|possibly|alternatively|either|maybe|perhaps|~|about|approx\.?)\s+" + number
        label_re = _re.compile(
            r"(?i)\b(" + _re.escape(label_stem) + r"\w*(?:[ \t]+[A-Za-z]\w*){0,2}?)[ \t]*[:=\-\u2013\u2014]?[ \t]*\n?[ \t]*" + number
        )
        hedge_re = _re.compile(hedge, _re.I)
        tail_re = _re.compile(r"(?i)\b" + _re.escape(label_stem) + r"\w*(?:[ \t]+[A-Za-z]\w*){0,2}?[ \t]*[:=]\s*$")
        head_re = _re.compile(r"^\s*" + number)
        for page_index, page in enumerate(document):
            blocks = [b for b in page.get_text("blocks", sort=False) if len(b) <= 6 or b[6] == 0]
            for block_index, block in enumerate(blocks):
                text = block[4] or ""
                # "Label:" alone at the end of a block with the number opening the
                # next block (a paragraph break between label and value).
                if block_index + 1 < len(blocks) and tail_re.search(text):
                    nxt = blocks[block_index + 1][4] or ""
                    if head_re.match(nxt):
                        text = text.rstrip() + " " + nxt
                total_extracted += len(text)
                if total_extracted > aggregate_limit:
                    raise ValueError("PDF extractable text exceeds the supported limit")
                for m in label_re.finditer(text):
                    line_start = text.rfind("\n", 0, m.start()) + 1
                    line_end = text.find("\n", m.end())
                    line_end = len(text) if line_end < 0 else line_end
                    alternatives = []
                    tail = text[m.end():line_end]
                    for hm in hedge_re.finditer(tail):
                        alternatives.append(float(hm.group(1).replace(",", "")))
                    hits.append({
                        "label": m.group(1).strip(), "value": float(m.group(2).replace(",", "")),
                        "value_text": m.group(2), "alternatives": alternatives,
                        "page": page_index + 1, "block": block_index,
                        "line": text[line_start:line_end].strip()[:limit or 500],
                    })
    for page in document:
        if mode in ("pages", "labels"):
            continue
        # sort=False keeps each text block (column) whole in content-stream order;
        # sort=True interleaves side-by-side columns line by line.
        text = page.get_text("text", sort=False) or ""
        total_extracted += len(text)
        if total_extracted > aggregate_limit:
            raise ValueError("PDF extractable text exceeds the supported limit")
        if mode == "inspect":
            character_count += len(text.strip())
            continue
        if mode == "extract":
            text = text.strip()
            if not text:
                continue
            separator = 1 if parts else 0
            remaining = max(0, limit - returned - separator)
            if not remaining:
                truncated = True
                break
            bounded = text[:remaining]
            parts.append(bounded)
            returned += separator + len(bounded)
            if len(bounded) < len(text):
                truncated = True
                break
            continue
        for line in text.splitlines():
            if "=" not in line:
                continue
            answer = line.rsplit("=", 1)[-1].strip()
            separator = 1 if answers else 0
            remaining = max(0, limit - returned - separator)
            if not remaining:
                truncated = True
                break
            bounded = answer[:remaining]
            answers.append(bounded)
            returned += separator + len(bounded)
            if len(bounded) < len(answer):
                truncated = True
                break
        if truncated:
            break
    document.close()
    if mode == "inspect":
        result = {"page_count": page_count, "character_count": character_count}
    elif mode == "pages":
        result = {"page_count": page_count}
    elif mode == "extract":
        result = {"text": "\n".join(parts), "truncated": truncated}
    elif mode == "labels":
        first = hits[0] if hits else None
        distinct = sorted({h["value"] for h in hits})
        result = {
            "found": bool(hits),
            "label": first["label"] if first else None,
            "value": first["value"] if first else None,
            "alternatives": sorted({a for h in hits for a in h["alternatives"]}),
            "page": first["page"] if first else None,
            "block": first["block"] if first else None,
            "line": first["line"] if first else None,
            "occurrences": len(hits),
            "consistent": len(distinct) <= 1,
            "all_values": distinct,
        }
    else:
        result = {"answers": answers, "truncated": truncated}
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
except BaseException as exc:
    try:
        print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}))
    except BaseException:
        pass
    raise SystemExit(2)
'''


def _run_pdf_text_worker(
    path: Path, mode: str, limit: int, label: str = ""
) -> dict[str, Any]:
    """Parse an untrusted PDF in a memory- and CPU-limited process."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SourceDataError(f"could not inspect PDF {path.name}") from exc
    if size == 0:
        raise SourceDataError("PDF artifact is empty")
    if size > MAX_PDF_BYTES:
        raise SourceDataError(f"PDF artifact exceeds {MAX_PDF_BYTES} bytes")
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _PDF_TEXT_WORKER,
                str(path),
                mode,
                str(max(0, limit)),
                str(MAX_PDF_EXTRACTED_CHARS),
                str(PDF_WORKER_MEMORY_BYTES),
                label,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PDF_WORKER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceDataError("PDF text extraction exceeded its CPU-time budget") from exc
    if len(process.stdout) > MAX_PDF_EXTRACTED_CHARS * 4 + 1_000_000:
        raise SourceDataError("PDF text extraction returned excessive evidence")
    try:
        payload = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SourceDataError(
            "PDF text extraction failed inside its resource sandbox"
        ) from exc
    if process.returncode != 0 or not payload.get("ok"):
        detail = str(payload.get("error", "worker exited unexpectedly"))[:500]
        raise SourceDataError(f"malformed or excessive PDF text: {detail}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SourceDataError("PDF text extraction returned malformed evidence")
    return result


class PdfExtractTextInput(StrictModel):
    """Input for pdf.extract_text.

    Attributes:
        path: Workspace-relative PDF path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        """Requires the path to target a PDF file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PDF extension.

        Raises:
            ValueError: If the path does not end in ``.pdf``.
        """
        if Path(value).suffix.lower() != ".pdf":
            raise ValueError("pdf.extract_text requires a .pdf file")
        return value


class PdfExtractTextOutput(StrictModel):
    """Output from pdf.extract_text.

    Attributes:
        text: Extracted page text.
        truncated: Whether additional nonempty text was omitted by the evidence cap.
    """

    text: str
    truncated: bool = False


class ExtractText(SourceCommand[PdfExtractTextInput, PdfExtractTextOutput]):
    """Extracts text from PDF pages."""

    name = "extract_text"
    input_model = PdfExtractTextInput
    output_model = PdfExtractTextOutput

    def run(self, source_input: PdfExtractTextInput, context: SourceContext) -> PdfExtractTextOutput:
        """Runs PDF text extraction.

        Args:
            source_input: Validated PDF extraction input.
            context: Source runtime context.

        Returns:
            Extracted PDF text.
        """
        resolved = context.resolve_path(source_input.path)
        text, truncated = extract_pdf_text_bounded(
            resolved, context.max_content_chars
        )
        return PdfExtractTextOutput(text=text, truncated=truncated)


class PdfInspectDocumentInput(StrictModel):
    """Input for ``pdf.inspect_document``.

    Attributes:
        path: Workspace-relative PDF path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        """Requires the path to target a PDF file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PDF extension.

        Raises:
            ValueError: If the path does not end in ``.pdf``.
        """
        if Path(value).suffix.lower() != ".pdf":
            raise ValueError("pdf.inspect_document requires a .pdf file")
        return value


class PdfInspectDocumentOutput(StrictModel):
    """PDF structure expressed as counts.

    Attributes:
        page_count: Pages in the document, which is what a prompt constraining
            a deliverable to "no more than two pages" actually states.
        character_count: Extractable text characters across every page. Not
            capped by the source content limit, because this command returns
            counts rather than the text itself.
        has_text: Whether any page yields extractable text. A scanned or
            image-only PDF reports False, which distinguishes "the model
            delivered an image" from "the content is wrong".
    """

    page_count: int
    character_count: int
    has_text: bool


class InspectDocument(SourceCommand[PdfInspectDocumentInput, PdfInspectDocumentOutput]):
    """Report a PDF's page count and whether it carries extractable text."""

    name = "inspect_document"
    input_model = PdfInspectDocumentInput
    output_model = PdfInspectDocumentOutput

    def run(
        self, source_input: PdfInspectDocumentInput, context: SourceContext
    ) -> PdfInspectDocumentOutput:
        """Runs PDF structure inspection.

        Args:
            source_input: Validated document inspection input.
            context: Source runtime context.

        Returns:
            PDF structure as counts.
        """
        resolved = context.resolve_path(source_input.path)
        return inspect_pdf_document(resolved)


def inspect_pdf_document(path: Path) -> PdfInspectDocumentOutput:
    """Reads a PDF's page count and extractable-text volume.

    Args:
        path: Resolved PDF file path.

    Returns:
        PDF structure as counts.
    """
    result = _run_pdf_text_worker(path, "inspect", 0)
    page_count = int(result["page_count"])
    character_count = int(result["character_count"])
    return PdfInspectDocumentOutput(
        page_count=page_count,
        character_count=character_count,
        has_text=character_count > 0,
    )


def extract_pdf_text_bounded(path: Path, limit: int) -> tuple[str, bool]:
    """Return bounded text plus an explicit partial-evidence indicator."""
    result = _run_pdf_text_worker(path, "extract", limit)
    return str(result["text"]), bool(result["truncated"])


def extract_pdf_text(path: Path, limit: int) -> str:
    """Extracts text from a PDF file.

    Args:
        path: Resolved PDF file path.
        limit: Maximum number of characters to return.

    Returns:
        Extracted text capped to ``limit`` characters.
    """
    return extract_pdf_text_bounded(path, limit)[0]


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
    separator = 1 if parts else 0
    remaining = max(0, limit - total - separator)
    if not remaining:
        return min(total, limit)
    bounded = text[:remaining]
    parts.append(bounded)
    return total + separator + len(bounded)


_PAGE_RELATIONS: dict[str, str] = {
    "eq": "eq",
    "==": "eq",
    "ne": "ne",
    "!=": "ne",
    "lt": "lt",
    "<": "lt",
    "le": "le",
    "<=": "le",
    "gt": "gt",
    ">": "gt",
    "ge": "ge",
    ">=": "ge",
}


class PdfCheckPagesInput(StrictModel):
    """Input for ``pdf.check_pages``.

    Attributes:
        path: Workspace-relative PDF path.
        relation: Comparison operator applied as ``page_count <relation>
            ref_value``. Accepts operator words (``eq``, ``le``) or symbols
            (``==``, ``<=``).
        ref_value: The page count to compare against.
    """

    path: str
    relation: Literal["eq", "==", "ne", "!=", "lt", "<", "le", "<=", "gt", ">", "ge", ">="]
    ref_value: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        """Requires the path to target a PDF file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PDF extension.

        Raises:
            ValueError: If the path does not end in ``.pdf``.
        """
        if Path(value).suffix.lower() != ".pdf":
            raise ValueError("pdf.check_pages requires a .pdf file")
        return value


class PdfCheckPagesOutput(StrictModel):
    """Result of comparing a PDF page count to a reference.

    Attributes:
        page_count: Pages in the document.
        match: Whether the page count satisfies the relation. Boolean rather
            than a score because a page-count constraint is inherently binary.
    """

    page_count: int
    match: bool


class CheckPages(SourceCommand[PdfCheckPagesInput, PdfCheckPagesOutput]):
    """Check a PDF's page count against a reference value and relation."""

    name = "check_pages"
    input_model = PdfCheckPagesInput
    output_model = PdfCheckPagesOutput

    def run(
        self, source_input: PdfCheckPagesInput, context: SourceContext
    ) -> PdfCheckPagesOutput:
        """Runs the PDF page-count check.

        Args:
            source_input: Validated page-count input.
            context: Source runtime context.

        Returns:
            The page count and whether it satisfies the relation.
        """
        resolved = context.resolve_path(source_input.path)
        page_count = int(_run_pdf_text_worker(resolved, "pages", 0)["page_count"])
        relation = getattr(operator, _PAGE_RELATIONS[source_input.relation])
        return PdfCheckPagesOutput(
            page_count=page_count,
            match=bool(relation(page_count, source_input.ref_value)),
        )


class PdfExtractAnswersInput(StrictModel):
    """Input for ``pdf.extract_answers``.

    Attributes:
        path: Workspace-relative PDF path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        """Requires the path to target a PDF file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PDF extension.

        Raises:
            ValueError: If the path does not end in ``.pdf``.
        """
        if Path(value).suffix.lower() != ".pdf":
            raise ValueError("pdf.extract_answers requires a .pdf file")
        return value


class PdfExtractAnswersOutput(StrictModel):
    """Right-hand-side values parsed from ``key = value`` lines.

    Attributes:
        answers: Bounded text after ``=`` on qualifying lines, in page/line
            order. Lets a verifier assert on computed answers without depending
            on their surrounding prose.
        truncated: Whether the aggregate source-content limit cut the evidence.
    """

    answers: list[str]
    truncated: bool


class ExtractAnswers(SourceCommand[PdfExtractAnswersInput, PdfExtractAnswersOutput]):
    """Extract ``key = value`` right-hand sides from a PDF's text."""

    name = "extract_answers"
    input_model = PdfExtractAnswersInput
    output_model = PdfExtractAnswersOutput

    def run(
        self, source_input: PdfExtractAnswersInput, context: SourceContext
    ) -> PdfExtractAnswersOutput:
        """Runs PDF answer extraction.

        Args:
            source_input: Validated extraction input.
            context: Source runtime context.

        Returns:
            The extracted right-hand-side values.
        """
        resolved = context.resolve_path(source_input.path)
        answers, truncated = extract_answers_from_pdf(
            resolved, limit=context.max_content_chars
        )
        return PdfExtractAnswersOutput(answers=answers, truncated=truncated)


def extract_answers_from_pdf(
    path: Path, *, limit: int = 90_000
) -> tuple[list[str], bool]:
    """Parses ``key = value`` right-hand sides from a PDF's text.

    Args:
        path: Resolved PDF file path.
        limit: Maximum aggregate answer characters to return.

    Returns:
        Bounded values in page/line order and whether evidence was truncated.

    Raises:
        SourceDataError: If the PDF is malformed or cannot be opened.
    """
    result = _run_pdf_text_worker(path, "answers", limit)
    answers = result.get("answers")
    if not isinstance(answers, list) or not all(
        isinstance(answer, str) for answer in answers
    ):
        raise SourceDataError("PDF answer extraction returned malformed evidence")
    return answers, bool(result.get("truncated"))


class PdfFindLabeledValueInput(StrictModel):
    """Input for ``pdf.find_labeled_value``.

    Attributes:
        path: Workspace-relative PDF path.
        label: Case-insensitive label stem (``"Substantive"`` matches
            ``Substantive changes: 8`` and ``SUBSTANTIVE: 8``). Matched inside
            one text block so a neighbouring column never supplies the value.
    """

    path: str
    label: str = Field(min_length=2, max_length=80)

    @field_validator("path")
    @classmethod
    def require_pdf_extension(cls, value: str) -> str:
        """Requires the path to target a PDF file."""
        if Path(value).suffix.lower() != ".pdf":
            raise ValueError("pdf.find_labeled_value requires a .pdf file")
        return value

    @field_validator("label")
    @classmethod
    def require_word_label(cls, value: str) -> str:
        """Requires a plain word stem so the label cannot smuggle a regex."""
        if not value.strip() or not all(ch.isalnum() or ch in " _-" for ch in value):
            raise ValueError("pdf.find_labeled_value label must be a plain word stem")
        return value.strip()


class PdfFindLabeledValueOutput(StrictModel):
    """The first ``<label …>: <number>`` pair found in reading order.

    Attributes:
        found: Whether any block carries the label followed by a number.
        label: The label text as written in the document (None if not found).
        value: The number bound to the label (None if not found).
        alternatives: Numbers hedged onto the same line after the value
            (``8 or 9``, ``7 (possibly 5)``); empty when the figure is stated once.
        page: 1-based page of the first hit.
        block: Text-block index of the first hit on that page.
        line: The line carrying the pair, for the failure reason.
        occurrences: How many label/number pairs matched in the document.
        consistent: Whether every occurrence carries the same number.
        all_values: Sorted distinct values across occurrences.
    """

    found: bool
    label: str | None
    value: float | None
    alternatives: list[float]
    page: int | None
    block: int | None
    line: str | None
    occurrences: int
    consistent: bool
    all_values: list[float]


class FindLabeledValue(SourceCommand[PdfFindLabeledValueInput, PdfFindLabeledValueOutput]):
    """Bind a headline figure to its label inside one text block."""

    name = "find_labeled_value"
    input_model = PdfFindLabeledValueInput
    output_model = PdfFindLabeledValueOutput

    def run(
        self, source_input: PdfFindLabeledValueInput, context: SourceContext
    ) -> PdfFindLabeledValueOutput:
        """Runs the label/value lookup."""
        resolved = context.resolve_path(source_input.path)
        result = _run_pdf_text_worker(resolved, "labels", 500, source_input.label)
        try:
            return PdfFindLabeledValueOutput.model_validate(result)
        except ValueError as exc:
            raise SourceDataError("PDF label lookup returned malformed evidence") from exc


COMMANDS = (
    ExtractText(),
    InspectDocument(),
    CheckPages(),
    ExtractAnswers(),
    FindLabeledValue(),
)
