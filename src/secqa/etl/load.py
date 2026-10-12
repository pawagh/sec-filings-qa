"""Idempotent loaders. Re-running with the same inputs changes zero rows."""

import hashlib
import json

import pandas as pd
import psycopg

from secqa.etl.chunking import chunk_text
from secqa.etl.facts import FACT_COLUMNS
from secqa.etl.sections import Section


def start_run(conn: psycopg.Connection, params: dict) -> int:
    return conn.execute("INSERT INTO etl_runs (params) VALUES (%s) RETURNING run_id",
                        (json.dumps(params),)).fetchone()[0]


def finish_run(conn: psycopg.Connection, run_id: int, status: str, stats: dict) -> None:
    conn.execute("UPDATE etl_runs SET finished_at = now(), status = %s, stats = %s WHERE run_id = %s",
                 (status, json.dumps(stats, default=str), run_id))


def write_audit(conn: psycopg.Connection, run_id: int, records: list[dict]) -> None:
    if not records:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO dq_audit (run_id, check_name, severity, entity, details) VALUES (%s, %s, %s, %s, %s)",
            [(run_id, r["check_name"], r["severity"], r["entity"], json.dumps(r["details"], default=str))
             for r in records])


def upsert_company(conn: psycopg.Connection, c: dict) -> None:
    conn.execute(
        """INSERT INTO companies (cik, ticker, name, sector, sic, sic_description, fiscal_year_end, aliases)
           VALUES (%(cik)s, %(ticker)s, %(name)s, %(sector)s, %(sic)s, %(sic_description)s, %(fiscal_year_end)s, %(aliases)s)
           ON CONFLICT (cik) DO UPDATE SET
             ticker = EXCLUDED.ticker, name = EXCLUDED.name, sector = EXCLUDED.sector, sic = EXCLUDED.sic,
             sic_description = EXCLUDED.sic_description, fiscal_year_end = EXCLUDED.fiscal_year_end,
             aliases = EXCLUDED.aliases, updated_at = now()
           WHERE (companies.ticker, companies.name, companies.sector, companies.sic, companies.fiscal_year_end, companies.aliases)
                 IS DISTINCT FROM
                 (EXCLUDED.ticker, EXCLUDED.name, EXCLUDED.sector, EXCLUDED.sic, EXCLUDED.fiscal_year_end, EXCLUDED.aliases)""",
        c)


def upsert_concepts(conn: psycopg.Connection, concepts: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO metric_concept_map (metric, label, taxonomy, concept, unit, period_type, priority)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (metric, taxonomy, concept) DO UPDATE SET
                 label = EXCLUDED.label, unit = EXCLUDED.unit, period_type = EXCLUDED.period_type,
                 priority = EXCLUDED.priority""",
            concepts[["metric", "label", "taxonomy", "concept", "unit", "period_type", "priority"]]
            .itertuples(index=False, name=None))


def load_facts(conn: psycopg.Connection, df: pd.DataFrame, run_id: int) -> int:
    """COPY into a temp staging table, then insert only rows whose natural key is new."""
    if df.empty:
        return 0
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS stg_facts (LIKE financial_facts INCLUDING DEFAULTS) ON COMMIT DROP")
    conn.execute("TRUNCATE stg_facts")
    with conn.cursor() as cur, cur.copy(f"COPY stg_facts ({', '.join(FACT_COLUMNS)}) FROM STDIN") as copy:
        for row in df[FACT_COLUMNS].itertuples(index=False, name=None):
            copy.write_row([None if pd.isna(v) else v for v in row])
    cur = conn.execute(
        f"""INSERT INTO financial_facts ({', '.join(FACT_COLUMNS)}, run_id)
            SELECT {', '.join(FACT_COLUMNS)}, %s FROM stg_facts
            ON CONFLICT ON CONSTRAINT financial_facts_natural_key DO NOTHING""", (run_id,))
    return cur.rowcount


def upsert_filing(conn: psycopg.Connection, f: dict) -> None:
    conn.execute(
        """INSERT INTO filings (accession, cik, form, fiscal_year, period_end, filed_date, primary_doc, url)
           VALUES (%(accession)s, %(cik)s, %(form)s, %(fiscal_year)s, %(period_end)s, %(filed_date)s, %(primary_doc)s, %(url)s)
           ON CONFLICT (accession) DO NOTHING""", f)


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def upsert_sections(conn: psycopg.Connection, filing: dict, sections: list[Section],
                    chunk_size: int, overlap: int) -> dict:
    """Insert/replace sections; re-chunk only sections whose content changed. Returns counts."""
    stats = {"sections_changed": 0, "chunks_inserted": 0}
    existing = {sec: _digest(content) for sec, content in conn.execute(
        "SELECT section, content FROM filing_sections WHERE accession = %s", (filing["accession"],))}
    for s in sections:
        if existing.get(s.section) == _digest(s.content):
            continue
        stats["sections_changed"] += 1
        conn.execute(
            """INSERT INTO filing_sections (accession, section, title, content) VALUES (%s, %s, %s, %s)
               ON CONFLICT (accession, section) DO UPDATE SET title = EXCLUDED.title, content = EXCLUDED.content""",
            (filing["accession"], s.section, s.title, s.content))
        conn.execute("DELETE FROM document_chunks WHERE accession = %s AND section = %s",
                     (filing["accession"], s.section))
        chunks = chunk_text(s.content, chunk_size, overlap)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO document_chunks (accession, cik, fiscal_year, section, ordinal, content, char_start, char_end)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [(filing["accession"], filing["cik"], filing["fiscal_year"], s.section, c.ordinal, c.content,
                  c.char_start, c.char_end) for c in chunks])
        stats["chunks_inserted"] += len(chunks)
    return stats


def refresh_materialized_views(conn: psycopg.Connection) -> None:
    populated = conn.execute(
        "SELECT ispopulated FROM pg_matviews WHERE matviewname = 'mv_company_year_metrics'").fetchone()
    concurrently = "CONCURRENTLY" if populated and populated[0] else ""
    conn.execute(f"REFRESH MATERIALIZED VIEW {concurrently} mv_company_year_metrics")
