"""Hybrid retrieval: pgvector (cosine, HNSW) + Postgres full-text, fused with Reciprocal Rank Fusion.

Router-extracted ticker/year/section become WHERE filters on both legs, which is the single biggest
recall win on a corpus where every company's risk factors sound alike.
"""

from dataclasses import asdict, dataclass

import psycopg

from secqa.rag.embeddings import embed_query

RRF_K = 60

_SQL = """
WITH vec AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s) AS r
    FROM document_chunks ch
    WHERE embedding IS NOT NULL {filters}
    ORDER BY embedding <=> %(qvec)s
    LIMIT %(k)s
),
q AS (
    -- OR the query's lexemes: plainto_tsquery ANDs every word, which almost never matches a question.
    SELECT NULLIF(replace(plainto_tsquery('english', %(qtext)s)::text, ' & ', ' | '), '')::tsquery AS tsq
),
fts AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q.tsq) DESC) AS r
    FROM document_chunks ch, q
    WHERE q.tsq IS NOT NULL AND tsv @@ q.tsq {filters}
    ORDER BY ts_rank_cd(tsv, q.tsq) DESC
    LIMIT %(k)s
)
SELECT ch.id, c.ticker, c.name, ch.fiscal_year, ch.section, ch.accession, f.url, ch.content,
       COALESCE(1.0 / (%(rrf)s + vec.r), 0) + COALESCE(1.0 / (%(rrf)s + fts.r), 0) AS score,
       vec.r AS vec_rank, fts.r AS fts_rank
FROM vec FULL OUTER JOIN fts USING (id)
JOIN document_chunks ch ON ch.id = COALESCE(vec.id, fts.id)
JOIN companies c ON c.cik = ch.cik
JOIN filings f ON f.accession = ch.accession
ORDER BY score DESC
LIMIT %(top)s
"""


@dataclass
class Passage:
    chunk_id: int
    ticker: str
    company: str
    fiscal_year: int
    section: str
    accession: str
    url: str
    content: str
    score: float
    vec_rank: int | None
    fts_rank: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def search(conn: psycopg.Connection, query: str, tickers: list[str] | None = None,
           years: list[int] | None = None, sections: list[str] | None = None,
           top_k: int = 5, candidates: int = 40, mode: str = "hybrid") -> list[Passage]:
    """mode: hybrid | vector | fts (the single-leg modes exist for the eval ablation)."""
    filters, params = [], {"qtext": query, "k": candidates, "top": top_k, "rrf": RRF_K}
    if tickers:
        filters.append("AND ch.cik IN (SELECT cik FROM companies WHERE ticker = ANY(%(tickers)s))")
        params["tickers"] = tickers
    if years:
        filters.append("AND ch.fiscal_year = ANY(%(years)s)")
        params["years"] = years
    if sections:
        filters.append("AND ch.section = ANY(%(sections)s)")
        params["sections"] = sections
    params["qvec"] = embed_query(query)
    sql = _SQL.format(filters=" ".join(filters))
    if mode == "vector":
        params["qtext"] = ""
    elif mode == "fts":
        sql = sql.replace("WHERE embedding IS NOT NULL", "WHERE false")
    with conn.transaction():
        # pgvector >= 0.8: keep scanning the HNSW graph until enough rows pass the filters.
        try:
            with conn.transaction():
                conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        except psycopg.Error:
            pass
        conn.execute("SET LOCAL hnsw.ef_search = 100")
        rows = conn.execute(sql, params).fetchall()
    return [Passage(*r[:8], float(r[8]), r[9], r[10]) for r in rows]
