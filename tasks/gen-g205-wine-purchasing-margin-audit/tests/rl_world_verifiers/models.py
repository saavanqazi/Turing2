import json
import math
from typing import Any, Literal

from functools import lru_cache

from jsonpath_ng.ext import parse as _jsonpath_parse_uncached

# jsonpath_ng rebuilds its PLY LALR parse table on EVERY parse call, and every
# check's path is parsed twice per replay (schema validation + extraction) —
# measured 2026-09-02 as 96% of a replay's wall time (3.75s -> 0.072s warm with
# this cache, verdicts byte-identical). parse() is pure in its string argument,
# so memoization cannot change behavior.
jsonpath_parse = lru_cache(maxsize=4096)(_jsonpath_parse_uncached)
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

Comparison = Literal[
    "equals",
    "not_equals",
    "approx_equals",
    "greater_than",
    "less_than",
    "greater_than_equal",
    "less_than_equal",
    "contains",
    "not_contains",
    "regex_match",
    "not_regex_match",
    "in_array",
    "not_in_array",
    "table_equals",
    "object_equals",
]


class StrictModel(BaseModel):
    """Base Pydantic model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class Config(StrictModel):
    """Optional global verifier configuration.

    Attributes:
        models: LiteLLM model names used for rubric judge attempts.
        temperature: Temperature passed to LiteLLM rubric judge calls.
    """

    models: list[str] | None = Field(default=None, min_length=1, max_length=3)
    temperature: float | None = None

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: list[str] | None) -> list[str] | None:
        """Validates LiteLLM-native model names.

        Args:
            value: Configured LiteLLM model names.

        Returns:
            The unchanged model list when valid.

        Raises:
            ValueError: If a model name is blank or uses colon-style provider
                separators.
        """
        if value is None:
            return value
        for model in value:
            if not model.strip():
                raise ValueError("config.models entries must be non-empty")
            if ":" in model:
                raise ValueError("config.models must use LiteLLM-native model names such as anthropic/claude-sonnet")
        if len(value) % 2 == 0:
            raise ValueError(
                "config.models must have an odd count (1 or 3): an even count makes every "
                "judge disagreement a fail-closed tie, wasting a model's signal"
            )
        return value


class VerifierMetadata(StrictModel):
    """Author-provided verifier metadata.

    Attributes:
        how_justification: Explanation of how this verifier checks the task.
        why_justification: Explanation of why this verifier matters.
        tag: Optional human grouping label.
        weight: Optional authored weight from zero to one.
    """

    how_justification: str
    why_justification: str
    tag: str | None = None
    weight: float | None = Field(default=None, gt=0, le=1)


class FileCommand(StrictModel):
    """File source command declaration.

    Attributes:
        type: Source namespace, such as ``json`` or ``docx``.
        command: Command name within the namespace.
        arguments: Command-specific arguments.
    """

    type: str
    command: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ResponseCommand(StrictModel):
    """Response source command declaration.

    Attributes:
        command: Response command name.
        arguments: Optional empty argument object. Non-empty arguments are not
            allowed for response commands yet.
    """

    command: str
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=0)


class Source(StrictModel):
    """Verifier source declaration.

    Attributes:
        type: Source family.
        file: File source command declaration when ``type`` is ``file``.
        response: Response source command declaration when ``type`` is
            ``response``.
    """

    type: Literal["file", "response"]
    file: FileCommand | None = None
    response: ResponseCommand | None = None

    @model_validator(mode="after")
    def validate_branch(self) -> "Source":
        """Validates that source details match the selected source family.

        Returns:
            The validated source declaration.

        Raises:
            ValueError: If source branch config does not match the selected
                source type.
        """
        if self.type == "file" and self.file is None:
            raise ValueError("file source requires file config")
        if self.type == "file" and self.response is not None:
            raise ValueError("file source must not include response config")
        if self.type == "response" and self.response is None:
            raise ValueError("response source requires response config")
        if self.type == "response" and self.file is not None:
            raise ValueError("response source must not include file config")
        return self

    @property
    def source_name(self) -> str:
        """Returns the registered source command name.

        Returns:
            Dotted source command name used by ``SourceRegistry``.
        """
        if self.file is not None:
            return f"{self.file.type}.{self.file.command}"
        assert self.response is not None
        return f"response.{self.response.command}"

    @property
    def arguments(self) -> dict[str, Any]:
        """Returns command arguments for the selected source branch.

        Returns:
            Command-specific argument mapping.
        """
        if self.file is not None:
            return self.file.arguments
        assert self.response is not None
        return {}


class Tolerance(StrictModel):
    """Numeric comparison tolerance config.

    Attributes:
        absolute: Optional absolute tolerance.
        relative: Optional relative tolerance.
    """

    absolute: float | None = None
    relative: float | None = None


class DeterministicConfig(StrictModel):
    """Deterministic assertion details.

    Attributes:
        path: JSONPath evaluated against the source output root.
        comparison: Comparator used to compare actual and expected values.
        tolerance: Optional numeric tolerance for approximate comparisons.
    """

    path: str
    comparison: Comparison
    tolerance: Tolerance | None = None

    @field_validator("path")
    @classmethod
    def validate_jsonpath(cls, value: str) -> str:
        """Validates deterministic JSONPath syntax at schema parse time.

        Args:
            value: JSONPath expression.

        Returns:
            The unchanged JSONPath when valid.

        Raises:
            ValueError: If the JSONPath cannot be parsed.
        """
        try:
            jsonpath_parse(value)
        except Exception as exc:
            raise ValueError(f"invalid JSONPath: {value}") from exc
        return value


class RubricConfig(StrictModel):
    """LLM rubric assertion details.

    Attributes:
        prompt: Rubric text evaluated by the judge.
    """

    prompt: str


class Assertion(StrictModel):
    """Verifier assertion declaration.

    Attributes:
        type: Assertion family, either deterministic or rubric.
        expected: Expected value. Rubric expected values must be booleans.
        deterministic: Deterministic assertion details.
        rubric: Rubric assertion details.
    """

    type: Literal["deterministic", "rubric"]
    expected: Any
    deterministic: DeterministicConfig | None = None
    rubric: RubricConfig | None = None

    @model_validator(mode="after")
    def validate_branch(self) -> "Assertion":
        """Validates that assertion details match the selected type.

        Returns:
            The validated assertion.

        Raises:
            ValueError: If required branch config is missing or forbidden branch
                config is present.
        """
        if self.type == "deterministic":
            if self.deterministic is None:
                raise ValueError("deterministic assertion requires deterministic config")
            if self.rubric is not None:
                raise ValueError("deterministic assertion must not include rubric config")
            validate_deterministic_expected(self.expected, self.deterministic.comparison)
        if self.type == "rubric":
            if self.rubric is None:
                raise ValueError("rubric assertion requires rubric config")
            if self.deterministic is not None:
                raise ValueError("rubric assertion must not include deterministic config")
            if not isinstance(self.expected, bool):
                raise ValueError("rubric assertion expected value must be boolean")
        return self


def validate_deterministic_expected(expected: Any, comparison: Comparison) -> None:
    """Validates expected value shape for deterministic comparisons.

    Args:
        expected: Expected value from the verifier assertion.
        comparison: Comparator selected by the verifier assertion.

    Raises:
        ValueError: If the expected value cannot be used by the comparator.
    """
    numeric_comparisons = {
        "approx_equals",
        "greater_than",
        "less_than",
        "greater_than_equal",
        "less_than_equal",
    }
    if comparison in numeric_comparisons and not is_json_number(expected):
        raise ValueError(f"{comparison} expected value must be a JSON number")
    if comparison in {"in_array", "not_in_array"} and not isinstance(expected, list):
        raise ValueError(f"{comparison} expected value must be an array")
    if comparison in {"regex_match", "not_regex_match"} and not isinstance(expected, str):
        raise ValueError(f"{comparison} expected value must be a regex string")
    if comparison == "table_equals":
        validate_table_equals_expected(expected)
    if comparison == "object_equals":
        validate_object_equals_expected(expected)
    # Gold is static config, so this is validated once at load rather than on
    # every comparison. nan/inf here is an authoring mistake: every comparison
    # against them is misleading rather than merely wrong.
    if has_non_finite(expected):
        raise ValueError(f"{comparison} expected value contains a non-finite number")


#: The four value types of the 2026-09-03 equivalence contract. ``number``
#: parses then compares within a tolerance, ``text`` strips + casefolds +
#: collapses whitespace, ``id`` strips only, ``date`` canonicalises to
#: YYYY-MM-DD. The normalisers live in ``compare``; this is the vocabulary.
CELL_TYPES = ("number", "text", "id", "date", "json_number")

#: Every key a table_equals expected object may carry.
TABLE_EQUALS_KEYS = frozenset(
    {"id_column", "rows", "row_set", "columns", "row_set_ordered", "columns_ordered", "numeric_tolerance",
     "id_pattern", "cell_types"}
)

#: Every key an object_equals expected object may carry.
OBJECT_EQUALS_KEYS = frozenset({"keys", "closed"})

#: Every key one object_equals ``keys`` entry may carry.
OBJECT_EQUALS_KEY_SPEC_KEYS = frozenset({"value", "tolerance", "type", "unordered"})


def validate_table_equals_expected(expected: Any) -> None:
    """Validates the ``table_equals`` expected object at schema parse time.

    Mirrors the vendored b21 checks (``id_column``, ``rows``, ``row_set``) and
    adds the hardened options: closed ``columns``, ``row_set_ordered``,
    ``numeric_tolerance`` and the contract's ``cell_types`` (column ->
    ``number`` | ``text`` | ``id`` | ``date``). Unknown keys are rejected so a
    typo cannot silently weaken a verifier.

    Args:
        expected: Expected value from the verifier assertion.

    Raises:
        ValueError: If the expected object cannot be used by ``table_equals``.
    """
    if not isinstance(expected, dict) or not isinstance(expected.get("id_column"), str) \
            or not isinstance(expected.get("rows"), dict):
        raise ValueError(
            "table_equals expected value must be an object with a string "
            "'id_column' and a 'rows' mapping of record id -> {column: cell}"
        )
    unknown = sorted(set(expected) - TABLE_EQUALS_KEYS)
    if unknown:
        raise ValueError(f"table_equals expected has unknown keys: {', '.join(unknown)}")
    for record_id, cells in expected["rows"].items():
        if not isinstance(cells, dict) or not all(
            isinstance(cell, str) for cell in cells.values()
        ):
            raise ValueError(
                f"table_equals rows[{record_id!r}] must map column names to "
                "string cell values"
            )
    row_set = expected.get("row_set")
    if "row_set" in expected and (
        not isinstance(row_set, list) or not all(isinstance(item, str) for item in row_set)
    ):
        raise ValueError("table_equals 'row_set' must be a list of record id strings")
    if "columns_ordered" in expected:
        if not isinstance(expected["columns_ordered"], bool):
            raise ValueError("table_equals 'columns_ordered' must be a boolean")
        if expected["columns_ordered"] and expected.get("columns") is None:
            raise ValueError("table_equals 'columns_ordered' requires 'columns'")
    if "row_set_ordered" in expected:
        if not isinstance(expected["row_set_ordered"], bool):
            raise ValueError("table_equals 'row_set_ordered' must be a boolean")
        if expected["row_set_ordered"] and row_set is None:
            raise ValueError("table_equals 'row_set_ordered' requires 'row_set'")
    if "columns" in expected:
        columns = expected["columns"]
        if (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(column, str) for column in columns)
        ):
            raise ValueError("table_equals 'columns' must be a non-empty list of column names")
        if expected["id_column"] not in columns:
            raise ValueError("table_equals 'columns' must include the id_column")
    if "id_pattern" in expected:
        import re as _re
        if not isinstance(expected["id_pattern"], str):
            raise ValueError("table_equals 'id_pattern' must be a regex string")
        try:
            _re.compile(expected["id_pattern"])
        except _re.error as exc:
            raise ValueError(f"table_equals 'id_pattern' is not a valid regex: {exc}") from exc
    if "numeric_tolerance" in expected:
        tolerance = expected["numeric_tolerance"]
        if not is_json_number(tolerance) or tolerance < 0:
            raise ValueError("table_equals 'numeric_tolerance' must be a non-negative number")
    if "cell_types" in expected:
        cell_types = expected["cell_types"]
        if not isinstance(cell_types, dict) or not all(
            isinstance(column, str) and column.strip() for column in cell_types
        ):
            raise ValueError(
                "table_equals 'cell_types' must map column names to one of "
                + ", ".join(CELL_TYPES)
            )
        for column, cell_type in cell_types.items():
            if cell_type not in CELL_TYPES:
                raise ValueError(
                    f"table_equals cell_types[{column!r}] must be one of "
                    + ", ".join(CELL_TYPES) + f", got {cell_type!r}"
                )


def validate_object_equals_expected(expected: Any) -> None:
    """Validates the ``object_equals`` expected object at schema parse time.

    Mirrors the vendored b21 checks (``keys`` with per-key ``value`` and
    number-or-null ``tolerance``) and adds: unknown keys rejected, ``closed``
    must be boolean, tolerances must be non-negative and only accompany
    numeric values (a JSON number, a list of them, or a key typed ``number``),
    the contract's per-key ``type`` (``number`` | ``text`` | ``id`` |
    ``date``) and ``unordered`` (boolean, list values only).

    Args:
        expected: Expected value from the verifier assertion.

    Raises:
        ValueError: If the expected object cannot be used by ``object_equals``.
    """
    if not isinstance(expected, dict) or not isinstance(expected.get("keys"), dict):
        raise ValueError(
            "object_equals expected value must be an object with a 'keys' "
            "mapping of key -> {'value': ..., 'tolerance': number|null}"
        )
    unknown = sorted(set(expected) - OBJECT_EQUALS_KEYS)
    if unknown:
        raise ValueError(f"object_equals expected has unknown keys: {', '.join(unknown)}")
    if "closed" in expected and not isinstance(expected["closed"], bool):
        raise ValueError("object_equals 'closed' must be a boolean")
    for key, spec in expected["keys"].items():
        if not isinstance(spec, dict) or "value" not in spec:
            raise ValueError(f"object_equals keys[{key!r}] must carry a 'value'")
        unknown_spec = sorted(set(spec) - OBJECT_EQUALS_KEY_SPEC_KEYS)
        if unknown_spec:
            raise ValueError(
                f"object_equals keys[{key!r}] has unknown keys: {', '.join(unknown_spec)}"
            )
        value = spec["value"]
        value_type = spec.get("type")
        if value_type is not None and value_type not in CELL_TYPES:
            raise ValueError(
                f"object_equals keys[{key!r}] type must be one of "
                + ", ".join(CELL_TYPES) + f", got {value_type!r}"
            )
        if "unordered" in spec:
            if not isinstance(spec["unordered"], bool):
                raise ValueError(f"object_equals keys[{key!r}] 'unordered' must be a boolean")
            if spec["unordered"] and not isinstance(value, list):
                raise ValueError(
                    f"object_equals keys[{key!r}] 'unordered' requires a list value"
                )
        tolerance = spec.get("tolerance")
        if tolerance is None:
            continue
        if not is_json_number(tolerance) or tolerance < 0:
            raise ValueError(
                f"object_equals keys[{key!r}] tolerance must be a non-negative number or null"
            )
        numeric_value = is_json_number(value) or value_type == "number" or (
            isinstance(value, list) and bool(value) and all(is_json_number(v) for v in value)
        )
        if not numeric_value:
            raise ValueError(
                f"object_equals keys[{key!r}] tolerance requires a numeric value "
                "(or type 'number')"
            )


def has_non_finite(value: Any) -> bool:
    """Checks for a non-finite float anywhere in a JSON value.

    Args:
        value: Scalar, list, or dict to inspect.

    Returns:
        True when any nested float is nan/inf/-inf.
    """
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return any(has_non_finite(item) for item in value)
    if isinstance(value, dict):
        return any(has_non_finite(item) for item in value.values())
    return False


def is_json_number(value: Any) -> bool:
    """Checks whether a value is a finite JSON number in Python form.

    Args:
        value: Value to inspect.

    Returns:
        True for finite integer and float values except booleans.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


