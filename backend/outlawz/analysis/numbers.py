"""The numbers check: every number in the model's summary must exist in the computed result.

A number matches if some computed value rounds to it at the precision it was written with
("12.35" matches 12.3456; "1,200" matches 1200; "45%" matches 45 or 0.45). Citation markers and
numbers inside the question's own filters are allowed too. If any number fails, the model's text
is dropped and the computed summary is shown instead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

_MARKER = re.compile(r"\[\d+\]")
_NUMBER = re.compile(r"(?<![\w.])([-−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(\s?%)?(?![\w])")


@dataclass(frozen=True)
class Found:
    text: str
    value: float
    decimals: int
    percent: bool


def numbers_in(text: str) -> list[Found]:
    clean = _MARKER.sub(" ", text)
    found = []
    for match in _NUMBER.finditer(clean):
        sign, whole, frac, pct = match.groups()
        value = float(whole.replace(",", "") + (f".{frac}" if frac else ""))
        if sign:
            value = -value
        found.append(Found(match.group(0).strip(), value, len(frac or ""), bool(pct)))
    return found


def _matches(found: Found, value: float) -> bool:
    if math.isnan(value) or math.isinf(value):
        return False
    candidates = [value, abs(value)]
    if found.percent:
        candidates += [value * 100, abs(value) * 100]
    tolerance = 0.5 * 10 ** (-found.decimals) + 1e-9 * abs(found.value)
    for candidate in candidates:
        if abs(round(candidate, found.decimals) - found.value) <= tolerance:
            return True
        if abs(candidate - found.value) <= tolerance:
            return True
    # Large values written rounded to the nearest whole number ("about 1,235" for 1234.6).
    return (
        found.decimals == 0 and abs(found.value) >= 100 and abs(abs(value) - abs(found.value)) < 1
    )


def unmatched_numbers(text: str, allowed: Iterable[float]) -> list[str]:
    values = [float(v) for v in allowed]
    missing = []
    for found in numbers_in(text):
        if not any(_matches(found, v) for v in values):
            missing.append(found.text)
    return missing


def summary_is_grounded(text: str, allowed: Iterable[float]) -> bool:
    return not unmatched_numbers(text, allowed)
