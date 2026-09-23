"""Bounded HTML text and accessibility-tree source commands."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..models import StrictModel
from ..source_types import (
    SourceAuthoringError,
    SourceCapabilityError,
    SourceCommand,
    SourceContext,
    SourceDataError,
)

_ACCESSIBILITY_NAMESPACES = {
    "ubuntu": {
        "st": "https://accessibility.ubuntu.example.org/ns/state",
        "attr": "https://accessibility.ubuntu.example.org/ns/attributes",
        "cp": "https://accessibility.ubuntu.example.org/ns/component",
        "doc": "https://accessibility.ubuntu.example.org/ns/document",
        "docattr": "https://accessibility.ubuntu.example.org/ns/document/attributes",
        "txt": "https://accessibility.ubuntu.example.org/ns/text",
        "val": "https://accessibility.ubuntu.example.org/ns/value",
        "act": "https://accessibility.ubuntu.example.org/ns/action",
    },
    "windows": {
        "st": "https://accessibility.windows.example.org/ns/state",
        "attr": "https://accessibility.windows.example.org/ns/attributes",
        "cp": "https://accessibility.windows.example.org/ns/component",
        "doc": "https://accessibility.windows.example.org/ns/document",
        "docattr": "https://accessibility.windows.example.org/ns/document/attributes",
        "txt": "https://accessibility.windows.example.org/ns/text",
        "val": "https://accessibility.windows.example.org/ns/value",
        "act": "https://accessibility.windows.example.org/ns/action",
        "class": "https://accessibility.windows.example.org/ns/class",
    },
    "macos": {
        "st": "https://accessibility.macos.example.org/ns/state",
        "attr": "https://accessibility.macos.example.org/ns/attributes",
        "cp": "https://accessibility.macos.example.org/ns/component",
        "doc": "https://accessibility.macos.example.org/ns/document",
        "txt": "https://accessibility.macos.example.org/ns/text",
        "val": "https://accessibility.macos.example.org/ns/value",
        "act": "https://accessibility.macos.example.org/ns/action",
        "role": "https://accessibility.macos.example.org/ns/role",
    },
}


def _read_bounded_text(path: Path, limit: int) -> tuple[str, bool]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            text = handle.read(limit + 1)
    except UnicodeDecodeError as exc:
        raise SourceDataError(f"{path.name} is not valid UTF-8 text") from exc
    return text[:limit], len(text) > limit


def _fuzzy_ratio(actual: str, expected: str, *, partial: bool = False) -> float:
    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise SourceCapabilityError(
            "html fuzzy matching requires the rapidfuzz package"
        ) from exc
    # partial compares the best-matching region, so the score does not fall
    # with page length; plain ratio suits two similarly sized values.
    scorer = fuzz.partial_ratio if partial else fuzz.ratio
    return scorer(actual, expected) / 100.0


def _normalise_ws(value: str) -> str:
    return " ".join(value.split())


# Elements whose boundaries separate words for a reader (cells, rows, blocks,
# line breaks). Inline markup (<b>, <span>, ...) does not split a value.
_BLOCK_TAGS = frozenset(
    "address article aside blockquote br caption dd div dl dt fieldset figcaption "
    "figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section "
    "table tbody td tfoot th thead tr ul".split()
)


def _element_text(node) -> str:
    """Reader-visible text of ``node``: entities decoded, a space at every
    block/cell boundary so ``<td>UNMATCHED</td><td>2</td>`` reads
    ``UNMATCHED 2``, then whitespace collapsed."""
    parts: list[str] = []

    def walk(element) -> None:
        tag = element.tag if isinstance(element.tag, str) else ""
        block = tag.lower() in _BLOCK_TAGS
        if block:
            parts.append(" ")
        if element.text:
            parts.append(element.text)
        for child in element:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append(" ")

    walk(node)
    return _normalise_ws("".join(parts))


# Markup a reader never sees: inert ``<template>`` content, elements carrying
# the boolean ``hidden`` attribute, and elements hidden with an inline
# ``display:none``. The whole subtree is dropped rather than merely skipped for
# text, so a hidden decoy also stays out of a selector's match count and cannot
# satisfy — or spoil — a slot check. CSS in a stylesheet is still out of reach.
_DISPLAY_NONE_RE = re.compile(r"(?:^|;)\s*display\s*:\s*none\s*(?:;|!|$)", re.IGNORECASE)


def _is_hidden(element) -> bool:
    attrib = element.attrib
    # ``hidden`` is a boolean attribute: even hidden="false" hides the element.
    if "hidden" in attrib:
        return True
    style = attrib.get("style") or ""
    return _DISPLAY_NONE_RE.search(style) is not None


def _drop_invisible(element) -> None:
    """Remove every ``<template>`` and hidden subtree under ``element``.

    Top-down so a dropped subtree is never re-visited; ``drop_tree`` keeps each
    removed element's tail text, which belongs to the surviving parent.
    """
    for child in list(element):
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag.lower() == "template" or _is_hidden(child):
            child.drop_tree()
        else:
            _drop_invisible(child)


def _parse_html(text: str):
    """Tolerant HTML parse (lxml.html): doctype, void elements, entities and
    unclosed tags are all accepted; no network, no DTD, no entity expansion."""
    try:
        from lxml import etree, html as lxml_html  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SourceCapabilityError("html parsing requires the lxml package") from exc
    parser = lxml_html.HTMLParser(
        no_network=True, remove_comments=True, huge_tree=False
    )
    try:
        root = lxml_html.document_fromstring(text, parser=parser)
    except (etree.ParserError, ValueError) as exc:
        raise SourceDataError("html document is empty or cannot be parsed") from exc
    for junk in root.iter("script", "style"):
        junk.drop_tree()
    _drop_invisible(root)
    return root


def _page_text(text: str) -> str:
    if not text.strip():
        return ""
    return _element_text(_parse_html(text))


class SelectTextInput(StrictModel):
    """Arguments for ``html.select_text``."""

    path: str
    selector: str = Field(min_length=1, max_length=500)


class SelectTextOutput(StrictModel):
    """Rendered text of every element the CSS selector matches.

    ``text`` is the single element's text when exactly one element matched and
    null otherwise, so a check on ``$.text`` fails both a missing slot and a
    duplicated one (a hedge shape) without a second check on ``$.count``.
    """

    count: int
    texts: list[str]
    text: str | None
    truncated: bool


class SelectText(SourceCommand[SelectTextInput, SelectTextOutput]):
    """Select elements from a tolerant HTML parse and return their text."""

    name = "select_text"
    input_model = SelectTextInput
    output_model = SelectTextOutput

    def run(
        self, source_input: SelectTextInput, context: SourceContext
    ) -> SelectTextOutput:
        try:
            from cssselect import SelectorError  # type: ignore[import-not-found]
            from lxml.cssselect import CSSSelector  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependencies are declared
            raise SourceCapabilityError(
                "html.select_text requires lxml and cssselect"
            ) from exc
        resolved = context.resolve_path(source_input.path)
        text, truncated = _read_bounded_text(resolved, context.max_content_chars)
        if truncated:
            raise SourceDataError("html document exceeds the source content limit")
        try:
            selector = CSSSelector(source_input.selector)
        except SelectorError as exc:
            raise SourceAuthoringError(f"invalid CSS selector: {exc}") from exc
        if not text.strip():
            return SelectTextOutput(count=0, texts=[], text=None, truncated=truncated)
        root = _parse_html(text)
        texts = [_element_text(node) for node in selector(root)]
        return SelectTextOutput(
            count=len(texts),
            texts=texts,
            text=texts[0] if len(texts) == 1 else None,
            truncated=truncated,
        )


class IncludeExcludeInput(StrictModel):
    """Arguments for ``html.check_include_exclude``."""

    path: str
    include: list[str] = Field(default_factory=list, max_length=100)
    exclude: list[str] = Field(default_factory=list, max_length=100)
    # "source": substring containment over the raw markup (historic behaviour).
    # "text": containment over the page's rendered text (tags stripped, entities
    # decoded, whitespace collapsed) so an include term binds to what a reader
    # sees rather than to one serialization of the markup.
    scope: Literal["source", "text"] = "source"

    @field_validator("include", "exclude")
    @classmethod
    def bound_terms(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value:
                raise ValueError("include and exclude entries must be non-empty")
            if len(value) > 10_000:
                raise ValueError(
                    "include and exclude entries must be at most 10000 characters"
                )
        return values

    @model_validator(mode="after")
    def require_a_rule(self) -> IncludeExcludeInput:
        if not self.include and not self.exclude:
            raise ValueError("at least one include or exclude entry is required")
        return self


class IncludeExcludeOutput(StrictModel):
    """Result of required/forbidden substring checks with bounded evidence."""

    match: bool
    score: float
    missing_include: list[str]
    missing_include_count: int
    found_exclude: list[str]
    found_exclude_count: int
    diagnostics_truncated: bool
    truncated: bool


def _bounded_diagnostics(
    groups: tuple[list[str], ...], limit: int
) -> tuple[tuple[list[str], ...], bool]:
    """Bound diagnostic strings by their JSON-encoded aggregate size."""
    remaining = max(0, limit - 512)
    bounded: list[list[str]] = []
    was_truncated = False
    for values in groups:
        selected: list[str] = []
        for value in values:
            encoded_size = len(json.dumps(value, ensure_ascii=False)) + 1
            if encoded_size > remaining:
                was_truncated = True
                continue
            selected.append(value)
            remaining -= encoded_size
        bounded.append(selected)
        was_truncated = was_truncated or len(selected) != len(values)
    return tuple(bounded), was_truncated


class CheckIncludeExclude(SourceCommand[IncludeExcludeInput, IncludeExcludeOutput]):
    """Check required and forbidden substrings in bounded HTML source."""

    name = "check_include_exclude"
    input_model = IncludeExcludeInput
    output_model = IncludeExcludeOutput

    def run(
        self, source_input: IncludeExcludeInput, context: SourceContext
    ) -> IncludeExcludeOutput:
        resolved = context.resolve_path(source_input.path)
        text, truncated = _read_bounded_text(resolved, context.max_content_chars)
        if source_input.scope == "text":
            text = _page_text(text)
        missing = [value for value in source_input.include if value not in text]
        found = [value for value in source_input.exclude if value in text]
        # A partial read cannot establish that a forbidden value is absent.
        match = not missing and not found and not (truncated and source_input.exclude)
        (bounded_missing, bounded_found), diagnostics_truncated = _bounded_diagnostics(
            (missing, found), context.max_content_chars
        )
        return IncludeExcludeOutput(
            match=match,
            score=1.0 if match else 0.0,
            missing_include=bounded_missing,
            missing_include_count=len(missing),
            found_exclude=bounded_found,
            found_exclude_count=len(found),
            diagnostics_truncated=diagnostics_truncated,
            truncated=truncated,
        )


class FuzzyMatchInput(StrictModel):
    """Arguments for ``html.fuzzy_match``."""

    path: str
    expected: str = Field(min_length=1, max_length=100_000)


class FuzzyMatchOutput(StrictModel):
    """RapidFuzz partial ratio in the inclusive range 0..1."""

    score: float
    truncated: bool


class FuzzyMatch(SourceCommand[FuzzyMatchInput, FuzzyMatchOutput]):
    """Compare bounded file content with verifier-authored expected text."""

    name = "fuzzy_match"
    input_model = FuzzyMatchInput
    output_model = FuzzyMatchOutput

    def run(
        self, source_input: FuzzyMatchInput, context: SourceContext
    ) -> FuzzyMatchOutput:
        resolved = context.resolve_path(source_input.path)
        text, truncated = _read_bounded_text(resolved, context.max_content_chars)
        return FuzzyMatchOutput(
            # A prefix-only read must never receive full credit for an
            # arbitrarily longer file, even when that prefix equals expected.
            score=0.0
            if truncated
            else _fuzzy_ratio(text, source_input.expected, partial=True),
            truncated=truncated,
        )


class AccessibilityRule(StrictModel):
    """One XPath or CSS selection and optional text expectation."""

    xpath: str | None = Field(default=None, min_length=1, max_length=2000)
    selectors: list[str] | None = Field(default=None, min_length=1, max_length=20)
    text: str | None = Field(default=None, max_length=20_000)
    exact: bool = True

    @field_validator("selectors")
    @classmethod
    def bound_selectors(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        for value in values:
            if not value or len(value) > 500:
                raise ValueError(
                    "selectors must be non-empty and at most 500 characters"
                )
        return values

    @model_validator(mode="after")
    def require_one_selector_form(self) -> AccessibilityRule:
        if (self.xpath is None) == (self.selectors is None):
            raise ValueError(
                "accessibility rules require exactly one of xpath or selectors"
            )
        return self


class AccessibilityTreeInput(StrictModel):
    """Arguments for ``html.check_accessibility_tree``."""

    path: str
    rules: list[AccessibilityRule] = Field(min_length=1, max_length=50)
    os_name: Literal["ubuntu", "windows", "macos"] = "ubuntu"


class AccessibilityTreeOutput(StrictModel):
    """Multiplicative rule score and per-rule selection evidence."""

    match: bool
    score: float
    rule_scores: list[float]
    matched_elements: list[int]


class CheckAccessibilityTree(
    SourceCommand[AccessibilityTreeInput, AccessibilityTreeOutput]
):
    """Evaluate XPath/CSS rules against a bounded, non-networked XML tree."""

    name = "check_accessibility_tree"
    input_model = AccessibilityTreeInput
    output_model = AccessibilityTreeOutput

    def run(
        self, source_input: AccessibilityTreeInput, context: SourceContext
    ) -> AccessibilityTreeOutput:
        try:
            from cssselect import SelectorError  # type: ignore[import-not-found]
            from lxml import etree  # type: ignore[import-not-found]
            from lxml.cssselect import CSSSelector  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependencies are declared
            raise SourceCapabilityError(
                "html.check_accessibility_tree requires lxml and cssselect"
            ) from exc

        resolved = context.resolve_path(source_input.path)
        text, truncated = _read_bounded_text(resolved, context.max_content_chars)
        if truncated:
            raise SourceDataError("accessibility tree exceeds the source content limit")
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
            recover=False,
        )
        try:
            tree = etree.fromstring(text.encode("utf-8"), parser=parser)
        except etree.XMLSyntaxError as exc:
            raise SourceDataError("accessibility tree is not valid XML") from exc
        if tree.getroottree().docinfo.doctype:
            raise SourceDataError("accessibility tree must not contain a DOCTYPE")

        namespaces = _ACCESSIBILITY_NAMESPACES[source_input.os_name]
        rule_scores: list[float] = []
        matched_elements: list[int] = []
        for rule in source_input.rules:
            try:
                if rule.xpath is not None:
                    selected = tree.xpath(rule.xpath, namespaces=namespaces)
                elif rule.selectors is not None:
                    selector = CSSSelector(
                        ", ".join(rule.selectors), namespaces=namespaces
                    )
                    selected = selector(tree)
                else:  # guarded by AccessibilityRule validation
                    raise SourceAuthoringError(
                        "an accessibility rule needs xpath or selectors"
                    )
            except (etree.XPathError, SelectorError, ValueError) as exc:
                raise SourceAuthoringError(
                    f"invalid accessibility selector: {exc}"
                ) from exc
            if not isinstance(selected, list) or any(
                not isinstance(item, etree._Element) for item in selected
            ):
                raise SourceAuthoringError(
                    "accessibility xpath must select elements, not scalar or text nodes"
                )

            matched_elements.append(len(selected))
            if not selected:
                rule_scores.append(0.0)
                continue
            if rule.text is None:
                rule_scores.append(1.0)
                continue

            best = 0.0
            for element in selected:
                if hasattr(element, "itertext"):
                    actual = "".join(element.itertext())
                else:
                    actual = str(element)
                score = (
                    (1.0 if actual == rule.text else 0.0)
                    if rule.exact
                    else _fuzzy_ratio(actual, rule.text)
                )
                best = max(best, score)
            rule_scores.append(best)

        score = 1.0
        for rule_score in rule_scores:
            score *= rule_score
        return AccessibilityTreeOutput(
            match=score == 1.0,
            score=score,
            rule_scores=rule_scores,
            matched_elements=matched_elements,
        )


COMMANDS = (CheckAccessibilityTree(), FuzzyMatch(), CheckIncludeExclude(), SelectText())
