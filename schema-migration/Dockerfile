# schema-migration — standalone Alembic runner (ADR-0001)
# Ephemeral, one-shot image: applies `alembic upgrade head` and exits.
# Owned by no runtime service; run by the deploy-schema-migration pipeline
# as the `sql-migrations` identity (db_ddladmin).
FROM python:3.11-slim

# Microsoft ODBC Driver 18 + unixODBC (required by pyodbc against Azure SQL).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg apt-transport-https \
 && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null \
 && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic

# Default: apply all pending migrations. Override the command for --sql, downgrade, etc.
ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]
