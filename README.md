# SEC Filings Q&A

Ask questions like *"How did Apple's R&D spending change from 2022 to 2024, and what risks did it cite?"*
and get an answer that combines **exact numbers from SQL** (XBRL facts) with **cited passages from
10-K text** (hybrid pgvector + full-text retrieval). It runs on Kubernetes.

The full spec, including what changed from the original brief and why, is in
[docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

```
question ──► router (rules | LangChain tool-calling)
               ├─ sql  ──► fixed, parameterized SQL tools ──► mv_company_year_metrics / v_metric_values
               └─ rag  ──► hybrid retrieval: HNSW cosine + tsvector, RRF fusion, ticker/year/section filters
                           └─► answer (Gemini / Ollama Granite / extractive) with citations
EDGAR (companyfacts, submissions, 10-K HTML) ──► ETL + data-quality audit ──► PostgreSQL 16 + pgvector
```

## What's built

| Phase | Status |
|---|---|
| 1. ETL + SQL | 50 S&P 500 companies, 150 10-Ks (FY2023–FY2025), 7,482 as-reported facts. Idempotent loads (a re-run inserts 0 rows; a test enforces this). 7 data-quality checks write to `dq_audit`. 13 analytic queries, all under 15 ms. |
| 2. RAG + eval | 32,789 chunks from Items 1/1A/7, embedded with IBM Granite (`granite-embedding-small-english-r2`). Hybrid search with RRF. Rule router plus a LangChain tool-calling agent. 50-question eval with gold answers checked against the filings. |
| 3. Docker + K8s + CI | API and UI images. kustomize base with `kind` and `gke` overlays: 2-replica Deployment with startup/liveness/readiness probes, Service, Ingress, ConfigMap/Secret, Postgres StatefulSet, ETL CronJob, migration initContainer. GitHub Actions: lint, tests against a pgvector service container, image builds, manual GKE deploy. |

Verified end to end on a local **kind** cluster: ingress → 2 API replicas → in-cluster Postgres, and an
in-cluster ETL CronJob run that changed 0 rows. **Not run:** the GKE overlay, the CI workflow (no
remote repo yet), and the Gemini/LangChain path (no API key here).

## Evaluation results

From `eval/results/latest.md` (rule router, extractive answers, no LLM):

| Metric | Value |
|---|---|
| Routing accuracy | 100% (50/50) |
| Numeric exact match (gold checked against filings) | 100% (47/47 numbers) |
| Retrieval recall@5 / MRR | 100% / 0.85 |
| Citation precision | 93.2% |
| Answer correctness (key facts): numeric / narrative / hybrid | 100% / 93% / 60% |
| Latency p50 / p95 (local) | 90 / 577 ms |

**Retrieval ablation** (30 narrative and hybrid questions):

| Retriever | recall@5 | MRR |
|---|---|---|
| hybrid + metadata filters | **100%** | **0.85** |
| hybrid, no filters | 76.7% | 0.53 |
| vector only + filters | 86.7% | 0.80 |
| full-text only + filters | 100% | 0.86 |
| full-text only, no filters | 26.7% | 0.13 |

**Caveats**
- The 50 questions were written alongside the router, so routing and entity extraction are tuned to them.
  A held-out question set (add to `eval/spec.py`, then `make eval`) gives a fairer measure.
- Gold numbers are checked automatically: each figure must appear in that company's 10-K HTML.
  `build_gold.py` reads the HTML with its own extractor, independent of the ETL pipeline. They have not
  been reviewed by hand.
- Hybrid answers score 60% because extractive mode stitches retrieved sentences together instead of
  writing an answer. Set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY`, then rerun `make eval` (and
  `--router llm`) to measure the LLM path.
- Metadata filtering accounts for most of the retrieval quality. Full-text search with filters is about as
  good as hybrid on this set, because the questions name distinctive terms.

## Data issues found during the build

- **companyfacts `fy` is the filing's fiscal year, not the period's.** A FY2024 10-K tags FY2022 values
  `fy=2024`. The pipeline derives the fiscal year from the period end date and keeps every as-reported
  version, so `sql/analytics/10_restatements.sql` can show restatements.
- **Stock splits show up as "restatements"**: NVDA and AVGO share counts are restated 10×. The query labels these.
- **Concept choice changes the numbers.** `RevenueFromContractWithCustomer…` leaves out Pfizer's alliance
  revenue (FY2023: $50.9B vs. $59.6B total). Gold verification flagged it. Fixing it was a config
  priority change plus a view refresh, with no reload, because the "which concept wins" logic lives in SQL.
- **XOM's ticker now maps to a new holding-company CIK** with no 10-K history, so `companies.csv` takes a CIK override.
- **Inside Kubernetes, the migrator found no files and reported success.** It now fails loudly, and the
  image sets `SECQA_HOME`.
- **Granite r2 ships bf16 weights.** On CPUs without bf16 support, PyTorch falls back to a path about 180×
  slower (219s vs. 1.2s for 60 sentences), which caused 504s behind the ingress. The model now always loads in float32.

Remaining data-quality warnings, logged in `dq_audit` on purpose:
- CVX, WFC, XOM and JPM FY2025 have no Item 7. It lives in an exhibit or annual-report layout that the fallback doesn't parse.
- HON's risk factors sit inside its MD&A.
- GE, HON, INTC and MCD have no separate Item 1, because they use the cross-reference-index layout.
- Balance-sheet identity gaps for XOM, UNH, TSLA and CVX: equity excludes noncontrolling interest.

## Quickstart

Needs Python 3.11+, Docker, and about 2 GB of disk for the SEC downloads, model and database.
`make setup` uses [`uv`](https://docs.astral.sh/uv/); without it, run
`python3.12 -m venv .venv && .venv/bin/pip install -e ".[api,ui,dev]"` instead.

```bash
make setup
```
```bash
cp .env.example .env   # then set SEC_USER_AGENT to your app name and email
```
```bash
make db
```
```bash
make etl          # about 10 min first time: facts, 10-K text, embeddings (raw downloads cached in data/raw)
```
```bash
make test         # 36 tests, including integration tests against Postgres
```
```bash
make analytics    # run the 13 SQL queries with timings
```
```bash
make eval         # needs the full ETL; writes eval/results/<timestamp>.{json,md}
```
```bash
make api          # http://localhost:8000/docs
```
```bash
make ui           # http://localhost:8501
```

### Kubernetes (kind)

```bash
make kind-up      # kind cluster plus ingress-nginx
```
```bash
make kind-load    # build images and side-load them into kind
```
```bash
make kind-deploy  # creates a dev Secret if missing, then applies k8s/overlays/kind
```

Load data either by running the ETL in the cluster (`kubectl -n secqa create job etl-1 --from=cronjob/secqa-etl`)
or by restoring a local dump:

```bash
docker compose exec -T db pg_dump -U secqa -d secqa -Fc > data/secqa.dump
```
```bash
kubectl -n secqa exec -i postgres-0 -- pg_restore -U secqa -d secqa --clean --if-exists --no-owner < data/secqa.dump
```

Then open http://secqa.localtest.me (UI) and http://api.secqa.localtest.me/docs (API).
`*.localtest.me` resolves to 127.0.0.1. `make kind-down` deletes the cluster.

**GKE Autopilot** (`k8s/overlays/gke`) bills for as long as it runs. Use it for a demo, then run `make gke-down`.

## Layout

```
config/            companies.csv (tickers, sectors, aliases, CIK overrides), metric_concepts.csv
sql/migrations/    schema, indexes (HNSW, GIN), v_metric_values + mv_company_year_metrics
sql/analytics/     13 queries: LAG/LEAD, RANK, PERCENT_RANK, FIRST/LAST_VALUE, GROUPING SETS, FILTER, percentiles
src/secqa/edgar/   rate-limited, cached SEC client
src/secqa/etl/     facts, filings, section extraction, chunking, quality checks, idempotent loaders
src/secqa/rag/     Granite embeddings, hybrid retrieval
src/secqa/agent/   router, SQL tools, answer composition, LLM providers, LangChain agent
src/secqa/api/     FastAPI: /ask /health /ready /metrics
ui/                Streamlit
eval/              spec.py → build_gold.py → questions.jsonl → run_eval.py → results/
k8s/               kustomize base + kind/gke overlays
.github/workflows/ CI
```
