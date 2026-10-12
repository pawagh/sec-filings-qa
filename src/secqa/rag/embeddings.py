"""IBM Granite embeddings via sentence-transformers, plus a backfill job for un-embedded chunks."""

import argparse
import logging
import threading
from functools import lru_cache

import numpy as np

from secqa.config import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()

SECTION_LABELS = {"item1": "Business", "item1a": "Risk Factors", "item7": "MD&A"}


@lru_cache(maxsize=2)
def get_model(name: str | None = None):
    import torch
    from sentence_transformers import SentenceTransformer

    name = name or get_settings().embed_model
    log.info("loading embedding model %s", name)
    # Granite r2 ships bf16 weights. On CPUs without bf16 support (most containers, incl. arm64 Docker
    # VMs) torch silently falls back to a path ~180x slower, so always load in float32.
    return SentenceTransformer(name, model_kwargs={"dtype": torch.float32})


def embed(texts: list[str], batch_size: int | None = None) -> np.ndarray:
    model = get_model()
    with _lock:  # the model isn't safe to call concurrently on MPS/GPU
        return model.encode(texts, batch_size=batch_size or get_settings().embed_batch_size,
                            normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)


def embed_query(text: str) -> np.ndarray:
    return embed([text])[0]


def passage_text(name: str, ticker: str, fiscal_year: int, section: str, content: str) -> str:
    """Contextual header so a chunk saying "the Company" is still findable by company/year/section."""
    return f"{name} ({ticker}) FY{fiscal_year} 10-K, {SECTION_LABELS.get(section, section)}:\n{content}"


def embed_pending(conn, batch: int = 256) -> int:
    """Embed chunks with no embedding or one from a different model. Idempotent and resumable."""
    model_name = get_settings().embed_model
    total = 0
    while True:
        rows = conn.execute(
            """SELECT ch.id, c.name, c.ticker, ch.fiscal_year, ch.section, ch.content
               FROM document_chunks ch JOIN companies c USING (cik)
               WHERE ch.embedding IS NULL OR ch.embed_model IS DISTINCT FROM %s
               ORDER BY ch.id LIMIT %s""", (model_name, batch)).fetchall()
        if not rows:
            break
        vecs = embed([passage_text(*r[1:]) for r in rows])
        with conn.cursor() as cur:
            cur.executemany("UPDATE document_chunks SET embedding = %s, embed_model = %s WHERE id = %s",
                            [(v, model_name, r[0]) for r, v in zip(rows, vecs, strict=True)])
        conn.commit()
        total += len(rows)
        log.info("embedded %d chunks", total)
    return total


def main() -> None:
    from secqa.db import connect

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    argparse.ArgumentParser(description="Embed any un-embedded document chunks").parse_args()
    with connect() as conn:
        print(f"embedded {embed_pending(conn)} chunks")


if __name__ == "__main__":
    main()
