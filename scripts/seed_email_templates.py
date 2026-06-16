"""
seed_email_templates.py
=======================
Idempotently upsert the default (TenantId=1, Lang='en') email templates into
dbo.EmailTemplates from scripts/email_templates_seed_data.py.

Re-runnable: each run MERGEs by (TenantId, TemplateKey, Lang), updating the
Subject/BodyTemplate and bumping Version. Tenants override by inserting their
own rows; this script never touches non-default rows.

Requires SQL_SERVER / SQL_DATABASE / SQL_USER / SQL_PASSWORD in the environment
(or a .env file). Run AFTER `alembic upgrade head` (migration 0025) and BEFORE
deploying the handler rewiring.

Usage
-----
  python scripts/seed_email_templates.py            # upsert defaults
  python scripts/seed_email_templates.py --dry-run  # show what would change
"""

import argparse
import os
import sys

from dotenv import find_dotenv, load_dotenv

_DOTENV = find_dotenv(usecwd=True)
load_dotenv(_DOTENV)

sys.path.insert(0, os.path.dirname(__file__))
from email_templates_seed_data import EN_TEMPLATES  # noqa: E402

DEFAULT_TENANT_ID = 1
LANG = "en"

_MERGE = """
MERGE dbo.EmailTemplates AS tgt
USING (SELECT ? AS TenantId, ? AS TemplateKey, ? AS Lang) AS src
   ON tgt.TenantId = src.TenantId AND tgt.TemplateKey = src.TemplateKey AND tgt.Lang = src.Lang
WHEN MATCHED THEN UPDATE SET
   Subject = ?, BodyTemplate = ?, Active = 1,
   Version = tgt.Version + 1, UpdatedAt = SYSUTCDATETIME(), UpdatedBy = 'seed'
WHEN NOT MATCHED THEN INSERT
   (TenantId, TemplateKey, Lang, Subject, BodyTemplate, Active, Version, UpdatedBy)
   VALUES (?, ?, ?, ?, ?, 1, 1, 'seed');
"""


def _conn_string() -> str:
    server   = os.environ["SQL_SERVER"]
    database = os.environ["SQL_DATABASE"]
    user     = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    driver   = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
    return (
        f"Driver={driver};Server={server};Database={database};"
        f"UID={user};PWD={password};Encrypt=yes;TrustServerCertificate=no;"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed default email templates.")
    ap.add_argument("--dry-run", action="store_true", help="List actions without writing.")
    args = ap.parse_args()

    print("── email template seed ──────────────────────────────────────")
    print(f"  .env loaded from : {_DOTENV or '(process env)'}")
    print(f"  SQL_SERVER       : {os.getenv('SQL_SERVER', '(unset)')}")
    print(f"  SQL_DATABASE     : {os.getenv('SQL_DATABASE', '(unset)')}")
    print(f"  default tenant   : {DEFAULT_TENANT_ID}  lang : {LANG}")
    print(f"  templates        : {', '.join(EN_TEMPLATES)}")
    print("─────────────────────────────────────────────────────────────")

    if args.dry_run:
        for key in EN_TEMPLATES:
            print(f"  would upsert ({DEFAULT_TENANT_ID}, {key!r}, {LANG!r})")
        print("dry-run: no changes written")
        return

    for var in ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD"):
        if not os.getenv(var):
            print(f"\nERROR: {var} is not set (env or .env). Use --dry-run to preview.", file=sys.stderr)
            sys.exit(1)

    import pyodbc
    try:
        conn = pyodbc.connect(_conn_string())
    except Exception as e:
        print(f"\nERROR: could not connect to SQL ({e.__class__.__name__}: {e})", file=sys.stderr)
        sys.exit(1)

    try:
        cur = conn.cursor()
        for key, tpl in EN_TEMPLATES.items():
            subject, body = tpl["subject"], tpl["body"]
            cur.execute(
                _MERGE,
                (DEFAULT_TENANT_ID, key, LANG,        # USING src
                 subject, body,                        # WHEN MATCHED update
                 DEFAULT_TENANT_ID, key, LANG, subject, body),  # WHEN NOT MATCHED insert
            )
            print(f"  upserted ({DEFAULT_TENANT_ID}, {key!r}, {LANG!r})")
        conn.commit()
        print(f"\nOK: {len(EN_TEMPLATES)} default templates seeded.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
