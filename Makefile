SHELL := /bin/bash
PY ?= .venv/bin/python
TEST_DATABASE_URL ?= postgresql://secqa:secqa@localhost:5432/secqa_test

.PHONY: help setup db etl etl-small embed analytics test lint api ui eval images kind-up kind-load kind-deploy kind-down gke-down

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup:           ## create venv and install everything
	uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[api,ui,dev]"

db:              ## start local Postgres+pgvector and create the test DB
	docker compose up -d db
	until docker compose exec -T db pg_isready -U secqa >/dev/null 2>&1; do sleep 1; done
	-docker compose exec -T db psql -U secqa -c "CREATE DATABASE secqa_test" 2>/dev/null

etl:             ## full ETL: 50 companies, facts + 10-K text + embeddings
	.venv/bin/secqa-etl

etl-small:       ## quick ETL for 3 companies
	.venv/bin/secqa-etl --tickers AAPL,MSFT,NVDA

embed:           ## embed any chunks still missing vectors
	.venv/bin/secqa-embed

analytics:       ## run sql/analytics/*.sql with timings
	$(PY) scripts/run_analytics.py

test:            ## unit + integration tests
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tests scripts eval

api:             ## run the API locally on :8000
	.venv/bin/uvicorn secqa.api.main:app --reload --port 8000

ui:              ## run the Streamlit UI locally on :8501
	API_URL=http://localhost:8000 .venv/bin/streamlit run ui/app.py

eval:            ## run the evaluation suite and write eval/results/
	$(PY) eval/run_eval.py

images:          ## build both Docker images
	docker build -f docker/api.Dockerfile -t secqa-api:dev .
	docker build -f docker/ui.Dockerfile -t secqa-ui:dev .

kind-up:         ## create a kind cluster with ingress-nginx
	kind create cluster --config k8s/overlays/kind/kind-config.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
	kubectl -n ingress-nginx wait --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=180s

kind-load: images ## load local images into kind
	kind load docker-image secqa-api:dev secqa-ui:dev --name secqa

kind-deploy:     ## apply manifests (creates a dev Secret if missing)
	kubectl apply -f k8s/base/namespace.yaml
	kubectl -n secqa get secret secqa-secrets >/dev/null 2>&1 || kubectl -n secqa create secret generic secqa-secrets \
	  --from-literal=POSTGRES_PASSWORD=secqa \
	  --from-literal=DATABASE_URL=postgresql://secqa:secqa@postgres:5432/secqa \
	  --from-literal=SEC_USER_AGENT="$${SEC_USER_AGENT:?set SEC_USER_AGENT='App you@example.com'}" \
	  --from-literal=GEMINI_API_KEY="$${GEMINI_API_KEY:-}"
	kubectl apply -k k8s/overlays/kind
	kubectl -n secqa rollout status statefulset/postgres --timeout=180s
	kubectl -n secqa rollout status deploy/secqa-api --timeout=600s

kind-down:       ## delete the kind cluster
	kind delete cluster --name secqa

gke-down:        ## delete the GKE cluster (stop paying)
	gcloud container clusters delete secqa --region us-central1 --quiet
