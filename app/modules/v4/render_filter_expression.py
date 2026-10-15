from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_PADDED_FRAME_SELECT_RE = re.compile(
    r"(?P<prefix>\[vconcat\]select='not\()"
    r"(?P<body>eq\(n,\d+\)(?:\+eq\(n,\d+\))*)"
    r"(?P<suffix>\)',settb=)"
)
_EQ_TERM_RE = re.compile(r"eq\(n,\d+\)")


@dataclass(frozen=True, slots=True)
class FilterExpressionNormalization:
    script: str
    padded_frame_terms: int
    changed: bool


def balanced_addition_expression(terms: list[str]) -> str:
    """Build an equivalent addition expression with logarithmic AST depth.

    FFmpeg's libavutil expression evaluator has a finite AST-depth bound. A
    left-associative ``a+b+c+...`` expression grows one level per term and
    therefore fails for large Render graphs even when the filter script itself
    is well below the byte-size compiler bound. Pairwise balancing preserves
    exact numeric addition while reducing expression depth to O(log N).
    """

    if not terms:
        raise ValueError("At least one FFmpeg expression term is required.")
    level = list(terms)
    while len(level) > 1:
        next_level: list[str] = []
        for index in range(0, len(level), 2):
            if index + 1 >= len(level):
                next_level.append(level[index])
                continue
            next_level.append(f"({level[index]}+{level[index + 1]})")
        level = next_level
    return level[0]


def normalize_padded_frame_select_script(script: str) -> FilterExpressionNormalization:
    """Rebalance the generated post-concat padded-frame drop expression.

    The Render graph intentionally pads every visual segment by one frame before
    concat and removes those exact cumulative frame indices afterward. This
    function changes only the syntax tree used to sum the mutually-exclusive
    ``eq(n,index)`` predicates; it does not change the set of dropped frames,
    tpad placement, concat order, timestamps, or output labels.
    """

    match = _PADDED_FRAME_SELECT_RE.search(script)
    if match is None:
        return FilterExpressionNormalization(script=script, padded_frame_terms=0, changed=False)

    terms = _EQ_TERM_RE.findall(match.group("body"))
    if not terms:
        return FilterExpressionNormalization(script=script, padded_frame_terms=0, changed=False)

    balanced = balanced_addition_expression(terms)
    original_body = match.group("body")
    if balanced == original_body:
        return FilterExpressionNormalization(script=script, padded_frame_terms=len(terms), changed=False)

    replacement = f"{match.group('prefix')}{balanced}{match.group('suffix')}"
    normalized = script[: match.start()] + replacement + script[match.end() :]
    return FilterExpressionNormalization(script=normalized, padded_frame_terms=len(terms), changed=True)


def normalize_padded_frame_select_file(path: Path) -> FilterExpressionNormalization:
    """Normalize one generated Render filter script in place when required."""

    original = path.read_text(encoding="utf-8")
    result = normalize_padded_frame_select_script(original)
    if result.changed:
        path.write_text(result.script, encoding="utf-8", newline="\n")
    return result
