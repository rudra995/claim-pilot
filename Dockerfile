# ClaimPilot API. Built from a fresh clone; trains the models from the seeded synthetic data.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CLAIMPILOT_DB=/state/claimpilot.db CLAIMPILOT_UPLOADS=/state/uploads CLAIMPILOT_IMPORTS=/state/imports \
    CLAIMPILOT_LLM=off
COPY pyproject.toml ./
COPY backend ./backend
COPY data ./data
RUN pip install --no-cache-dir .
COPY fixtures ./fixtures
COPY models ./models
RUN python -m backend.models.train_denial && python -m backend.models.train_cost && python -m backend.rag.ingest
VOLUME /state
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
