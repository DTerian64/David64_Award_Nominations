"""
seed_email_templates.py
=======================
Idempotently upsert email templates into dbo.EmailTemplates from the seed-data
modules, over a parameterized pyodbc connection (so NVARCHAR/Unicode is sent
correctly — no literal/codepage games).

Seed sets:
    (TenantId=1, Lang='en')  ← EN_TEMPLATES  (system defaults / fallback)
    (TenantId=2, Lang='ko')  ← KO_TEMPLATES  (Korean overrides for tenant 2)

Re-runnable: MERGE by (TenantId, TemplateKey, Lang). Tenant rows are independent,
so seeding 'ko' never touches the 'en' defaults. Run AFTER `alembic upgrade head`
(migration 0025) and BEFORE deploying the handler rewiring.

Usage
-----
  python scripts/seed_email_templates.py              # seed en + ko
  python scripts/seed_email_templates.py --only ko    # only Korean (tenant 2)
  python scripts/seed_email_templates.py --dry-run
"""

import argparse
import os
import sys

from dotenv import find_dotenv, load_dotenv

_DOTENV = find_dotenv(usecwd=True)
load_dotenv(_DOTENV)

sys.path.insert(0, os.path.dirname(__file__))
from email_templates_seed_data import EN_TEMPLATES        # noqa: E402
from email_templates_seed_data_ko import KO_TEMPLATES     # noqa: E402
from certificate_labels_seed_data import CERT_EN, CERT_KO  # noqa: E402

# (tenant_id, lang, templates)
SEED_SETS = [
    (1, "en", EN_TEMPLATES),
    (2, "ko", KO_TEMPLATES),
    (1, "en", CERT_EN),   # certificate labels (default)
    (2, "ko", CERT_KO),   # certificate labels (Korean)
]

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
    return (
        f"Driver={os.getenv('DB_DRIVER', '{ODBC Driver 18 for SQL Server}')};"
        f"Server={os.environ['SQL_SERVER']};Database={os.environ['SQL_DATABASE']};"
        f"UID={os.environ['SQL_USER']};PWD={os.environ['SQL_PASSWORD']};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed email templates (en + ko).")
    ap.add_argument("--only", choices=["en", "ko"], help="Seed only this language.")
    ap.add_argument("--dry-run", action="store_true", help="List actions without writing.")
    args = ap.parse_args()

    sets = [s for s in SEED_SETS if not args.only or s[1] == args.only]

    print("── email template seed ──────────────────────────────────────")
    print(f"  .env loaded from : {_DOTENV or '(process env)'}")
    print(f"  SQL_SERVER       : {os.getenv('SQL_SERVER', '(unset)')}")
    print(f"  SQL_DATABASE     : {os.getenv('SQL_DATABASE', '(unset)')}")
    for tid, lang, tpls in sets:
        print(f"  set              : tenant={tid} lang={lang}  ({len(tpls)} templates)")
    print("─────────────────────────────────────────────────────────────")

    if args.dry_run:
        for tid, lang, tpls in sets:
            for key in tpls:
                print(f"  would upsert ({tid}, {key!r}, {lang!r})")
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

    total = 0
    try:
        cur = conn.cursor()
        for tid, lang, tpls in sets:
            for key, tpl in tpls.items():
                subject, body = tpl["subject"], tpl["body"]
                cur.execute(
                    _MERGE,
                    (tid, key, lang,                 # USING src
                     subject, body,                  # WHEN MATCHED
                     tid, key, lang, subject, body), # WHEN NOT MATCHED
                )
                print(f"  upserted ({tid}, {key!r}, {lang!r})")
                total += 1
        conn.commit()
        print(f"\nOK: {total} template rows seeded across {len(sets)} set(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
