"""Deterministic words+boxes grounding for VLM-proposed fields.

The VLM proposes values; this module alone assigns the final ``grounded`` bit and
source citation.  Any bbox, citation, or grounding claim supplied by the model is
discarded before local matching (W2-D3).  Matching is deliberately tiered: exact
phrases first, then strictly format-canonical atomic values, then bounded spatial
token agreement.  Every successful tier returns real words from this document, their
union bbox, and a source quote.  Every other outcome is UNSUPPORTED.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Generic, Iterable, TypeVar, cast

from app.ingestion.reader import NormBBox, PageWords, Word, WordsBoxes
from app.schemas.citations import CitationSourceType, CitationV2
from app.schemas.extraction import GroundedField

T = TypeVar("T")

# Preserve numerically-significant characters: a decimal point and a leading/interior
# minus sign carry value. Stripping them let numerically DISTINCT clinical values
# collapse to one token (``6.5`` -> ``65``). Percent is itself a clinically meaningful
# unit token. Only true separators are removed by the legacy exact-phrase tier.
_THOUSANDS_NUMBER = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")
_CLINICAL_SYMBOLS = frozenset(".,%/^*\u00b7+-\u00b1:<>=\u2264\u2265\u00b0\u03bc_'\"~")
_SYMBOL_TRANSLATION = str.maketrans(
    {
        "\u00d7": "*",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2032": "'",
        "\u2033": '"',
        "\u2212": "-",
        "\u223c": "~",
        "\u2248": "~",
    }
)
_VULGAR_FRACTIONS = {
    "\u00bd": "1/2",
    "\u2150": "1/7",
    "\u2151": "1/9",
    "\u2152": "1/10",
    "\u2153": "1/3",
    "\u2154": "2/3",
    "\u00bc": "1/4",
    "\u00be": "3/4",
    "\u2155": "1/5",
    "\u2156": "2/5",
    "\u2157": "3/5",
    "\u2158": "4/5",
    "\u2159": "1/6",
    "\u215a": "5/6",
    "\u215b": "1/8",
    "\u215c": "3/8",
    "\u215d": "5/8",
    "\u215e": "7/8",
}
_SUPERSCRIPT_CHARACTERS = {
    "\u2070": "0",
    "\u00b9": "1",
    "\u00b2": "2",
    "\u00b3": "3",
    "\u2074": "4",
    "\u2075": "5",
    "\u2076": "6",
    "\u2077": "7",
    "\u2078": "8",
    "\u2079": "9",
    "\u207a": "+",
    "\u207b": "-",
    "\u2071": "i",
    "\u207f": "n",
}

# Atomic canonicalization is intentionally closed and structural. It accepts one finite
# decimal magnitude and, optionally, one complete unit. It does not perform conversions,
# fuzzy matching, or unit synonym expansion.
_UNSIGNED_NUMBER_TEXT = r"(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
_NUMBER_TEXT = rf"[+-]?{_UNSIGNED_NUMBER_TEXT}"
_COMPARISON_TEXT = r"(?:<=|>=|[<>=\u2264\u2265])"
_UNIT_HEAD_TEXT = (
    r"(?:%|\u00b0\s*[^\W\d_]+(?:\d+)?|\u03bc\s*[^\W\d_]+(?:\d+)?|"
    r"[^\W\d_]+(?:\d+)?)"
)
_UNIT_TAIL_TEXT = rf"(?:{_UNIT_HEAD_TEXT}|\d+)"
_UNIT_EXPRESSION = re.compile(
    rf"(?:{_UNIT_HEAD_TEXT}(?:\s*[/^*\u00b7_-]\s*{_UNIT_TAIL_TEXT})*|"
    rf"\d+\s*\^\s*\d+(?:\s*[/^*\u00b7_-]\s*{_UNIT_TAIL_TEXT})+)",
    re.IGNORECASE | re.UNICODE,
)
_ATOMIC_NUMBER_PREFIX = re.compile(
    rf"\s*(?:{_COMPARISON_TEXT})?\s*{_NUMBER_TEXT}(?P<remainder>.*?)\s*",
    re.IGNORECASE | re.UNICODE,
)
_KNOWN_MULTIWORD_UNITS = frozenset({"mm hg"})
_NUMBER_UNIT = re.compile(
    rf"(?P<comparator>{_COMPARISON_TEXT})?(?P<number>{_NUMBER_TEXT})?"
    r"(?P<unit>(?:[%a-z\u03bc\u00b0/][%a-z0-9\u03bc\u00b0/.*^_-]*))?",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})")
_US_DATE = re.compile(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})")

# Significant-token matching preserves every clinical signal: magnitudes, signs,
# percent, unit components, and negations. Only articles are ignored; treating ``no``
# or ``without`` as stopwords would turn a negated statement into its opposite.
_SIGNIFICANT_PART = re.compile(
    rf"{_COMPARISON_TEXT}|{_NUMBER_TEXT}|%|[^\W\d_]+(?:['\u2019][^\W\d_]+)?",
    re.UNICODE,
)
_STRUCTURED_RELATION = re.compile(
    rf"(?<![^\W_])(?<!\.)(?=(?P<left>{_NUMBER_TEXT}|%|[^\W\d_]+)\s*"
    r"(?P<operator>[/^*\u00b7])\s*"
    rf"(?P<right>{_NUMBER_TEXT}|%|[^\W\d_]+)(?![^\W_])(?!\.))",
    re.UNICODE,
)
_STRUCTURED_COMPARISON = re.compile(
    rf"(?P<comparator>{_COMPARISON_TEXT})\s*(?P<number>{_NUMBER_TEXT})",
    re.UNICODE,
)
_STRUCTURED_NUMERIC_SEPARATOR = re.compile(
    rf"(?<![\d.,])(?=(?P<left>{_UNSIGNED_NUMBER_TEXT})\s*"
    rf"(?P<separator>[-,:'])\s*(?P<right>{_UNSIGNED_NUMBER_TEXT})(?![\d.,]))",
    re.UNICODE,
)
_STRUCTURED_NUMERIC_SIGN = re.compile(
    rf"(?<![\w.])(?P<sign>[+-])\s*"
    rf"(?P<number>{_UNSIGNED_NUMBER_TEXT})(?![\d.,])",
    re.UNICODE,
)
_MEASUREMENT_MARKER_RELATION = re.compile(
    rf"(?<![\d.,])(?P<number>{_UNSIGNED_NUMBER_TEXT})\s*"
    r"(?P<marker>['\"])",
    re.UNICODE,
)
_APPROXIMATE_NUMBER_RELATION = re.compile(
    rf"~\s*(?P<number>{_UNSIGNED_NUMBER_TEXT})(?![\d.,])",
    re.UNICODE,
)
_DEGREE_UNIT_RELATION = re.compile(
    r"\u00b0\s*(?P<unit>[^\W\d_]+(?:\d+)?)",
    re.UNICODE,
)
_HYPHENATED_UNIT_RELATION = re.compile(
    r"(?<![^\W_])(?<!\.)(?=(?P<left>[^\W\d_]+(?:\d+)?)\s*"
    r"(?P<operator>[-_])\s*(?P<right>[^\W\d_]+(?:\d+)?)(?![^\W_])(?!\.))",
    re.UNICODE,
)
_KNOWN_UNIT_ATOMS = frozenset(
    {
        "bpm",
        "cells",
        "cm2",
        "d",
        "day",
        "dl",
        "g",
        "h",
        "hr",
        "iu",
        "kg",
        "l",
        "lb",
        "mcg",
        "meq",
        "mg",
        "min",
        "ml",
        "mmhg",
        "mmol",
        "mol",
        "ng",
        "pg",
        "s",
        "sec",
        "u",
        "ug",
        "ul",
        "unit",
        "units",
        "week",
        "weeks",
        "\u03bcg",
        "\u03bcl",
    }
)
_GRAMMATICAL_ARTICLES = frozenset({"a", "an", "the"})
_STRUCTURAL_TABLE_LABELS = frozenset(
    {
        "allergen",
        "allergies",
        "allergy",
        "chief",
        "concern",
        "condition",
        "current",
        "date",
        "dob",
        "dose",
        "drug",
        "family",
        "frequency",
        "history",
        "medication",
        "medications",
        "name",
        "reaction",
        "relation",
        "relative",
        "unit",
        "value",
    }
)
_MAX_STRUCTURAL_LABEL_TOKENS = 3
_MAX_PUNCTUATION_WORDS = 8

# Geometry is normalized to the page. Bounds are intentionally small: a normal line of
# 10-12 pt text is roughly 0.012-0.016 page heights on US Letter. Non-free table values
# stay on one row unless a trusted cited region becomes available; allowlisted free text
# may span four tightly aligned lines.
_MIN_CELL_GAP = 0.012
_CELL_GAP_HEIGHT_FACTOR = 1.75
_TABLE_MAX_LINES = 1
_TABLE_MAX_VERTICAL_SPAN = 0.045
_FREE_TEXT_MAX_LINES = 4
_FREE_TEXT_MAX_VERTICAL_SPAN = 0.085
_FREE_TEXT_FIELDS = frozenset({"chief_concern", "family_history"})


@dataclass(frozen=True)
class GroundingOutcome(Generic[T]):
    """Final field plus a PHI-free deterministic outcome reason."""

    field: GroundedField[T]
    reason: str


@dataclass(frozen=True)
class GroundingSummary:
    """The persisted binary grounding tally (§2)."""

    fields_grounded: int
    fields_unsupported: int

    @classmethod
    def from_outcomes(
        cls, outcomes: list[GroundingOutcome[object]]
    ) -> "GroundingSummary":
        grounded = sum(1 for outcome in outcomes if outcome.field.grounded)
        return cls(grounded, len(outcomes) - grounded)


@dataclass(frozen=True)
class _Match:
    words: tuple[Word, ...]

    @property
    def bbox(self) -> NormBBox:
        return _union_bbox(self.words)

    @property
    def quote(self) -> str:
        return " ".join(word.text for word in self.words).strip()


@dataclass(frozen=True)
class _Row:
    words: tuple[Word, ...]

    @property
    def x0(self) -> float:
        return min(word.bbox.x0 for word in self.words)

    @property
    def x1(self) -> float:
        return max(word.bbox.x1 for word in self.words)

    @property
    def y0(self) -> float:
        return min(word.bbox.y0 for word in self.words)

    @property
    def y1(self) -> float:
        return max(word.bbox.y1 for word in self.words)

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class _Region:
    words: tuple[Word, ...]
    line_count: int

    @property
    def x0(self) -> float:
        return min(word.bbox.x0 for word in self.words)

    @property
    def x1(self) -> float:
        return max(word.bbox.x1 for word in self.words)

    @property
    def y0(self) -> float:
        return min(word.bbox.y0 for word in self.words)

    @property
    def y1(self) -> float:
        return max(word.bbox.y1 for word in self.words)

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class _AtomicValue:
    comparator: str | None
    sign: str | None
    number: Decimal | None
    unit: str | None


def _text(value: object) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _expand_unicode_math(value: str) -> str:
    expanded: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        fraction = _VULGAR_FRACTIONS.get(character)
        if fraction is not None:
            if expanded and expanded[-1][-1:].isdigit():
                # Keep the mixed-number boundary visible to exact normalization.
                # Significant-token matching treats this internal underscore as
                # layout, so OCR boxes ``1`` + ``1/2`` remain a faithful alias.
                expanded.append("_")
            expanded.append(fraction)
            index += 1
            continue
        if character == "\u2044":
            expanded.append("/")
            index += 1
            continue
        if character in _SUPERSCRIPT_CHARACTERS:
            superscript: list[str] = []
            while index < len(value) and value[index] in _SUPERSCRIPT_CHARACTERS:
                superscript.append(_SUPERSCRIPT_CHARACTERS[value[index]])
                index += 1
            if not expanded or expanded[-1] != "^":
                expanded.append("^")
            expanded.extend(superscript)
            continue
        expanded.append(character)
        index += 1
    return "".join(expanded)


def _normalize_unicode_symbols(value: str) -> str:
    # Translate both before and after NFKC: compatibility normalization decomposes
    # a double-prime into two single-primes, so pre-translation retains its intended
    # double-quote/measurement-marker identity.
    translated = _expand_unicode_math(value).translate(_SYMBOL_TRANSLATION)
    return unicodedata.normalize("NFKC", translated).translate(_SYMBOL_TRANSLATION)


def _normalize(token: str) -> str:
    folded = _normalize_unicode_symbols(token).casefold()
    # Remove commas only from a complete, valid thousands-grouped number. A comma
    # between arbitrary digits is not harmless formatting: collapsing ``1,2`` to
    # ``12`` would invent a different magnitude. Non-numeric punctuation commas are
    # still ordinary separators for the legacy phrase tier.
    folded = _THOUSANDS_NUMBER.sub(
        lambda match: match.group(0).replace(",", ""), folded
    )
    folded = re.sub(r"(?<!\d),|,(?!\d)", "", folded)
    return "".join(
        character
        for character in folded
        if character.isalnum() or character in _CLINICAL_SYMBOLS
    )


def _phrase_tokens(value: object) -> tuple[str, ...]:
    return tuple(
        normalized
        for token in _text(value).split()
        if (normalized := _normalize(token))
    )


def _phrase_token_variants(value: object) -> tuple[tuple[str, ...], ...]:
    """Canonicalize UTC datetimes to ``Z`` while accepting the ISO ``+00:00`` alias."""

    primary = _phrase_tokens(value)
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        return (primary,)
    iso = value.isoformat()
    if not iso.endswith("+00:00"):
        return (primary,)
    canonical = tuple(
        normalized
        for token in f"{iso[:-6]}Z".split()
        if (normalized := _normalize(token))
    )
    return (canonical, primary) if canonical != primary else (primary,)


def _union_bbox(words: Iterable[Word]) -> NormBBox:
    boxes = [word.bbox for word in words]
    return NormBBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _center_y(word: Word) -> float:
    return (word.bbox.y0 + word.bbox.y1) / 2


def _same_row(word: Word, row_words: list[Word]) -> bool:
    row_y0 = min(item.bbox.y0 for item in row_words)
    row_y1 = max(item.bbox.y1 for item in row_words)
    overlap = min(word.bbox.y1, row_y1) - max(word.bbox.y0, row_y0)
    word_height = word.bbox.y1 - word.bbox.y0
    row_height = row_y1 - row_y0
    if overlap > 0 and overlap >= min(word_height, row_height) * 0.35:
        return True
    row_center = median(_center_y(item) for item in row_words)
    return abs(_center_y(word) - row_center) <= max(
        0.0045, max(word_height, row_height) * 0.60
    )


def _rows(words: Iterable[Word]) -> tuple[_Row, ...]:
    grouped: list[list[Word]] = []
    ordered = sorted(words, key=lambda word: (_center_y(word), word.bbox.x0))
    for word in ordered:
        candidates = [row for row in grouped if _same_row(word, row)]
        if not candidates:
            grouped.append([word])
            continue
        target = min(
            candidates,
            key=lambda row: abs(
                _center_y(word) - median(_center_y(item) for item in row)
            ),
        )
        target.append(word)
    materialized = [
        _Row(tuple(sorted(row, key=lambda word: word.bbox.x0))) for row in grouped
    ]
    return tuple(sorted(materialized, key=lambda row: (row.y0, row.x0)))


def _row_cells(row: _Row) -> tuple[_Region, ...]:
    heights = [word.bbox.y1 - word.bbox.y0 for word in row.words]
    gap_limit = max(_MIN_CELL_GAP, median(heights) * _CELL_GAP_HEIGHT_FACTOR)
    cells: list[list[Word]] = []
    for word in row.words:
        if not cells or word.bbox.x0 - cells[-1][-1].bbox.x1 > gap_limit:
            cells.append([word])
        else:
            cells[-1].append(word)
    return tuple(_Region(tuple(cell), line_count=1) for cell in cells)


def _regions_align(left: _Region, right: _Region) -> bool:
    overlap = min(left.x1, right.x1) - max(left.x0, right.x0)
    min_width = min(left.x1 - left.x0, right.x1 - right.x0)
    overlaps = min_width > 0 and overlap / min_width >= 0.35
    left_aligned = abs(left.x0 - right.x0) <= 0.035
    return overlaps or left_aligned


def _spatial_regions(
    page: PageWords, *, max_lines: int, max_vertical_span: float
) -> tuple[_Region, ...]:
    """Return same-cell and tightly wrapped-cell regions in reading order.

    Large horizontal gaps split unrelated table cells. Multi-line regions require
    consecutive rows plus x overlap/alignment; tokens are never pooled page-wide.
    """

    rows = _rows(page.words)
    cells_by_row = tuple(_row_cells(row) for row in rows)
    regions: list[_Region] = [cell for cells in cells_by_row for cell in cells]
    if max_lines <= 1:
        return tuple(regions)

    for start_index, start_cells in enumerate(cells_by_row):
        for start_cell in start_cells:
            current = start_cell
            previous = start_cell
            for row_index in range(
                start_index + 1, min(len(rows), start_index + max_lines)
            ):
                vertical_gap = rows[row_index].y0 - rows[row_index - 1].y1
                height_scale = max(rows[row_index].height, rows[row_index - 1].height)
                if vertical_gap > max(0.012, height_scale * 1.25):
                    break
                aligned = [
                    cell
                    for cell in cells_by_row[row_index]
                    if _regions_align(previous, cell)
                ]
                if not aligned:
                    break
                next_cell = min(
                    aligned,
                    key=lambda cell: (
                        abs(cell.x0 - previous.x0),
                        abs(cell.x1 - previous.x1),
                    ),
                )
                combined_words = (*current.words, *next_cell.words)
                combined = _Region(
                    words=combined_words,
                    line_count=current.line_count + 1,
                )
                if combined.height > max_vertical_span:
                    break
                regions.append(combined)
                current = combined
                previous = next_cell
    return tuple(regions)


def _is_free_text_field(field_id: str) -> bool:
    return any(
        field_id == field or field_id.endswith(f".{field}")
        for field in _FREE_TEXT_FIELDS
    )


def _bounded_selection(
    page: PageWords, words: tuple[Word, ...], *, free_text: bool
) -> bool:
    if not words:
        return False
    selected = {id(word) for word in words}
    regions = _spatial_regions(
        page,
        max_lines=_FREE_TEXT_MAX_LINES if free_text else _TABLE_MAX_LINES,
        max_vertical_span=(
            _FREE_TEXT_MAX_VERTICAL_SPAN if free_text else _TABLE_MAX_VERTICAL_SPAN
        ),
    )
    return any(selected <= {id(word) for word in region.words} for region in regions)


def _match_contiguous(
    page: PageWords, wanted: tuple[str, ...], *, free_text: bool
) -> _Match | None:
    if page.unreadable or not wanted:
        return None
    normalized = [_normalize(word.text) for word in page.words]
    width = len(wanted)
    for start in range(0, len(normalized) - width + 1):
        if tuple(normalized[start : start + width]) != wanted:
            continue
        matched = tuple(page.words[start : start + width])
        if _bounded_selection(page, matched, free_text=free_text):
            return _Match(matched)
    return None


def _compact(value: str) -> str:
    normalized = _normalize_unicode_symbols(value).casefold()
    return "".join(normalized.split())


def _canonical_decimal(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _parse_date_text(raw: str) -> date | None:
    compact = _compact(raw)
    match = _ISO_DATE.fullmatch(compact) or _US_DATE.fullmatch(compact)
    if match is None:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    return _parse_date_text(str(value)) if isinstance(value, str) else None


def _parse_number_unit(raw: str) -> _AtomicValue | None:
    compact = _compact(raw)
    match = _NUMBER_UNIT.fullmatch(compact)
    if match is None or (match.group("number") is None and match.group("unit") is None):
        return None
    number = (
        _canonical_decimal(match.group("number"))
        if match.group("number") is not None
        else None
    )
    if match.group("number") is not None and number is None:
        return None
    unit = match.group("unit") or None
    comparator = match.group("comparator") or None
    if comparator == "\u2264":
        comparator = "<="
    elif comparator == "\u2265":
        comparator = ">="
    number_text = match.group("number") or ""
    sign = number_text[0] if number_text.startswith(("+", "-")) else None
    return _AtomicValue(
        comparator=comparator,
        sign=sign,
        number=number,
        unit=unit,
    )


def _unit_field(field_id: str) -> bool:
    return field_id == "unit" or field_id.endswith(".unit")


def _normalized_shape_text(raw: str) -> str:
    return _normalize_unicode_symbols(raw)


def _complete_unit_shape(raw: str) -> bool:
    normalized = _normalized_shape_text(raw).strip()
    return bool(
        _UNIT_EXPRESSION.fullmatch(normalized)
        or normalized.casefold() in _KNOWN_MULTIWORD_UNITS
    )


def _atomic_number_unit_shape(raw: str) -> bool:
    """Recognize a true number leaf without collapsing prose whitespace.

    Whitespace may separate the magnitude from one complete unit and may surround
    explicit unit operators. It may not concatenate arbitrary trailing words into a
    synthetic unit (for example, ``10 mg Lisinopril daily``).
    """

    match = _ATOMIC_NUMBER_PREFIX.fullmatch(_normalized_shape_text(raw))
    if match is None:
        return False
    remainder = match.group("remainder").strip()
    return not remainder or _complete_unit_shape(remainder)


def _atomic_value(value: object, field_id: str) -> _AtomicValue | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (Decimal, int, float)):
        number = _canonical_decimal(str(value))
        return (
            _AtomicValue(
                comparator=None,
                sign=str(value)[0] if str(value).startswith(("+", "-")) else None,
                number=number,
                unit=None,
            )
            if number is not None
            else None
        )
    if not isinstance(value, str):
        return None
    if _unit_field(field_id):
        if not _complete_unit_shape(value):
            return None
        compact = _compact(value)
        return _AtomicValue(comparator=None, sign=None, number=None, unit=compact)
    if not _atomic_number_unit_shape(value):
        return None
    parsed = _parse_number_unit(value)
    return parsed if parsed is not None and parsed.number is not None else None


def _atomic_agrees(wanted: _AtomicValue, source_text: str) -> bool:
    compact = _compact(source_text)
    if wanted.number is None and wanted.unit is not None and compact == wanted.unit:
        return True
    observed = _parse_number_unit(source_text)
    if observed is None:
        return False
    if observed.comparator != wanted.comparator:
        return False
    if observed.sign != wanted.sign:
        return False
    if wanted.number is not None and observed.number != wanted.number:
        return False
    if wanted.unit is not None and observed.unit != wanted.unit:
        return False
    if wanted.number is not None and wanted.unit is None and observed.unit is not None:
        # A source-attached unit is not mere whitespace. A bare magnitude may still
        # ground against its own OCR word when the unit occupies a separate word box,
        # but never by silently discarding ``%`` or ``mg/dL`` from one combined token.
        return False
    return wanted.number is not None or wanted.unit is not None


_EXPLICIT_ATOMIC_BOUNDARY = frozenset("/.*^_-,+<>=\u2264\u2265\u00b0\u03bc")


def _atomic_word_boundary_agrees(left: str, right: str) -> bool:
    """Whether removing whitespace at this OCR word boundary is format-only.

    Number-to-unit spacing and spacing around an explicit source punctuation token are
    harmless. Joining two digit runs (``1`` + ``6`` -> ``16``) or two letter runs
    (``m`` + ``g`` -> ``mg``) creates a token that the page never contained and is
    therefore forbidden.
    """

    left = _normalize_unicode_symbols(left).strip()
    right = _normalize_unicode_symbols(right).strip()
    if not left or not right:
        return False
    if left[-1] in _EXPLICIT_ATOMIC_BOUNDARY:
        return True
    if right[0] in _EXPLICIT_ATOMIC_BOUNDARY or right[0] in "%\u03bc\u00b0":
        return True
    return left[-1].isdigit() and right[0].isalpha()


def _match_atomic(page: PageWords, value: object, field_id: str) -> _Match | None:
    if page.unreadable:
        return None
    wanted_date = _date_value(value)
    wanted_atomic = _atomic_value(value, field_id)
    if wanted_date is None and wanted_atomic is None:
        return None

    matches: list[_Match] = []
    for region in _spatial_regions(
        page,
        max_lines=_TABLE_MAX_LINES,
        max_vertical_span=_TABLE_MAX_VERTICAL_SPAN,
    ):
        words = region.words
        for start in range(len(words)):
            for end in range(start + 1, len(words) + 1):
                candidate = words[start:end]
                if len(candidate) > 1 and not _atomic_word_boundary_agrees(
                    candidate[-2].text, candidate[-1].text
                ):
                    # Every longer slice contains the same unsafe boundary.
                    break
                source_text = "".join(word.text for word in candidate)
                agrees = (
                    _parse_date_text(source_text) == wanted_date
                    if wanted_date is not None
                    else wanted_atomic is not None
                    and _atomic_agrees(wanted_atomic, source_text)
                )
                if agrees:
                    matches.append(_Match(tuple(candidate)))
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            len(item.words),
            (item.bbox.x1 - item.bbox.x0) * (item.bbox.y1 - item.bbox.y0),
            item.bbox.y0,
            item.bbox.x0,
        ),
    )


def _canonical_significant_part(part: str) -> str | None:
    folded = _normalize_unicode_symbols(part).casefold()
    if folded in {"\u2264", "<="}:
        return "comparison:<="
    if folded in {"\u2265", ">="}:
        return "comparison:>="
    if folded in {"<", ">", "="}:
        return f"comparison:{folded}"
    if re.fullmatch(_NUMBER_TEXT, folded):
        number = _canonical_decimal(folded)
        if number is None:
            return None
        sign = folded[0] if folded.startswith(("+", "-")) else ""
        return f"number:{sign}{abs(number).normalize()}"
    if folded == "%":
        return "%"
    cleaned = "".join(character for character in folded if character.isalnum())
    # A capital single-letter ``A`` is often clinical content (vitamin A, type A),
    # not a grammatical article. Retain it while still ignoring prose articles.
    capital_single_letter = len(part) == 1 and part.isalpha() and part.isupper()
    if not cleaned or (cleaned in _GRAMMATICAL_ARTICLES and not capital_single_letter):
        return None
    return cleaned


def _valid_thousands_comma(raw: str, position: int) -> bool:
    start = position
    while start > 0 and (raw[start - 1].isdigit() or raw[start - 1] == ","):
        start -= 1
    end = position + 1
    while end < len(raw) and (raw[end].isdigit() or raw[end] == ","):
        end += 1
    return bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw[start:end]))


def _significant_tokens(raw: str) -> tuple[str, ...]:
    normalized_raw = _normalize_unicode_symbols(raw)
    numeric_relations: list[str] = []
    relations: list[str] = []
    ordinary_characters = list(normalized_raw)
    numeric_separator_positions: set[int] = set()
    for match in _STRUCTURED_NUMERIC_SEPARATOR.finditer(normalized_raw):
        separator = match.group("separator")
        separator_position = match.start("separator")
        if separator == "," and _valid_thousands_comma(
            normalized_raw, separator_position
        ):
            continue
        left = _canonical_significant_part(match.group("left"))
        right = _canonical_significant_part(match.group("right"))
        if left is None or right is None:
            continue
        relation_kind = {
            "-": "range",
            ",": "comma",
            ":": "colon",
            "'": "prime",
        }[separator]
        numeric_relations.append(f"relation:{left}:{relation_kind}:{right}")
        numeric_separator_positions.add(separator_position)
        if separator == "-":
            # A hyphen between two magnitudes is a range operator, not the sign of
            # the right endpoint. Spaced and compact ranges therefore tokenize alike.
            ordinary_characters[separator_position] = " "

    numeric_sign_positions: set[int] = set()
    for match in _STRUCTURED_NUMERIC_SIGN.finditer(normalized_raw):
        sign_position = match.start("sign")
        if sign_position in numeric_separator_positions:
            continue
        number = _canonical_significant_part(match.group("number"))
        if number is None:
            continue
        numeric_sign_positions.add(sign_position)
        ordinary_characters[sign_position] = " "
        numeric_relations.append(
            f"relation:numeric-sign:{match.group('sign')}:{number}"
        )

    for match in _MEASUREMENT_MARKER_RELATION.finditer(normalized_raw):
        number = _canonical_significant_part(match.group("number"))
        if number is not None:
            numeric_relations.append(
                f"relation:{number}:marker:{match.group('marker')}"
            )

    for match in _APPROXIMATE_NUMBER_RELATION.finditer(normalized_raw):
        number = _canonical_significant_part(match.group("number"))
        if number is not None:
            relations.extend(
                ("qualifier:approximate", f"relation:approximate:{number}")
            )

    unit_join_positions: set[int] = set()
    for match in _HYPHENATED_UNIT_RELATION.finditer(normalized_raw):
        left = _canonical_significant_part(match.group("left"))
        right = _canonical_significant_part(match.group("right"))
        operator = match.group("operator")
        if operator == "-" and (
            left not in _KNOWN_UNIT_ATOMS or right not in _KNOWN_UNIT_ATOMS
        ):
            continue
        unit_join_positions.add(match.start("operator"))
        relations.append(f"relation:{left}{operator}{right}")

    for position, sign in enumerate(normalized_raw):
        if sign not in "+-\u00b1" or position in (
            numeric_separator_positions | numeric_sign_positions | unit_join_positions
        ):
            continue
        left_index = position - 1
        while left_index >= 0 and normalized_raw[left_index].isspace():
            left_index -= 1
        right_index = position + 1
        while (
            right_index < len(normalized_raw) and normalized_raw[right_index].isspace()
        ):
            right_index += 1
        if (
            sign == "-"
            and left_index >= 0
            and right_index < len(normalized_raw)
            and normalized_raw[left_index].isalpha()
            and normalized_raw[right_index].isalpha()
        ):
            # Ordinary prose hyphenation remains format-flexible. Known unit joins
            # were captured above; a trailing clinical +/- is handled below.
            continue
        relations.append(f"qualifier:{sign}")
        preceding_parts = _SIGNIFICANT_PART.findall(normalized_raw[:position].rstrip())
        if preceding_parts:
            preceding = _canonical_significant_part(preceding_parts[-1])
            if preceding is not None:
                relations.append(f"relation:{preceding}:qualifier:{sign}")

    ordinary_raw = "".join(ordinary_characters)
    ordinary = tuple(
        token
        for part in _SIGNIFICANT_PART.findall(ordinary_raw)
        if (token := _canonical_significant_part(part)) is not None
    )
    # Operators are part of a compound unit's identity. Preserve each ordered
    # operator relation so an order-relaxed table-cell match cannot equate ``mg/dL``
    # with ``dL/mg`` or erase ``^`` from ``10^9/L``.
    for match in _STRUCTURED_RELATION.finditer(normalized_raw):
        left = _canonical_significant_part(match.group("left"))
        right = _canonical_significant_part(match.group("right"))
        if left is None or right is None:
            continue
        operator = (
            "*" if match.group("operator") == "\u00b7" else match.group("operator")
        )
        relations.append(f"relation:{left}{operator}{right}")
    for match in _STRUCTURED_COMPARISON.finditer(normalized_raw):
        comparator = _canonical_significant_part(match.group("comparator"))
        number = _canonical_significant_part(match.group("number"))
        if comparator is not None and number is not None:
            relations.append(f"relation:{comparator}{number}")
    for _ in re.finditer("\u00b0", normalized_raw):
        relations.append("unit:degree")
    for match in _DEGREE_UNIT_RELATION.finditer(normalized_raw):
        unit = _canonical_significant_part(match.group("unit"))
        if unit is not None:
            relations.append(f"relation:degree:{unit}")
    return (*ordinary, *relations, *numeric_relations)


def _counter_contains(container: Counter[str], wanted: Counter[str]) -> bool:
    return all(container[token] >= count for token, count in wanted.items())


def _match_significant_tokens(
    page: PageWords,
    wanted: Counter[str],
    *,
    allow_extra: bool,
    free_text: bool,
    allowed_structural_extras: frozenset[str] = frozenset(),
) -> _Match | None:
    if page.unreadable or not wanted:
        return None
    regions = _spatial_regions(
        page,
        max_lines=_FREE_TEXT_MAX_LINES if free_text else _TABLE_MAX_LINES,
        max_vertical_span=(
            _FREE_TEXT_MAX_VERTICAL_SPAN if free_text else _TABLE_MAX_VERTICAL_SPAN
        ),
    )
    extra_limit = max(3, (sum(wanted.values()) + 2) // 3) if allow_extra else 0
    max_window_words = max(
        12,
        sum(wanted.values())
        + extra_limit
        + _MAX_STRUCTURAL_LABEL_TOKENS
        + _MAX_PUNCTUATION_WORDS,
    )
    matches: list[_Match] = []
    for region in regions:
        words = region.words
        for start in range(len(words)):
            for end in range(start, min(len(words), start + max_window_words)):
                observed = Counter(
                    _significant_tokens(
                        " ".join(word.text for word in words[start : end + 1])
                    )
                )
                if allow_extra:
                    if _counter_contains(observed, wanted):
                        extra_count = sum((observed - wanted).values())
                        if extra_count <= extra_limit:
                            matches.append(_Match(tuple(words[start : end + 1])))
                else:
                    extra_tokens = observed - wanted
                    if any(
                        token not in allowed_structural_extras for token in extra_tokens
                    ):
                        # Structured punctuation can be context-sensitive while a
                        # source window is still incomplete (``70 -`` becomes a range
                        # once ``100`` arrives). Keep extending, but never accept the
                        # window while an unapproved extra remains.
                        continue
                    if sum(extra_tokens.values()) > _MAX_STRUCTURAL_LABEL_TOKENS:
                        continue
                    if _counter_contains(observed, wanted):
                        matches.append(_Match(tuple(words[start : end + 1])))
                        break
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            len(item.words),
            (item.bbox.x1 - item.bbox.x0) * (item.bbox.y1 - item.bbox.y0),
            item.bbox.y0,
            item.bbox.x0,
        ),
    )


class GroundingVerifier:
    """Construct final canonical fields from local agreement only.

    Page numbers exposed in fields/citations are one-based. ``PageWords.page_index``
    remains the reader's zero-based internal index.
    """

    def ground_value(
        self,
        *,
        value: T | None,
        words_boxes: WordsBoxes,
        source_document_id: str,
        field_id: str,
        page: int | None = None,
    ) -> GroundingOutcome[T]:
        pages = sorted(
            (
                item
                for item in words_boxes.pages
                if page is None or item.page_index == page - 1
            ),
            key=lambda item: item.page_index,
        )
        if value is None:
            return self._unsupported(value, page, "missing_value")
        if pages and all(item.unreadable for item in pages):
            return self._unsupported(value, page, "page_unreadable")

        free_text = _is_free_text_field(field_id)

        # Tier 1: preserve the existing contiguous normalized phrase comparison. Search
        # all pages before proceeding to a weaker tier, and require bounded geometry.
        variants = _phrase_token_variants(value)
        for source_page in pages:
            for wanted_phrase in variants:
                match = _match_contiguous(
                    source_page, wanted_phrase, free_text=free_text
                )
                if match is not None:
                    return self._grounded(
                        value=value,
                        source_page=source_page,
                        source_document_id=source_document_id,
                        field_id=field_id,
                        match=match,
                    )

        # Tier 2: canonicalize formatting only for strictly recognized dates and
        # number/unit atoms. Decimal magnitude and the complete supplied unit must agree.
        structured_atomic = (
            isinstance(value, datetime)
            or _date_value(value) is not None
            or _atomic_value(value, field_id) is not None
        )
        for source_page in pages:
            match = _match_atomic(source_page, value, field_id)
            if match is not None:
                return self._grounded(
                    value=value,
                    source_page=source_page,
                    source_document_id=source_document_id,
                    field_id=field_id,
                    match=match,
                )

        # Atomic structure is part of the value, not layout. Once strict atomic/date
        # comparison fails, never weaken it into an order-relaxed token bag: doing so
        # can swap month/day or invert a compound unit while retaining the same tokens.
        if structured_atomic:
            return self._unsupported(value, page, "not_found")

        wanted_significant = Counter(_significant_tokens(_text(value)))

        # Tier 3: all significant tokens, with multiplicity, must occupy one bounded
        # row/cell region. Order may differ. A few closed-vocabulary table labels may
        # surround the value; arbitrary extra clinical tokens remain disallowed.
        for source_page in pages:
            match = _match_significant_tokens(
                source_page,
                wanted_significant,
                allow_extra=False,
                free_text=free_text,
                allowed_structural_extras=_STRUCTURAL_TABLE_LABELS,
            )
            if match is not None:
                return self._grounded(
                    value=value,
                    source_page=source_page,
                    source_document_id=source_document_id,
                    field_id=field_id,
                    match=match,
                )

        # Tier 4: only named free-text fields may match a tightly bounded source region
        # that contains a few extra source tokens. Every significant proposal token is
        # still mandatory; invention never becomes grounding.
        if free_text:
            for source_page in pages:
                match = _match_significant_tokens(
                    source_page,
                    wanted_significant,
                    allow_extra=True,
                    free_text=True,
                )
                if match is not None:
                    return self._grounded(
                        value=value,
                        source_page=source_page,
                        source_document_id=source_document_id,
                        field_id=field_id,
                        match=match,
                    )

        return self._unsupported(value, page, "not_found")

    @staticmethod
    def _grounded(
        *,
        value: T,
        source_page: PageWords,
        source_document_id: str,
        field_id: str,
        match: _Match,
    ) -> GroundingOutcome[T]:
        page_number = source_page.page_index + 1
        citation = CitationV2(
            source_type=CitationSourceType.UPLOADED_DOCUMENT,
            source_id=source_document_id,
            page_or_section=str(page_number),
            field_or_chunk_id=field_id,
            quote_or_value=match.quote,
        )
        return GroundingOutcome(
            field=cast(
                GroundedField[T],
                GroundedField(
                    value=value,
                    page=page_number,
                    bbox=match.bbox,
                    grounded=True,
                    citation=citation,
                ),
            ),
            reason="matched",
        )

    def reground_candidate(
        self,
        candidate: GroundedField[T],
        *,
        words_boxes: WordsBoxes,
        source_document_id: str,
        field_id: str,
    ) -> GroundingOutcome[T]:
        """Discard every VLM grounding assertion, then run the local verifier."""

        return self.ground_value(
            value=candidate.value,
            words_boxes=words_boxes,
            source_document_id=source_document_id,
            field_id=field_id,
        )

    @staticmethod
    def _unsupported(
        value: T | None, page: int | None, reason: str
    ) -> GroundingOutcome[T]:
        return GroundingOutcome(
            field=cast(
                GroundedField[T],
                GroundedField(
                    value=value,
                    page=page,
                    bbox=None,
                    grounded=False,
                    citation=None,
                ),
            ),
            reason=reason,
        )
