FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ── Microsoft ODBC Driver 18 for SQL Server ───────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    bash \
    procps \
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
COPY integrity-check/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY integrity-engine-core /tmp/integrity-engine-core
RUN pip install --no-cache-dir /tmp/integrity-engine-core

# ── Application code ──────────────────────────────────────────────────────────
COPY integrity-check/ .

# No EXPOSE — pure Service Bus worker, no HTTP server.
# KEDA wakes the container when messages arrive on fraud-processor subscription.

SHELL ["/bin/bash", "-c"]

CMD ["python", "main.py"]
