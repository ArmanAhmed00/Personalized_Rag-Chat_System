"""CSV: the schema, a profile of each column, and blocks of rows for search.

Only the first ``csv_rows`` rows (default 5,000) are indexed for search; data analysis always
reads every row. Row blocks are cited as "rows 21–40". For keyword search only the *values* of a
row are indexed, so column names repeated on every row don't drown out real matches.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from sidelamp.errors import AppError, DocumentError
from sidelamp.ingest.extractors.base import (
    Block,
    Cancelled,
    Extracted,
    ExtractOptions,
    ProgressFn,
)
from sidelamp.tabular import ColumnKind, fmt_number, load_csv, typed_frame

_BINARY_MAGIC = (
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"\x7fELF",
    b"MZ",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"\xca\xfe\xba\xbe",
    b"\xcf\xfa\xed\xfe",
    b"\x1f\x8b",
    b"7z\xbc\xaf",
    b"Rar!",
    b"\xd0\xcf\x11\xe0",
)
_ROWS_SECTION = "Rows"


def _text_tokens(text: str) -> int:
    return max(1, len(text.split()) + text.count(";"))


class CsvExtractor:
    type = "csv"
    label = "CSV spreadsheet"
    extensions = (".csv",)
    media_type = "text/csv; charset=utf-8"
    inline = False
    version = 1

    def sniff(self, path: Path) -> bool:
        with open(path, "rb") as fh:
            head = fh.read(64 * 1024)
        if not head or b"\x00" in head:
            return False
        if any(head.startswith(magic) for magic in _BINARY_MAGIC):
            return False
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                head.decode(encoding)
                return True
            except UnicodeDecodeError:
                continue
        return True  # latin-1 decodes anything without NUL bytes

    def validate(self, path: Path) -> None:
        try:
            frame, _ = load_csv(path, nrows=200)
        except DocumentError as exc:
            raise AppError(exc.code, exc.message) from None
        if frame.shape[1] == 0:
            raise AppError("empty_file", "This CSV file has no columns. It wasn't added.")

    def extract(
        self,
        path: Path,
        progress: ProgressFn,
        options: ExtractOptions,
        cancelled: Callable[[], bool],
    ) -> Extracted:
        progress(0, 3, "Reading the table")
        raw, fmt = load_csv(path)
        if raw.shape[1] == 0:
            raise DocumentError("empty", "This CSV file has no columns.")
        frame, kinds = typed_frame(raw)
        total_rows = len(raw)
        name = path.name
        blocks = [schema_block(name, raw, kinds, options.csv_rows)]
        progress(1, 3, "Describing the columns")
        blocks.extend(profile_blocks(frame, kinds))
        if cancelled():
            raise Cancelled()
        progress(2, 3, "Indexing rows")
        blocks.extend(row_blocks(raw, options.csv_rows, options.target_tokens, cancelled))
        progress(3, 3, f"Read {total_rows:,} rows")
        return Extracted(
            blocks=blocks,
            pages=None,
            meta={
                "rows": total_rows,
                "columns": list(raw.columns),
                "kinds": kinds,
                "encoding": fmt.encoding,
                "delimiter": fmt.delimiter,
                "indexed_rows": min(total_rows, options.csv_rows),
            },
        )


def schema_block(name: str, raw: pd.DataFrame, kinds: dict[str, ColumnKind], cap: int) -> Block:
    rows, cols = raw.shape
    described = ", ".join(f"{c} ({kinds[c]})" for c in raw.columns)
    text = f"Table {name}: {rows:,} rows and {cols} columns. Columns: {described}."
    if rows > cap:
        text += (
            f" Only the first {cap:,} rows are indexed for search; computed answers use every row."
        )
    return Block(text=text, kind="schema", section="Columns")


def profile_blocks(frame: pd.DataFrame, kinds: dict[str, ColumnKind]) -> list[Block]:
    lines = [_profile_line(frame[c], c, kinds[c]) for c in frame.columns]
    blocks = []
    for start in range(0, len(lines), 8):
        blocks.append(
            Block(text="\n".join(lines[start : start + 8]), kind="profile", section="Columns")
        )
    return blocks


def _profile_line(series: pd.Series, name: str, kind: ColumnKind) -> str:
    empty = int(series.isna().sum())
    gaps = f"{empty:,} empty" if empty else "no empty cells"
    if kind == "number":
        values = series.dropna()
        if values.empty:
            return f"{name} (number): {gaps}."
        return (
            f"{name} (number): min {fmt_number(values.min())}, max "
            f"{fmt_number(values.max())}, mean {fmt_number(values.mean())}, median "
            f"{fmt_number(values.median())}; {gaps}."
        )
    if kind == "date":
        values = series.dropna()
        if values.empty:
            return f"{name} (date): {gaps}."
        return f"{name} (date): from {values.min().date()} to {values.max().date()}; {gaps}."
    if kind == "empty":
        return f"{name}: every cell is empty."
    counts = series.dropna().astype(str).value_counts()
    top = ", ".join(f"{value} ({count:,})" for value, count in counts.head(6).items())
    return f"{name} ({kind}): {counts.size:,} distinct values; most common: {top}; {gaps}."


def row_blocks(
    raw: pd.DataFrame, cap: int, target_tokens: int, cancelled: Callable[[], bool]
) -> list[Block]:
    subset = raw.head(cap)
    if subset.empty:
        return []
    columns = list(subset.columns)
    lines: list[tuple[str, str]] = []
    for position, row in enumerate(subset.itertuples(index=False, name=None), start=1):
        cells = ["" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v) for v in row]
        shown = "; ".join(f"{c} = {v}" for c, v in zip(columns, cells, strict=True) if v != "")
        lines.append((f"Row {position}: {shown}", " ".join(v for v in cells if v)))
    sample = lines[: min(50, len(lines))]
    per_row = max(1.0, sum(_text_tokens(t) for t, _ in sample) / len(sample))
    rows_per_block = int(max(1, min(50, target_tokens // per_row)))
    blocks = []
    for start in range(0, len(lines), rows_per_block):
        if cancelled():
            raise Cancelled()
        chunk = lines[start : start + rows_per_block]
        first, last = start + 1, start + len(chunk)
        blocks.append(
            Block(
                text="\n".join(t for t, _ in chunk),
                kind="rows",
                section=_ROWS_SECTION,
                rows=(first, last),
                index_text=" ".join(v for _, v in chunk),
            )
        )
    return blocks


EXTRACTOR = CsvExtractor()
