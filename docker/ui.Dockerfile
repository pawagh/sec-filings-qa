# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN pip install "streamlit>=1.38" "httpx>=0.27" "pandas>=2.2" && useradd --create-home --uid 10001 app
WORKDIR /app
COPY ui/app.py ./app.py
USER 10001
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--browser.gatherUsageStats=false"]
