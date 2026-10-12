-- Access paths used by the SQL tools, analytics and retrieval.
CREATE INDEX IF NOT EXISTS ix_facts_cik_concept_fy ON financial_facts (cik, concept, fiscal_year);
CREATE INDEX IF NOT EXISTS ix_facts_concept_unit ON financial_facts (taxonomy, concept, unit);
CREATE INDEX IF NOT EXISTS ix_filings_cik_fy ON filings (cik, fiscal_year);
CREATE INDEX IF NOT EXISTS ix_dq_audit_run ON dq_audit (run_id, severity);

CREATE INDEX IF NOT EXISTS ix_chunks_filter ON document_chunks (cik, fiscal_year, section);
CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON document_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON document_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
