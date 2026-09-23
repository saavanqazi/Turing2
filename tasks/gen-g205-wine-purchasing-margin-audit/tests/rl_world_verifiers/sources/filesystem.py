from ..models import StrictModel
from ..source_types import SourceCommand, SourceContext


class FilesystemCheckPathExistsInput(StrictModel):
    """Input for filesystem.check_path_exists.

    Attributes:
        path: Workspace-relative path to check.
    """

    path: str


class FilesystemCheckPathExistsOutput(StrictModel):
    """Output from filesystem.check_path_exists.

    Attributes:
        exists: Whether the path exists.
        is_file: Whether the path exists and is a regular file.
        is_directory: Whether the path exists and is a directory.
        size: Size in bytes. For a regular file this is the file size; for a
            directory or a non-existent path this is 0. A verifier can assert
            ``$.size > 0`` to reject empty (0-byte) deliverables that pass
            ``$.is_file == true`` without carrying any real content.
    """

    exists: bool
    is_file: bool
    is_directory: bool
    size: int


class CheckPathExists(SourceCommand[FilesystemCheckPathExistsInput, FilesystemCheckPathExistsOutput]):
    """Checks whether a workspace path exists."""

    name = "check_path_exists"
    input_model = FilesystemCheckPathExistsInput
    output_model = FilesystemCheckPathExistsOutput

    def run(
        self,
        source_input: FilesystemCheckPathExistsInput,
        context: SourceContext,
    ) -> FilesystemCheckPathExistsOutput:
        """Runs the path existence check.

        Args:
            source_input: Validated path check input.
            context: Source runtime context.

        Returns:
            Path existence, type, and size output.
        """
        resolved = context.resolve_path(source_input.path)
        is_file = resolved.is_file()
        return FilesystemCheckPathExistsOutput(
            exists=resolved.exists(),
            is_file=is_file,
            is_directory=resolved.is_dir(),
            size=resolved.stat().st_size if is_file else 0,
        )


COMMANDS = (CheckPathExists(),)
