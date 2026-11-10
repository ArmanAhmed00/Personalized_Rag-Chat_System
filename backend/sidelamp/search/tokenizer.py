"""The keyword-search tokenizer.

* Keeps decimals (``3.1``, ``1,234.5``) and codes (``AB1234``, ``iso-27001``, ``covid-19``) whole.
* Lower-cases, folds accents (``café`` → ``cafe``) and simple English plurals
  (``policies`` → ``policy``, ``boxes`` → ``box``, ``reports`` → ``report``).
* Drops common English stop words.

The same function is used at ingest time (term frequencies are stored with each passage) and at
query time, so they always agree.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

STOP_WORDS = frozenset(
    """
a about above after again against all am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't
down during each few for from further had hadn't has hasn't have haven't having he he'd he'll he's
her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself let's me more most mustn't my myself no nor not of off on once only or other ought
our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such
than that that's the their theirs them themselves then there there's these they they'd they'll
they're they've this those through to too under until up very was wasn't we we'd we'll we're we've
were weren't what what's when when's where where's which while who who's whom why why's will with
won't would wouldn't you you'd you'll you're you've your yours yourself yourselves also just
""".split()
)

# Order matters: the first alternative that matches wins.
_TOKEN = re.compile(
    r"""
    (?P<code>[a-z0-9]*\d[a-z0-9]*(?:[-_/][a-z0-9]+)+|[a-z]+(?:[-_/][a-z0-9]+)*[-_/][a-z0-9]*\d[a-z0-9]*)
    |(?P<number>\d+(?:[.,]\d+)+)
    |(?P<alnum>[a-z]+\d[a-z0-9]*|\d+[a-z][a-z0-9]*)
    |(?P<word>[^\W\d_]+(?:'[^\W\d_]+)?)
    |(?P<int>\d+)
    """,
    re.VERBOSE,
)

_PLURAL_EXCEPTIONS = frozenset(
    {
        "series",
        "species",
        "news",
        "analysis",
        "basis",
        "crisis",
        "thesis",
        "status",
        "bus",
        "gas",
        "lens",
        "chaos",
        "bias",
        "canvas",
        "atlas",
        "alias",
        "always",
        "perhaps",
        "whereas",
        "its",
        "this",
        "is",
        "was",
        "has",
        "does",
        "yes",
        "us",
        "less",
        "unless",
        "across",
        "plus",
        "minus",
        "corps",
        "physics",
        "mathematics",
        "economics",
        "politics",
        "ethics",
        "statistics",
    }
)
_THOUSANDS = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")


def fold_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def singular(word: str) -> str:
    """Very small English plural folding. Wrong in rare cases, but consistent."""
    if len(word) <= 3 or word in _PLURAL_EXCEPTIONS or not word.endswith("s"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith(("xes", "ches", "shes", "zzes")):
        return word[:-2]
    if word.endswith(("ss", "us", "is", "ous")):
        return word
    return word[:-1]


def _number(text: str) -> str:
    if _THOUSANDS.match(text):
        return text.replace(",", "")
    if text.count(",") == 1 and "." not in text:
        return text.replace(",", ".")  # decimal comma: 3,5 -> 3.5
    return text


def tokenize(text: str, *, keep_stop_words: bool = False) -> list[str]:
    lowered = fold_accents(text).lower().replace("’", "'")
    tokens: list[str] = []
    for match in _TOKEN.finditer(lowered):
        kind = match.lastgroup
        token = match.group()
        if kind == "word":
            if "'" in token:
                if not keep_stop_words and token in STOP_WORDS:
                    continue
                token = token.split("'", 1)[0]
            if not keep_stop_words and token in STOP_WORDS:
                continue
            if len(token) == 1:
                continue
            token = singular(token)
        elif kind == "number":
            token = _number(token)
        tokens.append(token)
    return tokens


def term_frequencies(text: str) -> tuple[dict[str, int], int]:
    """Term counts and token length, stored with each passage at ingest."""
    tokens = tokenize(text)
    return dict(Counter(tokens)), len(tokens)
