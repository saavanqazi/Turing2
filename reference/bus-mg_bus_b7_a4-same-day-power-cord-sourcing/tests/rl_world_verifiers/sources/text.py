from ..models import StrictModel
from ..source_types import SourceCommand, SourceContext


class TextExtractTextInput(StrictModel):
    """Input for text.extract_text.

    Attributes:
        path: Workspace-relative text path.
    """

    path: str


class TextExtractTextOutput(StrictModel):
    """Output from text.extract_text.

    Attributes:
        text: Raw UTF-8 text.
        truncated: Whether the file was longer than the source content limit
            and the text above is only its leading portion. A negative
            assertion cannot be satisfied by content that was cut, so the
            runner needs to know a read was partial.
    """

    text: str
    truncated: bool = False


class ExtractText(SourceCommand[TextExtractTextInput, TextExtractTextOutput]):
    """Reads any workspace file as raw UTF-8 text.

    This command intentionally does not validate file extension. It is the
    generic plain-text escape hatch for authors. If the target cannot be read as
    UTF-8, the verifier reports that as malformed agent output instead of
    rejecting the source definition.
    """

    name = "extract_text"
    input_model = TextExtractTextInput
    output_model = TextExtractTextOutput

    def run(self, source_input: TextExtractTextInput, context: SourceContext) -> TextExtractTextOutput:
        """Reads UTF-8 text from a workspace file.

        Args:
            source_input: Validated text extraction input.
            context: Source runtime context.

        Returns:
            Raw text capped to the configured source content limit.

        """
        resolved = context.resolve_path(source_input.path)
        raw = resolved.read_text(encoding="utf-8-sig")
        limit = context.max_content_chars
        return TextExtractTextOutput(text=raw[:limit], truncated=len(raw) > limit)


COMMANDS = (ExtractText(),)
