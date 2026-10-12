-- Core schema. Natural keys everywhere so every load is idempotent (INSERT ... ON CONFLICT).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    cik             integer PRIMARY KEY,
    ticker          text NOT NULL UNIQUE,
    name            text NOT NULL,
    sector          text,
    sic             text,
    sic_description text,
    fiscal_year_end text,              -- MMDD as reported by EDGAR, e.g. '0926'
    aliases         text[] NOT NULL DEFAULT '{}',
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id      bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    params      jsonb NOT NULL DEFAULT '{}',
    stats       jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS dq_audit (
    id         bigserial PRIMARY KEY,
    run_id     bigint REFERENCES etl_runs (run_id),
    check_name text NOT NULL,
    severity   text NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    entity     text,                   -- e.g. 'AAPL', 'AAPL/FY2024/item1a'
    details    jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS filings (
    accession    text PRIMARY KEY,     -- 0000320193-24-000123
    cik          integer NOT NULL REFERENCES companies (cik),
    form         text NOT NULL,
    fiscal_year  integer NOT NULL,
    period_end   date NOT NULL,
    filed_date   date NOT NULL,
    primary_doc  text NOT NULL,
    url          text NOT NULL,
    UNIQUE (cik, form, period_end)
);

-- Canonical metric -> candidate XBRL concepts, lowest priority number wins per company-year.
CREATE TABLE IF NOT EXISTS metric_concept_map (
    metric      text NOT NULL,
    label       text NOT NULL,
    taxonomy    text NOT NULL,
    concept     text NOT NULL,
    unit        text NOT NULL,
    period_type text NOT NULL CHECK (period_type IN ('duration', 'instant')),
    priority    smallint NOT NULL,
    PRIMARY KEY (metric, taxonomy, concept)
);

-- As-reported facts: one row per (period, filing). A 10-K re-reports prior periods, so the
-- same period appears once per filing; restatements are therefore visible, and the
-- "latest filed wins" choice is made in SQL (v_metric_values), not lost at load time.
CREATE TABLE IF NOT EXISTS financial_facts (
    id           bigserial PRIMARY KEY,
    cik          integer NOT NULL REFERENCES companies (cik),
    taxonomy     text NOT NULL,
    concept      text NOT NULL,
    unit         text NOT NULL,
    period_start date,                 -- NULL for instant (balance-sheet) facts
    period_end   date NOT NULL,
    fiscal_year  integer NOT NULL,     -- derived from period_end, NOT companyfacts' "fy" (that is the filing's FY)
    value        numeric NOT NULL,
    accession    text NOT NULL,
    form         text NOT NULL,
    filed_date   date NOT NULL,
    frame        text,
    run_id       bigint REFERENCES etl_runs (run_id),
    CONSTRAINT financial_facts_natural_key
        UNIQUE NULLS NOT DISTINCT (cik, taxonomy, concept, unit, period_start, period_end, accession)
);

CREATE TABLE IF NOT EXISTS filing_sections (
    id         bigserial PRIMARY KEY,
    accession  text NOT NULL REFERENCES filings (accession) ON DELETE CASCADE,
    section    text NOT NULL CHECK (section IN ('item1', 'item1a', 'item7')),
    title      text NOT NULL,
    content    text NOT NULL,
    char_count integer GENERATED ALWAYS AS (length(content)) STORED,
    UNIQUE (accession, section)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id          bigserial PRIMARY KEY,
    accession   text NOT NULL REFERENCES filings (accession) ON DELETE CASCADE,
    cik         integer NOT NULL REFERENCES companies (cik),   -- denormalized for filtered retrieval
    fiscal_year integer NOT NULL,
    section     text NOT NULL,
    ordinal     integer NOT NULL,
    content     text NOT NULL,
    char_start  integer NOT NULL,
    char_end    integer NOT NULL,
    embedding   vector(384),           -- granite-embedding-small-english-r2; change dim if you change model
    embed_model text,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (accession, section, ordinal)
);
