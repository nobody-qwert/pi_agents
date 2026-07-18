FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        openssh-client \
        qemu-system-x86 \
        qemu-utils \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10002 vmmanager \
    && install -d -o vmmanager -g vmmanager -m 0700 /var/lib/orchestrator/vms

COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir /app/backend "uvicorn[standard]>=0.34,<1"

USER vmmanager
EXPOSE 8010
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "orchestrator.vm_manager_asgi:app", "--host", "0.0.0.0", "--port", "8010"]
