# Alembic — Database Migration Guide

All commands should be run from the `backend/` directory.

---

## Prerequisites

Alembic is included in `requirements.txt`. Install it along with the rest of the dependencies:

```bash
pip install -r requirements.txt
```

Set the required environment variables before running any Alembic command:

```bash
# Development (SQL authentication)
export SQL_SERVER=<your-server>.database.windows.net
export SQL_DATABASE=AwardNominations
export SQL_USER=<sql-username>
export SQL_PASSWORD=<sql-password>

# Production (Managed Identity — set this instead of SQL_USER/SQL_PASSWORD)
export USE_MANAGED_IDENTITY=true
export SQL_SERVER=<your-server>.database.windows.net
export SQL_DATABASE=AwardNominations
```

---

## Initial Setup (first-time only)

### 1. Apply all migrations

This creates the full schema including the `Tenants` table and the `TenantId`
column on `Users`:

```bash
alembic upgrade head
```

### 2. Seed tenant and user data

After the schema is in place, run the seed script to populate (./../Data/seed_tenants.py):
- **Tenant 1** — Rideshare David64 Organization (from `Data/exportUsers_2026-03-09.csv`)
- **Tenant 2** — ACME Corp (synthetic data, 120 employees)

```bash
python seed_tenants.py
```

The seed script is idempotent — safe to re-run without creating duplicates.

---

## Common Commands

| Task | Command |
|---|---|
| Apply all pending migrations | `alembic upgrade head` |
| Roll back the last migration | `alembic downgrade -1` |
| Roll back to a specific revision | `alembic downgrade <revision_id>` |
| Show current revision in DB | `alembic current` |
| Show migration history | `alembic history --verbose` |
| Generate a new migration (autogenerate) | `alembic revision --autogenerate -m "describe change"` |
| Generate a blank migration | `alembic revision -m "describe change"` |
| Generate SQL script without connecting | `alembic upgrade head --sql` |

---

## Adding a New Migration

1. Edit the ORM models in `sqlhelper2.py` to reflect the schema change.
2. Generate the migration file:
   ```bash
   alembic revision --autogenerate -m "short description of change"
   ```
3. Review the generated file in `alembic/versions/` — autogenerate is a starting
   point, not always complete. Verify the `upgrade()` and `downgrade()` functions
   before applying.
4. Apply it:
   ```bash
   alembic upgrade head
   ```

---

## Migration History

| Revision | Description |
|---|---|
| `0001` | Initial schema — all baseline tables + `Tenants` table + `TenantId` FK on `Users` |

---

## Notes

- **Connection** is built dynamically from environment variables in `alembic/env.py` — the `sqlalchemy.url` field in `alembic.ini` is intentionally blank.
- **MS SQL Server** requires adding `TenantId` as nullable first, back-filling, then altering to `NOT NULL`. Migration `0001` handles this automatically for existing databases.
- **Fresh databases** (no pre-existing `Users` table) have `TenantId NOT NULL` applied directly at table creation.
- The `uq_users_upn_tenant` constraint replaces the old global unique constraint on `userPrincipalName` — UPNs are now unique per tenant, not globally.
