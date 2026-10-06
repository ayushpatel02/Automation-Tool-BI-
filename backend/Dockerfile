# Backend image. Includes the .NET runtime so Tabular Editor 2 CLI can validate TMDL.
FROM python:3.12-slim AS base

# --- .NET runtime for Tabular Editor 2 CLI (TMDL validation) ---
# TE2 is a free, Microsoft-ecosystem tool. The binary is fetched at build time into
# /opt/te2 and referenced via TE2_CLI_PATH. See tools/te2/README for the download step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip libicu-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "." \
    && pip install --no-cache-dir ".[warehouses]" || true

COPY app ./app

# Cache the PBIR JSON schemas for offline validation (best-effort; needs network at build).
RUN python -m app.validation.fetch_schemas || echo "schema fetch skipped (offline build)"

ENV GENERATED_DIR=/data/generated
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
