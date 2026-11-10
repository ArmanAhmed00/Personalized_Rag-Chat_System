"""Server-side citation checking.

* ``[n]`` markers that point at a source that wasn't sent are removed (``[1, 9]`` → ``[1]``
  when only 1 exists). Markers are normalised to ``[1][2]`` so the UI can link each one.
* An answer left with no valid citation is flagged, unless it's a plain "the documents don't
  cover this" refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARKER = re.compile(r"\[\s*(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\s*\]")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_REFUSAL = re.compile(
    r"(don't|do not|doesn't|does not|didn't|did not|can't|cannot|couldn't|could not)\s+"
    r"(cover|contain|mention|include|find|say|answer|address)|"
    r"\bnot (covered|mentioned|found|included)\b|\bno (information|mention|sources?)\b|"
    r"\bne (contiennent|mentionnent|couvrent) pas\b|\bno (contienen|mencionan)\b",
    re.IGNORECASE,
)


@dataclass
class CitationCheck:
    text: str
    cited: list[int] = field(default_factory=list)  # valid numbers, in order of first use
    removed: int = 0  # marker numbers dropped because nothing was sent with that number
    sentences: int = 0
    uncited_sentences: int = 0
    refusal: bool = False

    @property
    def flags(self) -> list[str]:
        flags = []
        if self.refusal:
            flags.append("refusal")
        elif not self.cited:
            flags.append("no_citations")
        if self.removed:
            flags.append("removed_citations")
        return flags


def is_refusal(text: str) -> bool:
    return bool(_REFUSAL.search(text))


def check_citations(text: str, valid: set[int]) -> CitationCheck:
    result = CitationCheck(text=text)
    cited: list[int] = []

    def replace(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.split(r"\s*[,;]\s*", match.group(1))]
        keep = [n for n in numbers if n in valid]
        result.removed += len(numbers) - len(keep)
        for n in keep:
            if n not in cited:
                cited.append(n)
        return "".join(f"[{n}]" for n in dict.fromkeys(keep))

    cleaned = _MARKER.sub(replace, text)
    cleaned = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", cleaned)  # "[2][2]" → "[2]"
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)  # "fact [9]." → "fact."
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    result.text = cleaned
    result.cited = cited
    for sentence in _SENTENCE.findall(cleaned):
        if len(sentence.split()) < 4:
            continue
        result.sentences += 1
        if not _MARKER.search(sentence):
            result.uncited_sentences += 1
    result.refusal = not cited and is_refusal(cleaned)
    return result
