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
   query at the end must show `sql-migrations-<env> -> db_ddladmin` and
   `sql-app-readwrite-<env> -> db_datareader/db_datawriter` only.
