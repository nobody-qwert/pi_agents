FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 orchestrator \
    && install -d -o orchestrator -g orchestrator -m 2770 /var/lib/orchestrator/artifacts \
    && install -d -o orchestrator -g orchestrator -m 0770 /run/lm-studio
COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir /app/backend "uvicorn[standard]>=0.34,<1"
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/migrations /app/backend/migrations
COPY config /app/config
ENV ORCHESTRATOR_ALEMBIC_CONFIG=/app/backend/alembic.ini
USER orchestrator
EXPOSE 8000
CMD ["uvicorn", "orchestrator.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
