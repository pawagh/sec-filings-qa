"""FastAPI service: POST /ask, GET /health (liveness), GET /ready (readiness), GET /metrics (Prometheus)."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pgvector.psycopg import register_vector
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from secqa.agent import llm, service
from secqa.config import get_settings
from secqa.rag.embeddings import get_model

log = logging.getLogger("secqa.api")

REQUESTS = Counter("secqa_http_requests_total", "HTTP requests", ["path", "method", "status"])
LATENCY = Histogram("secqa_http_request_seconds", "HTTP request latency", ["path"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
ROUTES = Counter("secqa_router_decisions_total", "Router decisions", ["route"])
STAGE = Histogram("secqa_stage_seconds", "Time per pipeline stage", ["stage"],
                  buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10))

state: dict = {"model_loaded": False}


def _configure(conn):
    register_vector(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    state["pool"] = ConnectionPool(s.database_url, min_size=1, max_size=8, configure=_configure, open=False)
    state["pool"].open(wait=False)
    await run_in_threadpool(get_model)  # load embeddings before reporting ready
    state["model_loaded"] = True
    yield
    state["pool"].close()


app = FastAPI(title="SEC Filings Q&A", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    path = request.scope.get("route").path if request.scope.get("route") else "unmatched"
    REQUESTS.labels(path, request.method, str(response.status_code)).inc()
    LATENCY.labels(path).observe(time.perf_counter() - t0)
    return response


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    router: str = Field(default="rules", pattern="^(rules|llm)$")


@app.post("/ask")
async def ask(req: AskRequest) -> dict:
    def _run():
        with state["pool"].connection() as conn:
            return service.ask(conn, req.question, top_k=req.top_k, router=req.router)

    try:
        out = await run_in_threadpool(_run)
    except Exception as e:
        log.exception("ask failed")
        raise HTTPException(status_code=500, detail=f"internal error: {type(e).__name__}") from e
    ROUTES.labels(out["route"]).inc()
    for stage, ms in out.get("timings_ms", {}).items():
        STAGE.labels(stage).observe(ms / 1000)
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    checks = {"model_loaded": state.get("model_loaded", False), "database": False, "chunks_embedded": 0}
    try:
        with state["pool"].connection(timeout=2) as conn:
            checks["chunks_embedded"] = conn.execute(
                "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL").fetchone()[0]
            checks["database"] = True
    except Exception as e:
        checks["error"] = type(e).__name__
    checks["llm_provider"] = llm.provider()
    ok = checks["model_loaded"] and checks["database"]
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not_ready", **checks}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