class VerifierDefinition(StrictModel):
    """One verifier definition from verifier.json.

    Attributes:
        name: Unique verifier name.
        metadata: Author-provided metadata.
        source: Source declaration.
        assertion: Assertion declaration.
    """

    name: str = Field(min_length=1)
    metadata: VerifierMetadata
    source: Source
    assertion: Assertion


class VerifierSpec(StrictModel):
    """Root verifier.json schema."""

    task_id: str
    config: Config | None = None
    verifiers: list[VerifierDefinition]

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_keys(cls, data: Any) -> Any:
        """Rejects old verifier schema keys before normal validation.

        Args:
            data: Raw verifier spec object.

        Returns:
            The unchanged object when it does not include legacy keys.

        Raises:
            ValueError: If a legacy top-level key is present.
        """
        if isinstance(data, dict):
            for key in ("schema_version", "judge", "assertions"):
                if key in data:
                    raise ValueError(f"legacy top-level key is not supported: {key}")
        return data

    @field_validator("verifiers")
    @classmethod
    def require_verifiers(cls, value: list[VerifierDefinition]) -> list[VerifierDefinition]:
        """Requires at least one verifier.

        Args:
            value: Parsed verifier definitions.

        Returns:
            The verifier list when it is non-empty.

        Raises:
            ValueError: If no verifiers are configured.
        """
        if not value:
            raise ValueError("verifier.json must contain at least one verifier")
        return value

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "VerifierSpec":
        """Validates cross-verifier schema rules.

        Returns:
            The validated verifier spec.

        Raises:
            ValueError: If names, judge config, or authored weights are invalid.
        """
        names = [verifier.name for verifier in self.verifiers]
        if len(names) != len(set(names)):
            raise ValueError("verifier names must be unique")

        has_rubric = any(v.assertion.type == "rubric" for v in self.verifiers)
        if has_rubric and (self.config is None or self.config.models is None or self.config.temperature is None):
            raise ValueError("config.models and config.temperature are required for rubric assertions")

        validate_weight_distribution(self.verifiers)
        return self


