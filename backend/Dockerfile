FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 orchestrator
COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir /app/backend "uvicorn[standard]>=0.34,<1"
COPY config /app/config
USER orchestrator
EXPOSE 8000
CMD ["uvicorn", "orchestrator.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
