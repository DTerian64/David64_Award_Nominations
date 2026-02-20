FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ────────────────────────────────────────────────────────────────
# Install Microsoft ODBC Driver 18 for SQL Server + required tools
# IMPORTANT: Keep bash and essential utilities for console access
# ────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    bash \
    procps \
    vim-tiny \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
    > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
    msodbcsql18 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create models directory
RUN mkdir -p ml_models

# Expose port
EXPOSE 8000

# Set bash as default shell for console access
SHELL ["/bin/bash", "-c"]

# Run with gunicorn + uvicorn
# --timeout 120: Extend timeout to 120 seconds (fraud assessment + DB queries can take time)
# -w 4: Use 4 workers for better concurrency (adjust based on CPU cores)
# --graceful-timeout 30: Give workers 30s to finish gracefully
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", "main:app"]