class LLMVerdict(StrictModel):
    """Structured verdict returned by the LLM judge.

    Attributes:
        verdict: Boolean rubric verdict.
        justification: Short evidence-based explanation for the verdict.
    """

    verdict: bool
    justification: str


def validate_weight_distribution(verifiers: list[VerifierDefinition]) -> None:
    """Validates authored verifier weights.

    Args:
        verifiers: Parsed verifier definitions.

    Raises:
        ValueError: If explicit weights cannot be distributed to one total.
    """
    weights = [v.metadata.weight for v in verifiers]
    explicit = [w for w in weights if w is not None]
    if not explicit:
        return

    explicit_sum = sum(explicit)
    unweighted = len([w for w in weights if w is None])
    if explicit_sum > 1 + 1e-9:
        raise ValueError("verifier weights must sum to at most 1")
    if unweighted and explicit_sum >= 1 - 1e-9:
        raise ValueError("unweighted verifiers require remaining positive weight")
    if not unweighted and abs(explicit_sum - 1) > 1e-9:
        raise ValueError("explicit verifier weights must sum to 1 when all verifiers are weighted")


def effective_weights(verifiers: list[VerifierDefinition]) -> dict[str, float]:
    """Computes effective verifier weights.

    Args:
        verifiers: Parsed verifier definitions.

    Returns:
        Mapping from verifier name to effective weight.
    """
    explicit_sum = sum(v.metadata.weight or 0 for v in verifiers)
    unweighted = [v for v in verifiers if v.metadata.weight is None]
    weights: dict[str, float] = {
        v.name: round(v.metadata.weight, 10) for v in verifiers if v.metadata.weight is not None
    }
    if unweighted:
        remaining = 1 - explicit_sum
        default = round(remaining / len(unweighted), 10)
        # Give every unweighted verifier the rounded default except the last, which
        # absorbs the remainder, so equal weights sum to `remaining` exactly. Per-
        # weight rounding otherwise left a perfect run scoring a hair under 1.0.
        head_total = default * (len(unweighted) - 1)
        for index, verifier in enumerate(unweighted):
            last = index == len(unweighted) - 1
            weights[verifier.name] = round(remaining - head_total, 10) if last else default
    return weights


def source_output_to_data(value: BaseModel | RootModel[Any]) -> Any:
    """Converts a Pydantic source output into JSON-like data.

    Args:
        value: Source output model.

    Returns:
        Model data suitable for JSONPath, serialization, or prompt rendering.
    """
    if isinstance(value, RootModel):
        return value.root
    return value.model_dump()


def jsonable(value: Any) -> Any:
    """Returns a JSON-serializable representation of a value.

    Args:
        value: Arbitrary Python value.

    Returns:
        The original value when JSON serialization supports it; otherwise its
        string representation.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
