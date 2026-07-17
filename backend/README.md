# Backend

The backend is a Python 3.12+ package managed with
[uv](https://docs.astral.sh/uv/). From a clean checkout, install the package and
its development tools with:

```bash
cd backend
uv sync --group dev
```

Application settings are read from the process environment. The currently
supported settings are all required; there are no provider, model, URL, or
credential fallbacks:

```text
APP_ENV=development
APP_BASE_URL=http://localhost:3000
MODEL_PROVIDER=lm-studio
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_MODEL_ID=qwen3.6-27b
```

`LM_STUDIO_API_KEY` is required because the OpenAI-compatible protocol expects
it, even though a local LM Studio server normally uses a non-secret placeholder.
Real secrets must be supplied at runtime and must not be committed.

Run the complete backend quality suite from `backend/`:

```bash
uv run pytest
uv run mypy src tests
uv run ruff check .
uv run ruff format --check .
```

To verify the installed package directly:

```bash
uv run python -c "import orchestrator"
```

## Artifact storage

`ArtifactService` is the only boundary for authoritative artifact bytes.  The
local adapter is configured with its volume root at construction time; its
default policy permits JSON, octet-stream, PDF, Markdown, and plain-text
content up to 10 MiB per immutable version.  Callers provide a validated logical
artifact ID and expected current version, never a filesystem path.  Metadata
records retain the content hash, size, type, and tenant/run/role scope; operator
previews intentionally omit the internal storage key.
