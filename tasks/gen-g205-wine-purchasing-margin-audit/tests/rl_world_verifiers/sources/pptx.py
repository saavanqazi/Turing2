import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..models import StrictModel
from ..source_types import (
    MediaAttachment,
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

RENDER_TIMEOUT_SECONDS = 60
MAX_RENDERED_SLIDES = 60
MAX_RENDERED_BYTES = 24 * 1024 * 1024


class PptxExtractTextInput(StrictModel):
    """Input for pptx.extract_text.

    Attributes:
        path: Workspace-relative PPTX path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        """Requires the path to target a PPTX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PPTX extension.

        Raises:
            ValueError: If the path does not end in ``.pptx``.
        """
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.extract_text requires a .pptx file")
        return value


class PptxExtractTextOutput(StrictModel):
    """Output from pptx.extract_text.

    Attributes:
        text: Extracted slide text.
    """

    text: str


class ExtractText(SourceCommand[PptxExtractTextInput, PptxExtractTextOutput]):
    """Extracts text from PPTX slides."""

    name = "extract_text"
    input_model = PptxExtractTextInput
    output_model = PptxExtractTextOutput

    def run(self, source_input: PptxExtractTextInput, context: SourceContext) -> PptxExtractTextOutput:
        """Runs PPTX text extraction.

        Args:
            source_input: Validated PPTX extraction input.
            context: Source runtime context.

        Returns:
            Extracted PPTX text.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_pptx_package(resolved, trusted=False)
        return PptxExtractTextOutput(
            text=extract_pptx_text(resolved, context.max_content_chars)
        )


class PptxRenderSlidesInput(StrictModel):
    """Input for ``pptx.render_slides``."""

    path: str

    @field_validator("path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.render_slides requires a .pptx file")
        return value


class RenderedSlide(StrictModel):
    """Visual evidence for one slide, with pixels excluded from JSON output."""

    number: int
    width: int
    height: int
    image_base64: str = Field(exclude=True, repr=False)


class PptxRenderSlidesOutput(StrictModel):
    """Rendered slide evidence plus ordinary extracted text."""

    text: str
    slide_count: int
    slides: list[RenderedSlide]
    truncated: bool
    renderer: str

    def media_attachments(self) -> list[MediaAttachment]:
        """Return image payloads without exposing them to reward serialization."""
        return [
            MediaAttachment(
                label=f"Slide {slide.number}",
                mime_type="image/png",
                data_base64=slide.image_base64,
            )
            for slide in self.slides
        ]


class RenderSlides(SourceCommand[PptxRenderSlidesInput, PptxRenderSlidesOutput]):
    """Render slide images for a prompt-grounded visual rubric."""

    name = "render_slides"
    input_model = PptxRenderSlidesInput
    output_model = PptxRenderSlidesOutput

    def run(
        self, source_input: PptxRenderSlidesInput, context: SourceContext
    ) -> PptxRenderSlidesOutput:
        resolved = context.resolve_path(source_input.path)
        _validate_pptx_package(resolved, trusted=False)
        return render_pptx_slides(resolved, context.max_content_chars)


class PptxInspectDeckInput(StrictModel):
    """Input for ``pptx.inspect_deck``.

    Attributes:
        path: Workspace-relative PPTX path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        """Requires the path to target a PPTX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PPTX extension.

        Raises:
            ValueError: If the path does not end in ``.pptx``.
        """
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.inspect_deck requires a .pptx file")
        return value


class PptxSlideSummary(StrictModel):
    """One slide of ``pptx.inspect_deck``, so a check can address a slide by
    what it IS (visible, titled, carrying text) instead of by physical position.

    Attributes:
        index: 1-based position in stored deck order.
        hidden: True when PowerPoint would skip the slide (``show="0"``).
        layout: Slide-layout name.
        title: Title placeholder text, "" when the layout has none, the title is
            blank, or the placeholder itself is hidden (``p:cNvPr hidden="1"``):
            a title nobody is shown is not the slide's title.
        body_text: Text of every non-title, non-hidden shape and table,
            newline-joined, bounded by the source content limit; "" for a blank
            or hollow slide.
        text: ``title`` and ``body_text`` newline-joined (either part omitted
            when empty). "The Nth slide the audience sees names X" is one
            ``regex_match`` on this field, whichever region carries X;
            ``body_text`` alone answers "X is in the body, not the title".
        has_content: True when the slide shows text or a picture, chart, table,
            media, or other graphic frame. A chart-only slide is not blank, so
            ``empty_visible_slide_count`` counts a slide only when this is False.
    """

    index: int
    hidden: bool
    layout: str
    title: str
    body_text: str
    text: str
    has_content: bool


class PptxInspectDeckOutput(StrictModel):
    """Deck structure expressed as counts, names, and speaker notes.

    python-pptx only: unlike ``pptx.render_slides`` this needs no LibreOffice,
    produces no images, and carries no image-capability requirement, so it can
    answer "does the deck have four slides" deterministically and for free.
    It sees structure, never visual quality; a presentation-design obligation
    still belongs to ``pptx.render_slides`` and a rubric.

    Attributes:
        slide_count: Slides stored in the deck, hidden ones included.
        visible_slide_count: Slides an audience is actually shown. PowerPoint
            hides a slide with ``show="0"``, which a stored count cannot see, so
            a prompt capping what the audience sees means this number.
        hidden_slide_count: Slides marked hidden, such as a backup appendix.
        slide_titles: One entry per slide, in deck order, empty for a slide
            whose layout has no title placeholder or whose title is blank.
            Aligning with ``slide_count`` keeps a position meaningful and makes
            ``not_contains ""`` mean "every slide is titled".
        layout_names: One slide-layout name per slide, in deck order.
        image_count: Picture shapes across the deck.
        chart_count: Chart shapes across the deck.
        table_count: Table shapes across the deck.
        slides_with_notes: Slides carrying non-empty speaker notes. Exact even
            when ``notes_text`` is truncated.
        notes_text: Speaker notes, each prefixed with its slide number and
            capped to the configured source content limit.
        visible_slide_titles: ``slide_titles`` restricted to slides the audience
            is shown, in deck order. A prompt that says "the first slide is
            titled X" means the first slide the audience sees, so
            ``$.visible_slide_titles[0]`` is that fact; a hidden cover or backup
            slide cannot shift it.
        first_content_slide_title: Title of the first visible slide carrying any
            text (title or body). Skips a blank cover; "" when no visible slide
            has text, so ``equals`` on it fails on a hollow deck.
        untitled_visible_slide_count: Visible slides whose title placeholder is
            empty, absent, or hidden; ``equals 0`` means "every shown slide is
            titled".
        empty_visible_slide_count: Visible slides showing nothing at all — no
            text and no picture, chart, table, or media frame; ``equals 0``
            means "no blank slide is shown". A chart-only or picture-only slide
            is content, not a blank, so it is not counted.
        slides: One ``PptxSlideSummary`` per stored slide, in deck order, for
            checks that need a specific slide's text or hidden flag.
        visible_slides: ``slides`` without the hidden ones, so "the body of the
            third slide the audience sees" is ``$.visible_slides[2].body_text``
            and a hidden cover cannot shift it.
    """

    slide_count: int
    visible_slide_count: int
    hidden_slide_count: int
    slide_titles: list[str]
    layout_names: list[str]
    image_count: int
    chart_count: int
    table_count: int
    slides_with_notes: int
    notes_text: str
    visible_slide_titles: list[str]
    first_content_slide_title: str
    untitled_visible_slide_count: int
    empty_visible_slide_count: int
    slides: list[PptxSlideSummary]
    visible_slides: list[PptxSlideSummary]


class InspectDeck(SourceCommand[PptxInspectDeckInput, PptxInspectDeckOutput]):
    """Report slide count, titles, layouts, embedded objects, and notes."""

    name = "inspect_deck"
    input_model = PptxInspectDeckInput
    output_model = PptxInspectDeckOutput

    def run(
        self, source_input: PptxInspectDeckInput, context: SourceContext
    ) -> PptxInspectDeckOutput:
        """Runs deck structure inspection.

        Args:
            source_input: Validated deck inspection input.
            context: Source runtime context.

        Returns:
            Deck structure as counts, names, and bounded notes text.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_pptx_package(resolved, trusted=False)
        return inspect_pptx_deck(resolved, context.max_content_chars)


def inspect_pptx_deck(path: Path, limit: int) -> PptxInspectDeckOutput:
    """Reads deck structure without rendering anything.

    Args:
        path: Resolved PPTX file path.
        limit: Maximum number of speaker-notes characters to return.

    Returns:
        Deck structure as counts, names, and notes text capped to ``limit``.
    """
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    picture_types = {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.LINKED_PICTURE}
    presentation = Presentation(str(path))
    titles: list[str] = []
    layout_names: list[str] = []
    notes_parts: list[str] = []
    notes_total = 0
    image_count = 0
    chart_count = 0
    table_count = 0
    slides_with_notes = 0
    hidden_count = 0
    summaries: list[PptxSlideSummary] = []
    body_total = 0

    for index, slide in enumerate(presentation.slides, start=1):
        hidden = slide_is_hidden(slide)
        if hidden:
            hidden_count += 1
        title_shape = slide.shapes.title
        # A hidden title placeholder is still `shapes.title`, but the audience
        # never reads it, so it cannot be the slide's title.
        if title_shape is None or shape_is_hidden(title_shape):
            title = ""
        else:
            title = (title_shape.text or "").strip()
        titles.append(title)
        layout_names.append(str(getattr(slide.slide_layout, "name", "") or ""))
        body_parts: list[str] = []
        for fragment in iter_slide_text(
            slide, skip_shape=title_shape, skip_hidden=True
        ):
            body_total = append_limited(body_parts, fragment, body_total, limit)
        body_text = "\n".join(body_parts)
        summaries.append(PptxSlideSummary(
            index=index, hidden=hidden, layout=layout_names[-1], title=title,
            body_text=body_text,
            text="\n".join(part for part in (title, body_text) if part),
            has_content=bool(title or body_text) or any(
                shape_carries_visual_content(shape) for shape in slide.shapes
            ),
        ))
        for shape in slide.shapes:
            if getattr(shape, "has_chart", False):
                chart_count += 1
            if getattr(shape, "has_table", False):
                table_count += 1
            if getattr(shape, "shape_type", None) in picture_types:
                image_count += 1
        title_layout_chars = sum(map(len, titles)) + sum(map(len, layout_names))
        if title_layout_chars > limit:
            raise SourceDataError(
                "PPTX title and layout evidence exceeds the source content limit"
            )

        # `slide.notes_slide` creates the notes part on access, so the guard has
        # to be `has_notes_slide` rather than a try/except around the read.
        if not slide.has_notes_slide:
            continue
        notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        if not notes:
            continue
        # Counted before the content bound: a truncated evidence string must
        # not silently lower the number an author asserts on.
        slides_with_notes += 1
        notes_limit = max(0, limit - title_layout_chars)
        if notes_total < notes_limit:
            notes_total = append_limited(
                notes_parts, f"[Slide {index}] {notes}", notes_total, notes_limit
            )

    visible = [entry for entry in summaries if not entry.hidden]
    first_content = next(
        (entry.title for entry in visible if entry.title or entry.body_text), ""
    )
    return PptxInspectDeckOutput(
        visible_slide_titles=[entry.title for entry in visible],
        first_content_slide_title=first_content,
        untitled_visible_slide_count=sum(1 for entry in visible if not entry.title),
        empty_visible_slide_count=sum(
            1 for entry in visible if not entry.has_content
        ),
        slides=summaries,
        visible_slides=visible,
        slide_count=len(titles),
        visible_slide_count=len(titles) - hidden_count,
        hidden_slide_count=hidden_count,
        slide_titles=titles,
        layout_names=layout_names,
        image_count=image_count,
        chart_count=chart_count,
        table_count=table_count,
        slides_with_notes=slides_with_notes,
        notes_text="\n".join(notes_parts)[
            : max(0, limit - sum(map(len, titles)) - sum(map(len, layout_names)))
        ],
    )


def extract_pptx_text(path: Path, limit: int) -> str:
    """Extracts visible slide and table text from a PPTX file.

    Args:
        path: Resolved PPTX file path.
        limit: Maximum number of characters to return.

    Returns:
        Extracted text capped to ``limit`` characters.
    """
    from pptx import Presentation

    presentation = Presentation(str(path))
    parts: list[str] = []
    total = 0
    for index, slide in enumerate(presentation.slides, start=1):
        slide_parts = list(iter_slide_text(slide))
        if not slide_parts:
            continue
        total = append_limited(parts, f"[Slide {index}]", total, limit)
        if total >= limit:
            break
        for text in slide_parts:
            total = append_limited(parts, text, total, limit)
            if total >= limit:
                break
        if total >= limit:
            break
    return "\n".join(parts)[:limit]


def render_pptx_slides(path: Path, text_limit: int) -> PptxRenderSlidesOutput:
    """Render a PPTX through isolated LibreOffice and Poppler subprocesses.

    Rendering happens only in a temporary directory. The submitted deck is
    never mutated, macros are never run, and the LibreOffice user profile is
    isolated so parallel benchmark runs cannot share state.
    """
    # Parse before invoking LibreOffice so a malformed ZIP becomes the same
    # normal agent-output failure as pptx.extract_text.
    text = extract_pptx_text(path, text_limit)
    office = shutil.which("soffice") or shutil.which("libreoffice")
    pdftoppm = shutil.which("pdftoppm")
    if not office or not pdftoppm:
        raise SourceCapabilityError(
            "pptx.render_slides requires LibreOffice Impress and pdftoppm in the benchmark image"
        )

    with tempfile.TemporaryDirectory(prefix="filecheck-pptx-") as raw_temp:
        tempdir = Path(raw_temp)
        profile = tempdir / "profile"
        pdf_dir = tempdir / "pdf"
        png_dir = tempdir / "png"
        pdf_dir.mkdir()
        png_dir.mkdir()
        try:
            subprocess.run(
                [
                    office,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                    str(pdf_dir),
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
            pdf_files = sorted(pdf_dir.glob("*.pdf"))
            if len(pdf_files) != 1:
                raise SourceDataError("PowerPoint conversion did not produce exactly one PDF")
            prefix = png_dir / "slide"
            subprocess.run(
                [pdftoppm, "-png", "-r", "110", str(pdf_files[0]), str(prefix)],
                check=True,
                capture_output=True,
                text=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceCapabilityError("PPTX rendering exceeded the verifier timeout") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "conversion failed").strip()
            raise SourceDataError(f"PowerPoint rendering failed: {detail[:300]}") from exc

        images = sorted(png_dir.glob("slide-*.png"), key=slide_sort_key)
        if not images:
            raise SourceDataError("PowerPoint rendering produced no slide images")
        slide_count = len(images)
        selected: list[RenderedSlide] = []
        total_bytes = 0
        truncated = slide_count > MAX_RENDERED_SLIDES
        for image in images[:MAX_RENDERED_SLIDES]:
            raw = image.read_bytes()
            if total_bytes + len(raw) > MAX_RENDERED_BYTES:
                truncated = True
                break
            width, height = png_dimensions(raw)
            selected.append(RenderedSlide(
                number=slide_number_from_path(image),
                width=width,
                height=height,
                image_base64=base64.b64encode(raw).decode("ascii"),
            ))
            total_bytes += len(raw)
        if not selected:
            raise SourceDataError("PowerPoint rendering exceeded the configured image evidence limit")
        return PptxRenderSlidesOutput(
            text=text,
            slide_count=slide_count,
            slides=selected,
            truncated=truncated,
            renderer="libreoffice-impress+pdftoppm",
        )


def slide_is_hidden(slide: Any) -> bool:
    """Whether PowerPoint would skip this slide when presenting.

    A hidden slide is stored as ``<p:sld show="0">``. python-pptx exposes no
    accessor for it, so this reads the underlying element defensively: a future
    python-pptx that reshapes the element must degrade to "visible" rather than
    raise, since ``AttributeError`` is absent from ``agent_failure_types`` and
    would fail the whole run instead of one check.

    Args:
        slide: python-pptx slide object.

    Returns:
        True when the slide carries an explicit ``show="0"``.
    """
    element = getattr(slide, "_element", None)
    getter = getattr(element, "get", None)
    if not callable(getter):
        return False
    return str(getter("show") or "").strip() in {"0", "false"}


def slide_sort_key(path: Path) -> tuple[int, str]:
    """Sort Poppler's ``slide-12.png`` names numerically."""
    return slide_number_from_path(path), path.name


def slide_number_from_path(path: Path) -> int:
    """Read Poppler's trailing page number, with a stable fallback."""
    stem = path.stem.rsplit("-", 1)[-1]
    try:
        return int(stem)
    except ValueError:
        return 0


def png_dimensions(raw: bytes) -> tuple[int, int]:
    """Read PNG IHDR dimensions without adding an image-processing dependency."""
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise SourceDataError("PowerPoint renderer returned an invalid PNG")
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    if width <= 0 or height <= 0:
        raise SourceDataError("PowerPoint renderer returned a PNG with invalid dimensions")
    return width, height


def shape_is_hidden(shape: Any) -> bool:
    """Whether PowerPoint would draw nothing for this shape.

    A hidden shape is stored as ``<p:cNvPr ... hidden="1">`` inside the shape's
    non-visual properties. python-pptx exposes no accessor for it, so this reads
    the element defensively, the way ``slide_is_hidden`` does: anything
    unexpected degrades to "visible" rather than raising, because
    ``AttributeError`` is absent from ``agent_failure_types`` and would fail the
    whole run instead of one check.

    Args:
        shape: python-pptx shape object.

    Returns:
        True when the shape carries an explicit ``hidden="1"``.
    """
    element = getattr(shape, "_element", None)
    properties = getattr(element, "_nvXxPr", None)
    non_visual = getattr(properties, "cNvPr", None)
    if non_visual is None:
        # Namespace-agnostic fallback for a shape kind whose element does not
        # expose ``_nvXxPr``: the first ``cNvPr`` under the shape's own
        # non-visual properties child.
        finder = getattr(element, "iter", None)
        if not callable(finder):
            return False
        non_visual = next(
            (
                node
                for node in finder()
                if str(getattr(node, "tag", "")).rsplit("}", 1)[-1] == "cNvPr"
            ),
            None,
        )
    getter = getattr(non_visual, "get", None)
    if not callable(getter):
        return False
    return str(getter("hidden") or "").strip() in {"1", "true"}


def shape_carries_visual_content(shape: Any) -> bool:
    """Whether a shape shows something other than text.

    A slide holding only a chart, picture, table, or media frame is a content
    slide, not a blank one, so ``empty_visible_slide_count`` asks this before
    calling a text-free slide empty. Hidden shapes show nothing and answer False.

    Args:
        shape: python-pptx shape object.

    Returns:
        True when the shape draws a picture, chart, table, media, or embedded
        object the audience can see.
    """
    if shape_is_hidden(shape):
        return False
    if getattr(shape, "has_chart", False) or getattr(shape, "has_table", False):
        return True
    element = getattr(shape, "_element", None)
    tag = str(getattr(element, "tag", "")).rsplit("}", 1)[-1]
    if tag in {"pic", "graphicFrame"}:
        return True
    if tag == "grpSp":
        return any(
            shape_carries_visual_content(child)
            for child in getattr(shape, "shapes", ())
        )
    name = str(getattr(getattr(shape, "shape_type", None), "name", "") or "")
    return name in {
        "PICTURE",
        "LINKED_PICTURE",
        "MEDIA",
        "EMBEDDED_OLE_OBJECT",
        "LINKED_OLE_OBJECT",
    }


def iter_slide_text(
    slide: Any, skip_shape: Any = None, skip_hidden: bool = False
) -> list[str]:
    """Collects text from a slide's text frames and tables.

    Args:
        slide: python-pptx slide object.
        skip_shape: Optional shape (the title placeholder) to leave out, so the
            caller can separate body text from the title.
        skip_hidden: When True, shapes marked ``hidden="1"`` are left out, so a
            per-slide summary reports what the audience is shown. Defaults to
            False, which keeps ``pptx.extract_text`` reading every stored shape
            as it always has.

    Returns:
        Non-empty text fragments from the slide.
    """
    fragments: list[str] = []
    for shape in slide.shapes:
        if skip_shape is not None and shape._element is skip_shape._element:
            continue
        if skip_hidden and shape_is_hidden(shape):
            continue
        if getattr(shape, "has_text_frame", False):
            text = shape.text.strip()
            if text:
                fragments.append(text)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
                if cells:
                    fragments.append(" | ".join(cells))
    return fragments


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


class PptxCompareFilesInput(StrictModel):
    """Input for ``pptx.compare_files``.

    Every ``examine_*`` flag defaults to the aspect being checked, so an
    unconfigured call compares text and formatting the way the OSWorld metric
    does. Turn a flag off to let a deliberately-varying aspect (e.g. shape
    geometry on a "resize this box" task) pass.

    Attributes:
        path: Workspace-relative PPTX path for the produced deliverable.
        expected_path: Verifier-private PPTX path for the gold reference.
        approximately_tolerance: Relative tolerance for geometry comparisons.
        color_tolerance: Max RGB Euclidean distance treated as equal color.
    """

    path: str
    expected_path: str
    approximately_tolerance: float = Field(
        default=0.005, ge=0, allow_inf_nan=False
    )
    color_tolerance: float = Field(
        default=0.0, ge=0, le=442, allow_inf_nan=False
    )
    examine_number_of_slides: bool = True
    examine_shape: bool = True
    examine_text: bool = True
    examine_indent: bool = True
    examine_font_name: bool = True
    examine_font_size: bool = True
    examine_font_bold: bool = True
    examine_font_italic: bool = True
    examine_color_rgb: bool = True
    examine_font_underline: bool = True
    examine_strike_through: bool = True
    examine_alignment: bool = True
    examine_image_size: bool = False
    examine_modify_height: bool = False
    examine_bullets: bool = True
    examine_background_color: bool = True
    examine_note: bool = True

    @model_validator(mode="after")
    def require_one_examined_aspect(self) -> "PptxCompareFilesInput":
        """Rejects a comparison that examines nothing and so always passes."""
        if not any(
            getattr(self, name)
            for name in type(self).model_fields
            if name.startswith("examine_")
        ):
            raise ValueError("at least one examine_* option must stay enabled")
        return self

    @field_validator("path", "expected_path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        """Requires each path to target a PPTX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PPTX extension.

        Raises:
            ValueError: If the path does not end in ``.pptx``.
        """
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.compare_files requires .pptx files")
        return value


MAX_COMPARE_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_COMPARE_ARCHIVE_MEMBERS = 2_000
MAX_COMPARE_ARCHIVE_RATIO = 200
MAX_COMPARE_XML_ELEMENTS = 500_000
MAX_COMPARE_CONTENT_ELEMENTS = 200_000


def _validate_pptx_package(path: Path, *, trusted: bool) -> None:
    """Reject malformed or expansion-heavy PPTX packages before parsing."""
    import zipfile

    error_type = SourceCapabilityError if trusted else SourceDataError
    try:
        if path.stat().st_size > MAX_COMPARE_PACKAGE_BYTES:
            raise ValueError("package exceeds the compressed-size limit")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_COMPARE_ARCHIVE_MEMBERS:
                raise ValueError("package has too many archive members")
            total = 0
            xml_elements = 0
            content_elements = 0
            for member in members:
                if member.file_size > MAX_COMPARE_PACKAGE_BYTES:
                    raise ValueError("package member exceeds the expanded-size limit")
                if member.file_size and not member.compress_size:
                    raise ValueError("package contains an invalid compressed member")
                if (
                    member.compress_size
                    and member.file_size / member.compress_size
                    > MAX_COMPARE_ARCHIVE_RATIO
                ):
                    raise ValueError("package contains a suspicious compression ratio")
                actual = 0
                if not member.is_dir():
                    with archive.open(member) as stream:
                        while chunk := stream.read(1024 * 1024):
                            actual += len(chunk)
                            total += len(chunk)
                            if actual > MAX_COMPARE_PACKAGE_BYTES:
                                raise ValueError("package member exceeds its streamed limit")
                            if total > MAX_COMPARE_PACKAGE_BYTES:
                                raise ValueError("package exceeds the expanded-size limit")
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
                                if local_name in {"sp", "graphicFrame", "pic", "grpSp", "p"}:
                                    content_elements += 1
                                if xml_elements > MAX_COMPARE_XML_ELEMENTS:
                                    raise ValueError("package XML is too structurally complex")
                                if content_elements > MAX_COMPARE_CONTENT_ELEMENTS:
                                    raise ValueError("presentation content is too structurally complex")
                            else:
                                element.clear()
        from pptx import Presentation

        Presentation(str(path))
    except Exception as exc:
        kind = "trusted PPTX reference" if trusted else "PPTX artifact"
        raise error_type(f"{kind} is unreadable: {path.name}: {exc}") from exc


class PptxScoreOutput(StrictModel):
    """A presentation comparison score with its pass/fail interpretation.

    Attributes:
        score: Similarity in ``[0, 1]``.
        match: Whether ``score`` equals ``1.0``.
    """

    score: float
    match: bool


class CompareFiles(SourceCommand[PptxCompareFilesInput, PptxScoreOutput]):
    """Compare a PPTX deliverable's content and formatting against a gold PPTX."""

    name = "compare_files"
    input_model = PptxCompareFilesInput
    output_model = PptxScoreOutput

    def run(
        self, source_input: PptxCompareFilesInput, context: SourceContext
    ) -> PptxScoreOutput:
        """Runs the PPTX comparison.

        Args:
            source_input: Validated comparison input.
            context: Source runtime context.

        Returns:
            ``1.0`` when every examined aspect matches, else ``0.0``.
        """
        resolved = context.resolve_path(source_input.path)
        expected = context.resolve_trusted_reference_path(source_input.expected_path)
        _validate_pptx_package(resolved, trusted=False)
        _validate_pptx_package(expected, trusted=True)
        options = source_input.model_dump(exclude={"path", "expected_path"})
        try:
            trusted_self_score = compare_pptx_files(expected, expected, options)
        except Exception as exc:
            raise SourceCapabilityError(
                f"trusted PPTX reference is unreadable: {expected.name}: {exc}"
            ) from exc
        if trusted_self_score < 1.0:
            raise SourceCapabilityError(
                "trusted PPTX reference contains no comparable slide evidence"
            )
        try:
            score = compare_pptx_files(resolved, expected, options)
        except Exception as exc:
            raise SourceDataError(
                f"PPTX artifact is unreadable: {resolved.name}: {exc}"
            ) from exc
        return PptxScoreOutput(score=score, match=score >= 1.0)


class PptxCheckSlideNumbersColorInput(StrictModel):
    """Input for ``pptx.check_slide_numbers_color``.

    Attributes:
        path: Workspace-relative PPTX path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        """Requires the path to target a PPTX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PPTX extension.

        Raises:
            ValueError: If the path does not end in ``.pptx``.
        """
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.check_slide_numbers_color requires a .pptx file")
        return value


class CheckSlideNumbersColor(
    SourceCommand[PptxCheckSlideNumbersColorInput, PptxScoreOutput]
):
    """Check the slide-number placeholder is red."""

    name = "check_slide_numbers_color"
    input_model = PptxCheckSlideNumbersColorInput
    output_model = PptxScoreOutput

    def run(
        self, source_input: PptxCheckSlideNumbersColorInput, context: SourceContext
    ) -> PptxScoreOutput:
        """Runs the slide-number color check.

        Args:
            source_input: Validated input.
            context: Source runtime context.

        Returns:
            ``1.0`` when a slide-number placeholder is red, else ``0.0``.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_pptx_package(resolved, trusted=False)
        score = check_slide_numbers_color(resolved)
        return PptxScoreOutput(score=score, match=score >= 1.0)


class PptxFillRgbDistanceInput(StrictModel):
    """Input for ``pptx.fill_rgb_distance``.

    Attributes:
        path: Workspace-relative PPTX path.
        rgb: Target slide-background color as an ``[r, g, b]`` triple.
        original_rgb: Optional pre-edit color; a slide still carrying it scores
            as a full match, so untouched slides do not drag the average down.
    """

    path: str
    rgb: tuple[int, int, int]
    original_rgb: tuple[int, int, int] | None = None

    @field_validator("rgb", "original_rgb")
    @classmethod
    def require_rgb_channels(
        cls, value: tuple[int, int, int] | None
    ) -> tuple[int, int, int] | None:
        """Require an actual 8-bit RGB triple."""
        if value is not None and any(channel < 0 or channel > 255 for channel in value):
            raise ValueError("RGB channels must be between 0 and 255")
        return value

    @field_validator("path")
    @classmethod
    def require_pptx_extension(cls, value: str) -> str:
        """Requires the path to target a PPTX file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a PPTX extension.

        Raises:
            ValueError: If the path does not end in ``.pptx``.
        """
        if Path(value).suffix.lower() != ".pptx":
            raise ValueError("pptx.fill_rgb_distance requires a .pptx file")
        return value


class FillRgbDistance(SourceCommand[PptxFillRgbDistanceInput, PptxScoreOutput]):
    """Score how close slide backgrounds are to a target fill color."""

    name = "fill_rgb_distance"
    input_model = PptxFillRgbDistanceInput
    output_model = PptxScoreOutput

    def run(
        self, source_input: PptxFillRgbDistanceInput, context: SourceContext
    ) -> PptxScoreOutput:
        """Runs the slide-fill color-distance evaluation.

        Args:
            source_input: Validated input.
            context: Source runtime context.

        Returns:
            A similarity score in ``[0, 1]`` averaged across slides.
        """
        resolved = context.resolve_path(source_input.path)
        _validate_pptx_package(resolved, trusted=False)
        score = evaluate_presentation_fill_to_rgb_distance(
            resolved,
            rgb=source_input.rgb,
            original_rgb=source_input.original_rgb,
        )
        return PptxScoreOutput(score=score, match=score >= 1.0)


def _pptx_theme_root(part: Any) -> Any | None:
    """Return the theme XML governing a slide/run part."""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml import parse_xml

    try:
        slide = part.slide
        theme_part = slide.slide_layout.slide_master.part.part_related_by(RT.THEME)
        return parse_xml(theme_part.blob)
    except (AttributeError, KeyError, ValueError):
        return None


def _theme_typeface(part: Any, token: str) -> str:
    """Resolve ``+mj-*``/``+mn-*`` typeface tokens through the slide theme."""
    root = _pptx_theme_root(part)
    if root is None or not token.startswith(("+mj-", "+mn-")):
        return token
    from pptx.oxml.ns import qn

    family = "a:majorFont" if token.startswith("+mj-") else "a:minorFont"
    script = token.rsplit("-", 1)[-1]
    child_name = {"lt": "a:latin", "ea": "a:ea", "cs": "a:cs"}.get(
        script, "a:latin"
    )
    font = root.find(f".//{qn(family)}/{qn(child_name)}")
    typeface = font.get("typeface") if font is not None else None
    return typeface or token


def _theme_color_rgb(part: Any, token: str) -> tuple[int, int, int] | None:
    """Resolve a DrawingML scheme token to its concrete theme RGB value."""
    from pptx.oxml.ns import qn

    root = _pptx_theme_root(part)
    if root is None:
        return None
    aliases = {"tx1": "dk1", "tx2": "dk2", "bg1": "lt1", "bg2": "lt2"}
    token = aliases.get(token, token)
    slot = root.find(f".//{qn('a:clrScheme')}/{qn(f'a:{token}')}")
    if slot is None or not len(slot):
        return None
    color = slot[0]
    value = color.get("val")
    if value is None or len(value) != 6:
        value = color.get("lastClr")
    if value is None or len(value) != 6:
        return None
    try:
        channels = bytes.fromhex(value)
        return channels[0], channels[1], channels[2]
    except ValueError:
        return None


def _resolved_color_node(node: Any, part: Any) -> tuple[int, int, int] | None:
    """Resolve an sRGB/scheme/system DrawingML color and luminance transforms."""
    from lxml import etree

    kind = etree.QName(node).localname
    if kind == "srgbClr":
        try:
            rgb = tuple(bytes.fromhex(node.get("val", "")))
        except ValueError:
            return None
    elif kind == "schemeClr":
        rgb = _theme_color_rgb(part, node.get("val", ""))
    elif kind == "sysClr":
        try:
            rgb = tuple(bytes.fromhex(node.get("lastClr", "")))
        except ValueError:
            return None
    else:
        return None
    if rgb is None or len(rgb) != 3:
        return None
    channels = [float(channel) for channel in rgb]
    for transform in node:
        name = etree.QName(transform).localname
        try:
            amount = int(transform.get("val", "0")) / 100_000
        except ValueError:
            continue
        if name == "lumMod":
            channels = [channel * amount for channel in channels]
        elif name == "lumOff":
            channels = [channel + 255 * amount for channel in channels]
    bounded = [max(0, min(255, round(channel))) for channel in channels]
    return bounded[0], bounded[1], bounded[2]


def _font_property_elements(run: Any, paragraph: Any, shape: Any) -> list[Any]:
    """Collect run-property elements from direct through master inheritance."""
    from pptx.oxml.ns import qn

    elements: list[Any] = []
    direct = getattr(run._r, "rPr", None)
    if direct is not None:
        elements.append(direct)
    paragraph_properties = getattr(paragraph._p, "pPr", None)
    if paragraph_properties is not None:
        default = paragraph_properties.find(qn("a:defRPr"))
        if default is not None:
            elements.append(default)

    current = shape
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            list_style = current.text_frame._txBody.find(qn("a:lstStyle"))
            level = list_style.find(qn(f"a:lvl{paragraph.level + 1}pPr"))
            default = level.find(qn("a:defRPr")) if level is not None else None
        except (AttributeError, ValueError):
            default = None
        if default is not None:
            elements.append(default)
        try:
            current = current._base_placeholder
        except (AttributeError, KeyError, ValueError):
            current = None

    try:
        master = run.part.slide.slide_layout.slide_master
        text_styles = master.element.find(qn("p:txStyles"))
        placeholder_name = (
            shape.placeholder_format.type.name
            if getattr(shape, "is_placeholder", False)
            else ""
        )
        if "TITLE" in placeholder_name:
            style_name = "p:titleStyle"
        elif placeholder_name in {"BODY", "OBJECT", "SUBTITLE"}:
            style_name = "p:bodyStyle"
        else:
            style_name = "p:otherStyle"
        style = text_styles.find(qn(style_name)) if text_styles is not None else None
        level = style.find(qn(f"a:lvl{paragraph.level + 1}pPr")) if style is not None else None
        default = level.find(qn("a:defRPr")) if level is not None else None
        if default is not None:
            elements.append(default)
    except (AttributeError, KeyError, ValueError):
        pass
    return elements


def _effective_run_font(run: Any, paragraph: Any, shape: Any) -> dict[str, Any]:
    """Resolve effective run formatting through paragraph/placeholder/master/theme."""
    from pptx.oxml.ns import qn

    result: dict[str, Any] = {}
    for element in _font_property_elements(run, paragraph, shape):
        if "name" not in result:
            latin = element.find(qn("a:latin"))
            if latin is not None and latin.get("typeface"):
                result["name"] = _theme_typeface(run.part, latin.get("typeface"))
        for key, attribute in (("size", "sz"), ("bold", "b"), ("italic", "i"), ("underline", "u"), ("strike", "strike")):
            if key in result or element.get(attribute) is None:
                continue
            value = element.get(attribute)
            if key == "size":
                try:
                    result[key] = int(value)
                except ValueError:
                    result[key] = value
            elif key in {"bold", "italic"}:
                result[key] = value not in {"0", "false", "off"}
            else:
                result[key] = value
        if "color" not in result:
            solid = element.find(qn("a:solidFill"))
            if solid is not None and len(solid):
                result["color"] = _resolved_color_node(solid[0], run.part)
    result.setdefault("bold", False)
    result.setdefault("italic", False)
    result.setdefault("underline", None)
    result.setdefault("strike", "noStrike")
    result.setdefault("color", None)
    return result


def compare_pptx_files(
    path: Path, expected_path: Path, options: dict[str, Any] | None = None
) -> float:
    """Compares content and formatting of two PPTX files.

    Ported from the OSWorld ``compare_pptx_files`` metric (debug logging
    removed). Returns as soon as any examined aspect differs.

    Args:
        path: Resolved deliverable PPTX path.
        expected_path: Resolved gold PPTX path.
        options: ``examine_*`` toggles plus ``approximately_tolerance`` and
            ``color_tolerance``.

    Returns:
        ``1.0`` when every examined aspect matches, else ``0.0``.
    """
    from math import sqrt

    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.enum.text import PP_ALIGN

    opts = options or {}
    prs1 = Presentation(str(path))
    prs2 = Presentation(str(expected_path))

    tol = opts.get("approximately_tolerance", 0.005)
    color_tol = opts.get("color_tolerance", 0.0)

    def color_signature(color: Any, presentation: Any) -> tuple[Any, ...]:
        if color is None:
            return ("none",)
        if hasattr(color, "_color"):
            try:
                rgb = color.rgb
            except (AttributeError, ValueError):
                rgb = None
            try:
                brightness = float(color.brightness or 0.0)
            except (AttributeError, ValueError):
                brightness = 0.0
            if rgb is not None:
                return ("rgb", *tuple(rgb), brightness)
            try:
                theme = color.theme_color
            except (AttributeError, ValueError):
                theme = None
            if theme is not None:
                try:
                    node = color._color._xClr
                    resolved = _resolved_color_node(
                        node, presentation.slides[0].part
                    )
                except (AttributeError, IndexError, TypeError, ValueError):
                    resolved = None
                if resolved is not None:
                    return ("rgb", *resolved, 0.0)
                return ("theme", int(theme), brightness)
            color_type = getattr(color, "type", None)
            return ("unset", None if color_type is None else int(color_type))
        try:
            return ("rgb", *tuple(color), 0.0)
        except TypeError:
            return ("unknown", type(color).__qualname__)

    def color_equal(color1: Any, color2: Any) -> bool:
        signature1 = color_signature(color1, prs1)
        signature2 = color_signature(color2, prs2)
        if signature1 == signature2:
            return True
        if signature1[0] != "rgb" or signature2[0] != "rgb":
            return False
        r1, g1, b1, brightness1 = signature1[1:]
        r2, g2, b2, brightness2 = signature2[1:]
        return brightness1 == brightness2 and (
            sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)
            <= color_tol
        )

    def approx_equal(v1: Any, v2: Any) -> bool:
        if v1 == v2:
            return True
        if v1 == 0 and v2 == 0:
            return True
        if v1 == 0 or v2 == 0:
            return False
        return abs(v1 - v2) / max(abs(v1), abs(v2)) <= tol

    def nonempty_runs(para: Any) -> list[Any]:
        return [r for r in para.runs if (r.text or "").strip() != ""]

    get = opts.get
    if len(prs1.slides) != len(prs2.slides):
        if get("examine_number_of_slides", True):
            return 0.0
        # Extra deliverable slides are tolerated; missing gold slides are not.
        if len(prs1.slides) < len(prs2.slides):
            return 0.0
        slides1 = list(prs1.slides)[: len(prs2.slides)]
        slides2 = list(prs2.slides)
    else:
        slides1, slides2 = prs1.slides, prs2.slides

    if not slides1 or not slides2:
        return 0.0

    for slide1, slide2 in zip(slides1, slides2, strict=True):
        if not _compare_slide(
            slide1,
            slide2,
            get,
            color_equal,
            approx_equal,
            nonempty_runs,
            MSO_SHAPE_TYPE,
            PP_ALIGN,
        ):
            return 0.0
    return 1.0


def _slide_background_color(slide: Any) -> Any:
    """Reads a slide's solid background color, following a master inherit.

    Args:
        slide: A presentation slide.

    Returns:
        The background color format, or ``None`` when it is not a solid fill.
    """
    candidates = (
        slide,
        slide.slide_layout,
        slide.slide_layout.slide_master,
    )
    for owner in candidates:
        fill = owner.background.fill
        if fill.type == 1:
            try:
                resolved = _resolved_color_node(fill.fore_color._color._xClr, slide.part)
            except (AttributeError, TypeError, ValueError):
                resolved = None
            return resolved if resolved is not None else fill.fore_color
        if fill.type != 5:
            return None
    return None


def _compare_run_fonts(
    run1: Any,
    run2: Any,
    paragraph1: Any,
    paragraph2: Any,
    shape1: Any,
    shape2: Any,
    get: Any,
    color_equal: Any,
) -> bool:
    """Compares font attributes of two runs per the examine flags.

    Args:
        run1: Deliverable run.
        run2: Gold run.
        get: ``options.get`` accessor.
        color_equal: Tolerant RGB equality predicate.

    Returns:
        ``True`` when every examined font attribute matches.
    """
    font1 = _effective_run_font(run1, paragraph1, shape1)
    font2 = _effective_run_font(run2, paragraph2, shape2)
    if font1.get("name") != font2.get("name") and get("examine_font_name", True):
        return False
    if font1.get("size") != font2.get("size") and get("examine_font_size", True):
        return False
    if font1["bold"] != font2["bold"] and get("examine_font_bold", True):
        return False
    if font1["italic"] != font2["italic"] and get("examine_font_italic", True):
        return False
    if get("examine_color_rgb", True) and not color_equal(
        font1["color"], font2["color"]
    ):
        return False
    if font1["underline"] != font2["underline"] and get(
        "examine_font_underline", True
    ):
        return False
    if get("examine_strike_through", True) and font1["strike"] != font2["strike"]:
        return False
    return True


def _part_payload_digest(part: Any, seen: set[int] | None = None) -> bytes:
    """Hash a related OPC part and its descendants without trusting rel IDs."""
    import hashlib

    visited = seen if seen is not None else set()
    key = id(part)
    if key in visited:
        return b"cycle"
    visited.add(key)
    digest = hashlib.sha256()
    digest.update(getattr(part, "blob", b""))
    relationships = sorted(
        getattr(part, "rels", {}).values(),
        key=lambda rel: (rel.reltype, rel.target_ref),
    )
    for relationship in relationships:
        digest.update(relationship.reltype.encode("utf-8", errors="replace"))
        if relationship.is_external:
            digest.update(relationship.target_ref.encode("utf-8", errors="replace"))
        else:
            digest.update(_part_payload_digest(relationship.target_part, visited))
    return digest.digest()


def _shape_payload_signature(shape: Any) -> bytes | None:
    """Canonicalize a relationship-bearing shape and hash embedded payloads."""
    import hashlib
    from copy import deepcopy

    from lxml import etree

    relationship_ns = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    try:
        root = deepcopy(shape._element)
        part = shape.part
        payload = hashlib.sha256()
        for element in list(root.iter()):
            if etree.QName(element).localname == "xfrm":
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                continue
            for attribute, relationship_id in list(element.attrib.items()):
                if not attribute.startswith(f"{{{relationship_ns}}}"):
                    continue
                relationship = part.rels[relationship_id]
                if relationship.is_external:
                    replacement = relationship.target_ref.encode("utf-8")
                else:
                    replacement = _part_payload_digest(relationship.target_part)
                element.set(attribute, hashlib.sha256(replacement).hexdigest())
                payload.update(replacement)
        payload.update(etree.tostring(root, method="c14n"))
        return payload.digest()
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _shape_has_payload(shape_type: Any, mso_shape_type: Any) -> bool:
    """Whether a shape type needs payload comparison beyond text/geometry."""
    names = (
        "CANVAS",
        "CHART",
        "DIAGRAM",
        "EMBEDDED_OLE_OBJECT",
        "FORM_CONTROL",
        "IGX_GRAPHIC",
        "INK",
        "LINKED_OLE_OBJECT",
        "LINKED_PICTURE",
        "MEDIA",
        "OLE_CONTROL_OBJECT",
        "PICTURE",
        "SCRIPT_ANCHOR",
        "WEB_VIDEO",
    )
    return shape_type in {
        member for name in names if (member := getattr(mso_shape_type, name, None))
    }


def _compare_shape(
    shape1: Any,
    shape2: Any,
    get: Any,
    color_equal: Any,
    approx_equal: Any,
    nonempty_runs: Any,
    mso_shape_type: Any,
    pp_align: Any,
) -> bool:
    """Compare one shape pair, recursively descending through groups."""
    if shape1.shape_type != shape2.shape_type:
        return False

    if get("examine_shape", True) and any(
        not approx_equal(value1, value2)
        for value1, value2 in (
            (shape1.left, shape2.left),
            (shape1.top, shape2.top),
            (shape1.width, shape2.width),
            (shape1.height, shape2.height),
            (shape1.rotation, shape2.rotation),
        )
    ):
        return False

    if shape1.shape_type == mso_shape_type.GROUP:
        if len(shape1.shapes) != len(shape2.shapes):
            return False
        return all(
            _compare_shape(
                child1,
                child2,
                get,
                color_equal,
                approx_equal,
                nonempty_runs,
                mso_shape_type,
                pp_align,
            )
            for child1, child2 in zip(shape1.shapes, shape2.shapes, strict=True)
        )

    if get("examine_image_size", False) and shape1.shape_type in {
        mso_shape_type.PICTURE,
        getattr(mso_shape_type, "LINKED_PICTURE", None),
    }:
        if not approx_equal(shape1.width, shape2.width) or not approx_equal(
            shape1.height, shape2.height
        ):
            return False

    if get("examine_modify_height", False):
        no_text = not getattr(shape1, "has_text_frame", False)
        freeform = shape1.shape_type == mso_shape_type.FREEFORM
        if (no_text or freeform) and not approx_equal(shape1.height, shape2.height):
            return False

    if shape1.shape_type == mso_shape_type.TABLE and not _compare_table(
        shape1, shape2, get, color_equal, nonempty_runs, pp_align
    ):
        return False

    has_text = getattr(shape1, "has_text_frame", False)
    if has_text:
        if shape1.text.strip() != shape2.text.strip() and get("examine_text", True):
            return False
        if not _compare_text_frame(
            shape1, shape2, get, color_equal, nonempty_runs, pp_align
        ):
            return False

    if _shape_has_payload(shape1.shape_type, mso_shape_type):
        signature1 = _shape_payload_signature(shape1)
        signature2 = _shape_payload_signature(shape2)
        if signature1 is None or signature2 is None or signature1 != signature2:
            return False

    return True


def _compare_slide(
    slide1: Any,
    slide2: Any,
    get: Any,
    color_equal: Any,
    approx_equal: Any,
    nonempty_runs: Any,
    mso_shape_type: Any,
    pp_align: Any,
) -> bool:
    """Compares one slide pair per the examine flags.

    Args:
        slide1: Deliverable slide.
        slide2: Gold slide.
        get: ``options.get`` accessor.
        color_equal: Tolerant RGB equality predicate.
        approx_equal: Relative geometry equality predicate.
        nonempty_runs: Filters runs down to those carrying text.
        mso_shape_type: ``MSO_SHAPE_TYPE`` enum.
        pp_align: ``PP_ALIGN`` enum.

    Returns:
        ``True`` when every examined aspect of the slide matches.
    """
    if get("examine_background_color", True):
        bg1 = _slide_background_color(slide1)
        bg2 = _slide_background_color(slide2)
        if bg1 is None and bg2 is None:
            pass
        elif bg1 is None or bg2 is None:
            return False
        elif not color_equal(bg1, bg2):
            return False

    if get("examine_note", True):
        note1 = (slide1.notes_slide.notes_text_frame.text or "").strip()
        note2 = (slide2.notes_slide.notes_text_frame.text or "").strip()
        if note1 != note2:
            return False

    if len(slide1.shapes) != len(slide2.shapes):
        return False

    for shape1, shape2 in zip(slide1.shapes, slide2.shapes, strict=True):
        if not _compare_shape(
            shape1,
            shape2,
            get,
            color_equal,
            approx_equal,
            nonempty_runs,
            mso_shape_type,
            pp_align,
        ):
            return False

    return True


def _paragraph_property_elements(paragraph: Any, shape: Any) -> list[Any]:
    """Collect paragraph properties through placeholder and master inheritance."""
    from pptx.oxml.ns import qn

    elements: list[Any] = []
    direct = getattr(paragraph._p, "pPr", None)
    if direct is not None:
        elements.append(direct)

    current = shape
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        try:
            list_style = current.text_frame._txBody.find(qn("a:lstStyle"))
            level = list_style.find(qn(f"a:lvl{paragraph.level + 1}pPr"))
        except (AttributeError, ValueError):
            level = None
        if level is not None:
            elements.append(level)
        try:
            current = current._base_placeholder
        except (AttributeError, KeyError, ValueError):
            current = None

    try:
        part = shape.part
        master = part.slide.slide_layout.slide_master
        text_styles = master.element.find(qn("p:txStyles"))
        placeholder_name = (
            shape.placeholder_format.type.name
            if getattr(shape, "is_placeholder", False)
            else ""
        )
        if "TITLE" in placeholder_name:
            style_name = "p:titleStyle"
        elif placeholder_name in {"BODY", "OBJECT", "SUBTITLE"}:
            style_name = "p:bodyStyle"
        else:
            style_name = "p:otherStyle"
        style = text_styles.find(qn(style_name)) if text_styles is not None else None
        level = (
            style.find(qn(f"a:lvl{paragraph.level + 1}pPr"))
            if style is not None
            else None
        )
        if level is not None:
            elements.append(level)
    except (AttributeError, KeyError, ValueError):
        pass
    return elements


def _bullet_nodes_signature(parent: Any) -> tuple[bytes, ...] | None:
    """Return canonical OOXML bullet declarations from paragraph properties."""
    from lxml import etree

    if parent is None:
        return None
    nodes = [
        child
        for child in parent
        if etree.QName(child).localname.startswith("bu")
    ]
    if not nodes:
        return None
    return tuple(etree.tostring(node, method="c14n") for node in nodes)


def _paragraph_bullet_signature(paragraph: Any, shape: Any) -> tuple[Any, ...]:
    """Read direct, placeholder, layout, and master bullet semantics."""
    for properties in _paragraph_property_elements(paragraph, shape):
        if inherited := _bullet_nodes_signature(properties):
            return "bullet", inherited
    return ("none",)


def _paragraph_alignment_signature(paragraph: Any, shape: Any) -> str:
    """Return effective paragraph alignment through the presentation hierarchy."""
    for properties in _paragraph_property_elements(paragraph, shape):
        alignment = properties.get("algn")
        if alignment:
            return alignment
    return "l"


def _compare_text_frame(
    shape1: Any,
    shape2: Any,
    get: Any,
    color_equal: Any,
    nonempty_runs: Any,
    pp_align: Any,
) -> bool:
    """Compares the paragraphs and runs of two text-bearing shapes.

    Args:
        shape1: Deliverable shape.
        shape2: Gold shape.
        get: ``options.get`` accessor.
        color_equal: Tolerant RGB equality predicate.
        nonempty_runs: Filters runs down to those carrying text.
        pp_align: ``PP_ALIGN`` enum.

    Returns:
        ``True`` when text, alignment, indent, and run fonts match.
    """
    paras1 = shape1.text_frame.paragraphs
    paras2 = shape2.text_frame.paragraphs
    if len(paras1) != len(paras2):
        return False

    for para1, para2 in zip(paras1, paras2, strict=True):
        if get("examine_bullets", True) and _paragraph_bullet_signature(
            para1, shape1
        ) != _paragraph_bullet_signature(para2, shape2):
            return False
        if get("examine_alignment", True) and _paragraph_alignment_signature(
            para1, shape1
        ) != _paragraph_alignment_signature(para2, shape2):
            return False
        if para1.text != para2.text and get("examine_text", True):
            return False
        if para1.level != para2.level and get("examine_indent", True):
            return False

        runs1, runs2 = para1.runs, para2.runs
        if (para1.text or "").strip() == "" and (para2.text or "").strip() == "":
            runs1, runs2 = nonempty_runs(para1), nonempty_runs(para2)
        if len(runs1) != len(runs2):
            return False
        for run1, run2 in zip(runs1, runs2, strict=True):
            if not _compare_run_fonts(
                run1, run2, para1, para2, shape1, shape2, get, color_equal
            ):
                return False
    return True


def _compare_table(
    shape1: Any,
    shape2: Any,
    get: Any,
    color_equal: Any,
    nonempty_runs: Any,
    pp_align: Any,
) -> bool:
    """Compares two table shapes cell by cell.

    Args:
        shape1: Deliverable table shape.
        shape2: Gold table shape.
        get: ``options.get`` accessor.
        color_equal: Tolerant RGB equality predicate.
        nonempty_runs: Filters runs down to those carrying text.
        pp_align: ``PP_ALIGN`` enum.

    Returns:
        ``True`` when table shape and every cell's run fonts match.
    """
    table1, table2 = shape1.table, shape2.table
    if len(table1.rows) != len(table2.rows) or len(table1.columns) != len(
        table2.columns
    ):
        return False
    for row in range(len(table1.rows)):
        for col in range(len(table1.columns)):
            cell1 = table1.cell(row, col)
            cell2 = table2.cell(row, col)
            paras1 = cell1.text_frame.paragraphs
            paras2 = cell2.text_frame.paragraphs
            if len(paras1) != len(paras2):
                return False
            for para1, para2 in zip(paras1, paras2, strict=True):
                if get("examine_bullets", True) and _paragraph_bullet_signature(
                    para1, cell1
                ) != _paragraph_bullet_signature(para2, cell2):
                    return False
                if get("examine_alignment", True) and _paragraph_alignment_signature(
                    para1, cell1
                ) != _paragraph_alignment_signature(para2, cell2):
                    return False
                if para1.level != para2.level and get("examine_indent", True):
                    return False
                # Compare table cell text content when examine_text is enabled
                if para1.text != para2.text and get("examine_text", True):
                    return False
                runs1, runs2 = para1.runs, para2.runs
                if (para1.text or "").strip() == "" and (para2.text or "").strip() == "":
                    runs1, runs2 = nonempty_runs(para1), nonempty_runs(para2)
                if len(runs1) != len(runs2):
                    return False
                for run1, run2 in zip(runs1, runs2, strict=True):
                    if not _compare_run_fonts(
                        run1,
                        run2,
                        para1,
                        para2,
                        cell1,
                        cell2,
                        get,
                        color_equal,
                    ):
                        return False
    return True


def check_slide_numbers_color(path: Path) -> float:
    """Check whether enabled slide-number placeholders resolve to red."""
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER
    from pptx.oxml.ns import qn

    presentation = Presentation(str(path))

    def declared_rgb(element: Any) -> tuple[int, int, int] | None:
        if element is None:
            return None
        for solid_fill in element.iter(qn("a:solidFill")):
            children = list(solid_fill)
            if not children:
                return (-1, -1, -1)
            color = children[0]
            if color.tag != qn("a:srgbClr"):
                # A theme/system/preset declaration overrides inheritance but
                # cannot prove the exact sRGB red required by this command.
                return (-1, -1, -1)
            value = color.get("val")
            if value is None or len(value) != 6:
                return (-1, -1, -1)
            try:
                parsed = bytes.fromhex(value)
            except ValueError:
                return (-1, -1, -1)
            return parsed[0], parsed[1], parsed[2]
        return None

    def rgb_from_shape(shape: Any) -> tuple[int, int, int] | None:
        if not getattr(shape, "has_text_frame", False):
            return None
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                try:
                    if run.font.color.rgb is not None:
                        return tuple(run.font.color.rgb)
                    if run.font.color.type is not None:
                        return (-1, -1, -1)
                except (AttributeError, ValueError):
                    pass
            for child in paragraph._p:
                if (color := declared_rgb(child)) is not None:
                    return color
        return declared_rgb(shape._element)

    def effective_rgb(shape: Any) -> tuple[int, int, int] | None:
        current = shape
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if (color := rgb_from_shape(current)) is not None:
                return color
            try:
                current = current._base_placeholder
            except (AttributeError, KeyError, ValueError):
                current = None
        return None

    def slide_numbers_enabled(slide: Any) -> bool:
        for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
            header_footer = owner._element.find(qn("p:hf"))
            if header_footer is not None and "sldNum" in header_footer.attrib:
                return header_footer.get("sldNum") not in {"0", "false", "off"}
        return True

    def number_placeholders(owner: Any) -> list[Any]:
        return [
            shape
            for shape in owner.shapes
            if getattr(shape, "is_placeholder", False)
            and shape.placeholder_format.type == PP_PLACEHOLDER.SLIDE_NUMBER
        ]

    found = False
    for slide in presentation.slides:
        if not slide_numbers_enabled(slide):
            continue
        # A slide need not materialize footer placeholders in its own shape
        # tree. PowerPoint renders the nearest layout/master placeholder, so
        # inspect exactly the closest level that declares one.
        placeholders: list[Any] = []
        for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
            placeholders = number_placeholders(owner)
            if placeholders:
                break
        for shape in placeholders:
            found = True
            if effective_rgb(shape) != (255, 0, 0):
                return 0.0
    return 1.0 if found else 0.0


def evaluate_presentation_fill_to_rgb_distance(
    path: Path,
    *,
    rgb: tuple[int, int, int],
    original_rgb: tuple[int, int, int] | None = None,
) -> float:
    """Scores how close each slide's background fill is to a target color.

    Args:
        path: Resolved PPTX file path.
        rgb: Target background color.
        original_rgb: Optional pre-edit color scored as a full match.

    Returns:
        A similarity score in ``[0, 1]`` averaged across slides.
    """
    from math import sqrt

    from pptx import Presentation

    max_dist = sqrt(255**2 + 255**2 + 255**2)

    def get_rgb(color: Any) -> Any:
        try:
            return color.rgb if hasattr(color, "rgb") else None
        except Exception:
            return None

    def slide_distance(slide: Any) -> float:
        fill = slide.background.fill
        color_rgb: Any = None
        if fill.type == 1:
            color_rgb = get_rgb(fill.fore_color)
        elif fill.type == 5:
            master_fill = slide.slide_layout.slide_master.background.fill
            if master_fill.type == 1:
                color_rgb = get_rgb(master_fill.fore_color)
            else:
                return 1.0
        else:
            return 1.0
        if color_rgb is None:
            return 1.0
        r1, g1, b1 = color_rgb
        # If slide matches original_rgb, distance is 0 (perfect match)
        if original_rgb is not None and (r1, g1, b1) == tuple(original_rgb):
            return 0.0
        r2, g2, b2 = rgb
        distance = sqrt(
            (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
        ) / max_dist
        return min(1.0, max(0.0, distance))

    prs = Presentation(str(path))
    slides = list(prs.slides)
    if not slides:
        # Empty deck scores 0.0 (no valid content to check)
        return 0.0
    score = 1.0 - sum(slide_distance(slide) for slide in slides) / len(slides)
    return min(1.0, max(0.0, score))


COMMANDS = (
    ExtractText(),
    RenderSlides(),
    InspectDeck(),
    CompareFiles(),
    CheckSlideNumbersColor(),
    FillRgbDistance(),
)
