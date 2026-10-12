# SEC Filings Q&A Assistant — Project Spec (v2)

This is a tightened version of the original project brief. It keeps the same goal and phases. It adds the
EDGAR details that would otherwise break the build, sets acceptance criteria for each phase, and fixes
the evaluation design so the numbers you put on a resume can be defended.

## Goal

Answer questions such as *"How did Apple's R&D spending change from FY2022 to FY2024, and what risks
did it cite?"* by combining:

- **SQL** over XBRL financial facts, for the numbers (exact and auditable)
- **Hybrid RAG** over 10-K narrative text, for the explanations (cited)

Everything runs on Kubernetes. It is enterprise data work that maps directly to IBM Consulting and IBM
Software roles, and every input is free.

## What changed from v1, and why

| v1 said | Problem | v2 does |
|---|---|---|
| "Use the companyfacts API" | Its `fy` field is the fiscal year of the **filing**, not the period. Every 10-K re-reports 2–3 prior years, so naive loads double-count and mislabel years. | Fiscal year comes from the period `end` date. Dedupe on (cik, concept, unit, start, end) and keep the latest-filed value (restatements win). Keep `accn` for lineage. |
| "Financial figures" | The same metric uses different us-gaap tags across companies (`Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, …). | A `metric_concept_map` table maps canonical metrics to ordered candidate tags. The ETL picks the first tag present for each company and year. |
| "Annual facts" | Annual 10-K facts mix full-year durations with quarterly ones (Q4 values sit inside 10-Ks). | Keep flow facts only when the duration is 350–380 days. Keep balance-sheet facts with no start date (instants). |
| "Split Items 1, 1A, 7" | 10-K HTML has a table of contents that repeats every heading, and headings vary in format. | For each item, take the **longest** span between that heading and the next item heading. This skips the TOC. Flag sections that come out implausibly short as a data-quality warning. |
| "SEC EDGAR APIs" | SEC blocks requests that have no declared `User-Agent` or that exceed 10 req/s. | Every request sends `SEC_USER_AGENT="App Name you@email"`. A rate limiter allows ≤ 8 req/s, and raw downloads are cached to disk so re-runs are free. |
| "Re-runnable loads" | Not defined. | Every table has a natural key and loads with `INSERT … ON CONFLICT DO NOTHING/UPDATE`. Chunks are keyed on (filing, section, ordinal). Running twice changes 0 rows. A test enforces this. |
| "Vector + full-text search" | No fusion method or filtering was specified. | Fuse vector and full-text results with Reciprocal Rank Fusion (k=60), with **metadata pre-filtering** by company/year from the router. Most of the recall gain comes from this. |
| "Route with LangChain" | No fallback, so the app breaks with no API key or when the LLM is flaky. | Two routers behind one interface: (a) LangChain tool-calling with Gemini, (b) a deterministic rule/entity router. Both call the same fixed, parameterized SQL tools. The app runs end to end with no API key. |
| "Answers with citations" | Citation format was undefined, so it can't be scored. | Each citation is `{ticker, fiscal_year, section, accession, chunk_id, url}`. Numbers cite `{ticker, fy, metric, accession}`. |
| "Hand-labeled 50 questions" + "numeric accuracy checked against SQL" | If the gold labels come from the same SQL the system runs, the test is circular. | Numeric gold values are **drafted** from SQL, then **hand-verified against the filing** (`verified: true`). Only verified items count toward the headline number. The eval still exercises routing, entity parsing, unit handling and formatting. |
| "Recall@5" | Gold chunk IDs change whenever chunking changes. | Gold retrieval targets are `(ticker, fiscal_year, section)` plus required key phrases. A hit means a top-5 chunk matches the target and contains a key phrase. |
| Embedding model unspecified | — | `ibm-granite/granite-embedding-small-english-r2` (384-d, 8k context, Apache-2.0). Configurable. |

## Phase 1 — ETL + SQL (usable on its own)

**Scope:** 50 S&P 500 companies across sectors (`config/companies.csv`). 10-Ks for FY2023–FY2025, plus
the FY2022 comparatives that those 10-Ks contain (needed for YoY).

**Tables:** `companies`, `filings`, `financial_facts`, `metric_concept_map`, `filing_sections`,
`document_chunks`, `etl_runs`, `dq_audit`.

**Data-quality checks** (run before load; failures written to `dq_audit` with run id, check, severity, payload):
1. Required fields not null (cik, concept, end, value, accession).
2. No duplicate (cik, concept, unit, period) after dedup.
3. Unit consistency: monetary metrics in USD, share counts in `shares`, EPS in `USD/shares`.
4. Period sanity: duration in [350, 380] days for flow metrics.
5. Coverage: each company/year has core metrics (Revenue, NetIncome, Assets). A gap is a warning, not an error.
6. Section sanity: Items 1, 1A and 7 each extracted with ≥ 2,000 chars.
7. Value sanity: Assets = Liabilities + Equity within 1% (warning).

**SQL you write yourself** (`sql/analytics/`): at least 10 queries using joins, CTEs, window functions
(`LAG`, `RANK`, `PERCENT_RANK`, moving averages), `FILTER`, and `GROUPING SETS`. Also: B-tree indexes on
access paths, a GIN index for full-text search, and the `mv_company_year_metrics` materialized view (a wide
company-year table with derived margins), refreshed `CONCURRENTLY` at the end of each ETL run.

**Acceptance:** `make etl` loads 50 companies. A second run inserts 0 new fact rows. All analytic queries run
in under 200 ms on the loaded data. `pytest tests/` passes against an ephemeral Postgres.

## Phase 2 — RAG + Evaluation

- **Chunking:** paragraph-aware, about 1,200 chars with 200 overlap, never crossing section boundaries.
  Store chunk ordinal and char offsets.
- **Storage:** `document_chunks.embedding vector(384)` with an HNSW index (cosine), and a generated `tsvector`
  column with a GIN index.
- **Retrieval:** top-k vector + top-k FTS, filtered by ticker, year and section when they're known, then fused with RRF.
- **Router:** classifies each question as `sql`, `rag` or `both`, and extracts tickers, years and metrics. SQL tools are
  fixed and parameterized: `get_metric`, `compare_metric_over_years`, `rank_companies_by_metric`,
  `get_company_profile`. **The LLM never writes SQL.**
- **Answering:** Gemini (`GEMINI_API_KEY`) or a local Granite via Ollama. With neither, an extractive
  fallback composes the answer from SQL results and the top passages. Answers always carry citations.
- **Eval** (`eval/`): 50 questions (20 numeric, 15 narrative, 15 hybrid). Metrics:
  - routing accuracy
  - retrieval recall@5 and MRR (section-level gold + key phrase)
  - numeric exact match (±0.5% relative tolerance, unit-normalized), on verified items only
  - citation accuracy (share of cited sources that match gold targets)
  - answer correctness (key-fact coverage; optional LLM-as-judge when a key is set)

  Results are written to `eval/results/<timestamp>.json` plus a markdown summary, so every number on the resume
  traces back to a file.

## Phase 3 — Docker + Kubernetes + CI/CD

- **Images:** `api` (FastAPI, CPU-only torch, model baked in at build time) and `ui` (Streamlit).
  Multi-stage builds, non-root user.
- **API:** `POST /ask`, `GET /health` (liveness), `GET /ready` (checks the DB plus the loaded model), and `GET /metrics`
  (Prometheus: request count/latency by route, router decision counts, retrieval latency).
- **Kubernetes** (kustomize base + `kind` and `gke` overlays):
  - API Deployment: 2 replicas, readiness/liveness probes, resource requests/limits.
  - Service + Ingress (ingress-nginx on kind; GCE ingress on GKE).
  - ConfigMap for settings, Secret for `GEMINI_API_KEY` / DB password (template only, never committed).
  - Postgres (`pgvector/pgvector:pg16`) StatefulSet with a PVC on kind; Cloud SQL on GKE.
  - ETL CronJob (weekly) with `concurrencyPolicy: Forbid`. Because loads are idempotent, retries are safe.
  - DB migration Job, run before the API rolls out.
- **CI (GitHub Actions):** ruff, then pytest with a `pgvector` service container, then building both images, then pushing to
  GHCR on `main`, then deploying (manual-approval `workflow_dispatch` job that applies the overlay).
- **Cost guard:** GKE Autopilot only on demand. `make gke-down` deletes the cluster, and the README warns about it.

## Resume bullet (fill in from `eval/results/`)

*"Built a filings Q&A system over N SEC 10-Ks (50 S&P 500 companies): idempotent PostgreSQL ETL with
data-quality auditing, hybrid pgvector + full-text RAG with RRF (LangChain, IBM Granite embeddings), and
parameterized SQL tool routing; reached X% numeric exact-match and Y recall@5 on a 50-question
hand-verified eval; deployed on Kubernetes (kind/GKE) with GitHub Actions CI/CD."*
