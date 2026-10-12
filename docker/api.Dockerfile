# syntax=docker/dockerfile:1
# API + ETL image (the CronJob and migration Job reuse it). CPU-only torch keeps it ~1.5 GB instead of ~5 GB.
FROM python:3.12-slim AS build
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch && pip install "sentence-transformers>=3.0"
# Bake the embedding model into the image so pods start without reaching Hugging Face.
# (Before COPY src, so code changes don't invalidate this layer.)
ARG EMBED_MODEL=ibm-granite/granite-embedding-small-english-r2
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[api]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/hf /opt/hf
WORKDIR /app
COPY config ./config
COPY sql ./sql
COPY src ./src
ENV PATH=/opt/venv/bin:$PATH SECQA_HOME=/app HF_HOME=/opt/hf HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
    PYTHONUNBUFFERED=1 CACHE_DIR=/tmp/edgar-cache
USER 10001
EXPOSE 8000
CMD ["uvicorn", "secqa.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
