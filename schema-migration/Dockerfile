# schema-migration — standalone Alembic runner (ADR-0001)
# Ephemeral, one-shot image: applies `alembic upgrade head` and exits.
# Owned by no runtime service; run by the deploy-schema-migration pipeline
# as the `sql-migrations` identity (db_ddladmin).
FROM python:3.11-slim-bookworm

# Microsoft ODBC Driver 18 + unixODBC (required by pyodbc against Azure SQL).
# Use Microsoft's packages-microsoft-prod.deb: it installs the apt repo AND its
# keyring at the exact path the repo's `signed-by=` references. Writing the key
# to trusted.gpg.d/microsoft.asc (the old method) leaves signed-by dangling and
# fails apt on Debian 12.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
 && dpkg -i packages-microsoft-prod.deb \
 && rm packages-microsoft-prod.deb \
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
