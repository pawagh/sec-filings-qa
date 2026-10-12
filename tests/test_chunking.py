from secqa.etl.chunking import chunk_text

TEXT = "\n".join(f"Paragraph {i}. " + ("This sentence adds some length. " * (3 + i % 7)) for i in range(60))


def test_offsets_round_trip_and_size():
    chunks = chunk_text(TEXT, 1200, 200)
    assert len(chunks) > 5
    for c in chunks:
        assert TEXT[c.char_start:c.char_end].strip() == c.content
        assert len(c.content) <= 1200
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_full_coverage_with_overlap():
    chunks = chunk_text(TEXT, 1200, 200)
    assert chunks[0].char_start == 0 and chunks[-1].char_end == len(TEXT)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.char_start <= a.char_end + 1        # no gaps (at most the newline between paragraphs)
        assert b.char_start > a.char_start           # always progresses


def test_long_paragraph_split_on_sentences():
    long_para = "Risk sentence number one is here. " * 100
    chunks = chunk_text(long_para, 500, 50)
    assert all(len(c.content) <= 500 for c in chunks)
    assert all(c.content.endswith(".") for c in chunks[:-1])
