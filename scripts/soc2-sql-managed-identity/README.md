# SOC 2 — SQL access bootstrap (ADR-0001)

`db-access-grants.sql` creates the contained database users and grants their
least-privilege roles. It's a **one-time, per-landing-zone** step.

## Why a manual script (not a workflow)

The SQL server is behind a private endpoint + firewall. A GitHub-hosted runner
cannot reach it for data-plane work, so the grants are run from a
**firewall-whitelisted machine** (your dev box, via `my_ips`) as an **Entra admin**
(a member of `sql-admins-<env>`). Terraform provisions everything around the DB
(groups, identities, the SQL Entra admin); this SQL is the only in-database step.

## Run

1. Add yourself to `sql-admins-<env>` in Entra (if not already).
2. From a whitelisted machine, open `db-access-grants.sql`, set `@env` at the top
   (e.g. `sandbox`), and run it against the app DB connected as the Entra admin --
   in SSMS / Azure Data Studio / sqlcmd (no SQLCMD mode needed). The verification
   queries at the end must show:

   - `sql-app-readwrite-<env> -> db_datareader/db_datawriter`
   - `sql-migrations-<env> -> db_ddladmin/db_datareader/db_datawriter`
   - `sql-migrations-<env> -> award_schema_migrator`
   - `award_schema_migrator -> CONTROL` on the `dbo`, `integrity`, and `ops` schemas

`award_schema_migrator` exists because SQL Server requires `CONTROL` on both
tables when enabling or disabling temporal system versioning, and `CONTROL` on
the source object plus authority on the destination schema for `ALTER SCHEMA
... TRANSFER`. The role is intentionally narrower than `db_owner` and does not
grant server-level authority or authority on schemas outside these three
application-managed namespaces.
