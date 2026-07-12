# schema-migration

Standalone Alembic project that **owns the Award Nomination database schema** (ADR-0001).

Alembic used to live in `backend/`, coupling schema changes to the backend's repo and
deploy lifecycle. It now lives here as its own artifact so any service's schema need
(backend, integrity-check, fraud-analytics, …) flows through one place and one gated
pipeline — and no runtime container is redeployed for a schema-only change.

## Layout

```
schema-migration/
  alembic.ini
  alembic/
    env.py            # models-free; SQL-auth locally, Entra token in CI/Azure
    script.py.mako
    versions/         # the migration history (moved from backend/)
  requirements.txt
  Dockerfile          # one-shot image: `alembic upgrade head`
  .env.example
```

## Models-free by design

Migrations are hand-written, applied with `alembic upgrade head`. Alembic's
`target_metadata` is `None`, so this project has **no ORM dependency** — it does not
import the backend. (Autogenerate is not used; if it's ever reintroduced, wire a shared
models package into `env.py`.)

## Running locally

```
cd schema-migration
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # set SQL_USER/PASSWORD, or `az login` for Entra auth
alembic upgrade head
```

## Authoring a migration

```
alembic revision -m "describe change"      # then hand-edit the generated file in versions/
```

## CI

`.github/workflows/deploy-schema-migration.yaml` builds this image and runs
`alembic upgrade head` as the secretless `schema-migrations` identity (Entra group
`sql-migrations-<env>` → `db_ddladmin`), gated by the GitHub Environment. Runtime
identities never hold DDL rights.
