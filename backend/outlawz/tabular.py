"""Reading CSV files and deciding what kind of values each column holds.

Shared by the CSV extractor (for search) and data analysis (for computing), so both see the same
columns with the same types.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from outlawz.errors import DocumentError

ColumnKind = Literal["number", "date", "boolean", "text", "empty"]

_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_DELIMITERS = ",;\t|"
_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}
_NUMBER_CLEAN = re.compile(r"^[\s$€£¥]*([-+]?[\d.,]+)\s*%?[\s$€£¥]*$")


@dataclass
class CsvFormat:
    encoding: str
    delimiter: str


def detect_format(path: Path) -> CsvFormat:
    with open(path, "rb") as fh:
        sample = fh.read(256 * 1024)
    text = None
    encoding = "utf-8-sig"
    for encoding in _ENCODINGS:
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise DocumentError("unreadable", "This CSV file's text encoding isn't recognised.")
    lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    delimiter = ","
    if lines:
        try:
            delimiter = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=_DELIMITERS).delimiter
        except csv.Error:
            counts = {d: lines[0].count(d) for d in _DELIMITERS}
            best = max(counts, key=lambda d: counts[d])
            delimiter = best if counts[best] else ","
    return CsvFormat(encoding=encoding, delimiter=delimiter)


def _clean_names(columns: list[Any]) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(columns, start=1):
        name = re.sub(r"\s+", " ", str(raw)).strip()
        if not name or name.lower().startswith("unnamed:"):
            name = f"column_{i}"
        base = name
        if base in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        names.append(name)
    return names


def load_csv(path: Path, *, nrows: int | None = None) -> tuple[pd.DataFrame, CsvFormat]:
    """Read every row (or the first ``nrows``) as text first; types are applied by
    :func:`typed_frame`, so a stray value never changes how a column is read."""
    fmt = detect_format(path)
    try:
        # header=None: read the header row ourselves, so duplicate names aren't mangled.
        frame = pd.read_csv(
            path,
            sep=fmt.delimiter,
            encoding=fmt.encoding,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            skipinitialspace=True,
            on_bad_lines="skip",
            header=None,
            nrows=None if nrows is None else nrows + 1,
            engine="python" if len(fmt.delimiter) != 1 else "c",
        )
    except pd.errors.EmptyDataError:
        raise DocumentError("empty", "This CSV file is empty.") from None
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError):
        raise DocumentError(
            "damaged",
            "This CSV file can't be read. Check that it's a plain "
            "comma-, semicolon- or tab-separated file.",
        ) from None
    if frame.empty:
        raise DocumentError("empty", "This CSV file is empty.")
    header = ["" if pd.isna(v) else v for v in frame.iloc[0].tolist()]
    frame = frame.iloc[1:].reset_index(drop=True)
    frame.columns = _clean_names(header)
    for column in frame.columns:
        frame[column] = frame[column].map(lambda v: v.strip() if isinstance(v, str) else v)
    return frame, fmt


def parse_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _NUMBER_CLEAN.match(text)
    if not match:
        return None
    number = match.group(1)
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", number):
        number = number.replace(",", "")
    elif re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+(,\d+)?", number):
        number = number.replace(".", "").replace(",", ".")
    elif number.count(",") == 1 and "." not in number:
        number = number.replace(",", ".")
    try:
        result = float(number)
    except ValueError:
        return None
    if text.endswith("%"):
        return result  # keep percentages as written ("12%" -> 12)
    return result


def column_kind(series: pd.Series) -> ColumnKind:
    values = series.dropna()
    values = values[values.astype(str).str.strip() != ""]
    if values.empty:
        return "empty"
    sample = values.astype(str).head(5000)
    lowered = sample.str.lower()
    if (
        lowered.isin(_TRUE | _FALSE).all()
        and lowered.nunique() <= 2
        and not sample.str.fullmatch(r"[01]").all()
    ):
        return "boolean"
    numbers = sample.map(parse_number)
    if numbers.notna().mean() >= 0.95:
        return "number"
    if sample.str.contains(r"\d", regex=True).mean() >= 0.95:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        if parsed.notna().mean() >= 0.95:
            return "date"
    return "text"


def typed_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, ColumnKind]]:
    """Convert text columns to numbers, dates and booleans where the values allow it."""
    kinds: dict[str, ColumnKind] = {}
    frame = pd.DataFrame(index=raw.index)
    for column in raw.columns:
        kind = column_kind(raw[column])
        kinds[column] = kind
        series = raw[column]
        if kind == "number":
            frame[column] = pd.to_numeric(series.map(parse_number), errors="coerce").astype(
                "float64"
            )
        elif kind == "date":
            frame[column] = pd.to_datetime(series, errors="coerce", format="mixed")
        elif kind == "boolean":
            lowered = series.astype("string").str.lower()
            frame[column] = lowered.map(
                lambda v: True if v in _TRUE else (False if v in _FALSE else None)
            ).astype("boolean")
        else:
            frame[column] = series.astype("string")
    return frame, kinds


def fmt_number(value: float | int | None, digits: int = 4) -> str:
    """Readable number: no scientific notation for everyday sizes, trailing zeros trimmed."""
    if value is None:
        return "—"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "—"
    if isinstance(value, int | np.integer) or (float(value).is_integer() and abs(value) < 1e15):
        return f"{int(value):,}"
    magnitude = abs(float(value))
    if magnitude >= 1000:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    text = f"{value:.{digits}g}" if magnitude < 1e-3 else f"{value:.{digits}f}"
    if "e" not in text and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def csv_to_text(frame: pd.DataFrame) -> str:
    buf = io.StringIO()
    frame.to_csv(buf, index=False)
    return buf.getvalue()
