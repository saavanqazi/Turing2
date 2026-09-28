import json
from pathlib import Path
from typing import Any

from pydantic import RootModel, field_validator

from ..models import StrictModel
from ..source_types import SourceDataError, SourceCommand, SourceContext


class JsonReadFileInput(StrictModel):
    """Input for json.read_file.

    Attributes:
        path: Workspace-relative JSON file path.
    """

    path: str

    @field_validator("path")
    @classmethod
    def require_json_extension(cls, value: str) -> str:
        """Requires the path to target a JSON file.

        Args:
            value: Workspace-relative path from verifier.json.

        Returns:
            The unchanged path when it has a JSON extension.

        Raises:
            ValueError: If the path does not end in ``.json``.
        """
        if Path(value).suffix.lower() != ".json":
            raise ValueError("json.read_file requires a .json file")
        return value


class JsonReadFileOutput(RootModel[Any]):
    """Output from json.read_file containing the parsed JSON value."""

    pass


class ReadFile(SourceCommand[JsonReadFileInput, JsonReadFileOutput]):
    """Reads and parses a JSON file from the workspace."""

    name = "read_file"
    input_model = JsonReadFileInput
    output_model = JsonReadFileOutput

    def run(self, source_input: JsonReadFileInput, context: SourceContext) -> JsonReadFileOutput:
        """Runs the JSON file read command.

        Args:
            source_input: Validated JSON read input.
            context: Source runtime context.

        Returns:
            Parsed JSON value wrapped as a root model.

        Raises:
            json.JSONDecodeError: If the file content is not valid JSON.
        """
        resolved = context.resolve_path(source_input.path)
        # utf-8-sig, matching the csv/md/text sources: Excel, PowerShell and
        # Notepad prefix a BOM, and plain utf-8 leaves it in the string so
        # json.loads rejects an otherwise correct answer.
        # ``json.loads`` keeps the LAST of two equal keys, so a wrong first
        # copy and a correct last copy would grade as correct; a duplicate
        # key is reported instead (same defect class as a duplicate CSV
        # header column).
        return JsonReadFileOutput.model_validate(
            json.loads(
                resolved.read_text(encoding="utf-8-sig"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceDataError(f"duplicate key {key!r} in JSON object")
        result[key] = value
    return result


COMMANDS = (ReadFile(),)
