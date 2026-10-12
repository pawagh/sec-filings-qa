"""Paragraph-aware chunking with character offsets; chunks never cross section boundaries.

Overlap is paragraph-granular: the next chunk restarts at the last paragraph(s) that fit in
`overlap` chars, so a chunk never begins mid-sentence.
"""

import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\"'])")


@dataclass
class Chunk:
    ordinal: int
    content: str
    char_start: int
    char_end: int


def _spans(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Paragraph spans; paragraphs longer than max_chars are split at sentence boundaries."""
    spans = []
    for m in re.finditer(r"[^\n]+", text):
        start, end = m.start(), m.end()
        if end - start <= max_chars:
            spans.append((start, end))
            continue
        cur = start
        cuts = [start + s.start() for s in _SENTENCE_END.finditer(m.group(0))] + [end]
        last_cut = start
        for cut in cuts:
            if cut - cur > max_chars and last_cut > cur:
                spans.append((cur, last_cut))
                cur = last_cut
            last_cut = cut
        while end - cur > max_chars:  # one giant "sentence": hard split
            spans.append((cur, cur + max_chars))
            cur += max_chars
        spans.append((cur, end))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> list[Chunk]:
    spans = _spans(text, max_chars)
    chunks: list[Chunk] = []
    i = 0
    while i < len(spans):
        start = spans[i][0]
        j = i
        while j + 1 < len(spans) and spans[j + 1][1] - start <= max_chars:
            j += 1
        end = spans[j][1]
        chunks.append(Chunk(len(chunks), text[start:end].strip(), start, end))
        if j + 1 >= len(spans):
            break
        # Overlap: restart at the latest paragraph that begins within `overlap` chars of the end,
        # but always make progress.
        nxt = j + 1
        k = j
        while k > i and end - spans[k][0] <= overlap:
            nxt = k
            k -= 1
        i = max(nxt, i + 1)
    return chunks
