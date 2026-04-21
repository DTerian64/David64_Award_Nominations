FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ── Microsoft ODBC Driver 18 for SQL Server ───────────────────────────────────
# Identical installation block to auxiliary-service and backend Dockerfiles.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    bash \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
    > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
    msodbcsql18 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
# Build context: analytics/fraud-analytics-job/
# Everything the job needs is in this directory — no cross-repo paths.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download sentence-transformers model at build time ────────────────────
# all-MiniLM-L6-v2 (~90 MB) is baked into the image so the container never
# hits HuggingFace at runtime.  Benefits:
#   - No runtime network dependency on huggingface.co
#   - No per-run download latency (was adding minutes on cold nodes)
#   - No 404 noise from optional config files sentence-transformers probes
#   - Fully air-gapped operation if needed in future
# The model is cached to /root/.cache/huggingface/hub/ inside the image.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# No EXPOSE — Container Apps Jobs have no HTTP server.
# The job runs to completion and exits; Container Apps reports success/failure
# based on the process exit code.

CMD ["python", "run_job.py"]
