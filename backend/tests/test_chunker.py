from __future__ import annotations

from sidelamp.ingest.chunker import ChunkSettings, chunk_blocks, count_tokens, split_sentences
from sidelamp.ingest.extractors.base import Block

CFG = ChunkSettings(target=60, overlap=12, minimum=10)


def sentences(n: int, prefix: str = "Fact") -> str:
    return " ".join(f"{prefix} number {i} is stated here clearly." for i in range(n))


def test_short_paragraphs_are_packed_together() -> None:
    blocks = [
        Block(text=f"Short paragraph {i} with a few words.", page=1, section="A") for i in range(4)
    ]
    chunks = chunk_blocks(blocks, CFG)
    assert len(chunks) == 1
    assert chunks[0].text.count("\n\n") == 3  # paragraph breaks kept


def test_target_size_is_respected() -> None:
    blocks = [Block(text=sentences(6, f"P{i}"), page=1, section="A") for i in range(6)]
    chunks = chunk_blocks(blocks, CFG)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.tokens <= CFG.target * 1.25


def test_never_crosses_a_section_or_chapter() -> None:
    blocks = [
        Block(text="Alpha text one.", section="A", chapter="I", page=1),
        Block(text="Beta text two.", section="B", chapter="I", page=1),
        Block(text="Gamma text three.", section="B", chapter="II", page=2),
    ]
    chunks = chunk_blocks(blocks, CFG)
    assert [(c.chapter, c.section) for c in chunks] == [("I", "A"), ("I", "B"), ("II", "B")]


def test_long_paragraph_splits_on_sentences() -> None:
    text = sentences(20)
    chunks = chunk_blocks([Block(text=text, section="A", page=3)], CFG)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.endswith(".")  # whole sentences only
        assert chunk.page == 3


def test_huge_sentence_splits_on_words() -> None:
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_blocks([Block(text=text, section="A")], CFG)
    assert len(chunks) >= 3
    assert all(c.tokens <= CFG.target * 1.25 for c in chunks)


def test_overlap_repeats_the_end_of_the_previous_passage() -> None:
    chunks = chunk_blocks([Block(text=sentences(20), section="A")], CFG)
    first, second = chunks[0], chunks[1]
    last_sentence = split_sentences(first.text)[-1]
    assert second.text.startswith(last_sentence)


def test_tiny_fragment_is_merged() -> None:
    blocks = [Block(text=sentences(7), section="A"), Block(text="Tiny end.", section="A")]
    chunks = chunk_blocks(blocks, ChunkSettings(target=60, overlap=0, minimum=10))
    assert all(c.tokens >= 10 for c in chunks)
    assert chunks[-1].text.endswith("Tiny end.")


def test_tiny_section_is_not_merged_across_sections() -> None:
    blocks = [Block(text=sentences(3), section="A"), Block(text="Tiny.", section="B")]
    chunks = chunk_blocks(blocks, CFG)
    assert [c.section for c in chunks] == ["A", "B"]


def test_page_range_tracked() -> None:
    blocks = [
        Block(text="On page four.", page=4, section="A"),
        Block(text="On page five.", page=5, section="A"),
    ]
    chunk = chunk_blocks(blocks, CFG)[0]
    assert (chunk.page, chunk.page_end) == (4, 5)


def test_tables_and_csv_blocks_stand_alone() -> None:
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    blocks = [
        Block(text="Intro.", section="A"),
        Block(text=table, kind="table", section="A"),
        Block(text="Outro.", section="A"),
        Block(text="Row 1: x = 1", kind="rows", rows=(1, 1), index_text="1", section="Rows"),
    ]
    chunks = chunk_blocks(blocks, CFG)
    assert [c.kind for c in chunks] == ["text", "table", "text", "rows"]
    assert chunks[3].rows == (1, 1) and chunks[3].index_text == "1"


def test_long_table_splits_with_header_repeated() -> None:
    rows = "\n".join(f"| item {i} | {i} |" for i in range(80))
    table = "| name | value |\n| --- | --- |\n" + rows
    chunks = chunk_blocks([Block(text=table, kind="table", section="A")], CFG)
    assert len(chunks) > 1
    assert all(c.text.startswith("| name | value |\n| --- | --- |") for c in chunks)


def test_sentence_split_keeps_decimals_and_abbreviations() -> None:
    parts = split_sentences("Growth was 3.1 percent, e.g. in Q2. Dr. Smith agreed. Next one.")
    assert parts == ["Growth was 3.1 percent, e.g. in Q2.", "Dr. Smith agreed.", "Next one."]


def test_token_count() -> None:
    assert count_tokens("Hello, world!") == 4
