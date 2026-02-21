"""
sql_agent.py
────────────
Translates a natural language question into a T-SQL SELECT query
using the same Azure OpenAI instance already configured in the project.

Usage (from main.py or anywhere else):
    from sql_agent import generate_sql

    sql = generate_sql(question)          # returns a T-SQL string
    rows = sqlhelper.run_query(sql)       # you execute it
"""

import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Schema context — the agent's only knowledge source
# ─────────────────────────────────────────────
_SCHEMA_CONTEXT = """
You are a T-SQL query generator for an Award Nomination system running on Azure SQL Server.

DATABASE SCHEMA:
────────────────────────────────────────────────────────────
TABLE: dbo.Users
  UserId            INT           PK
  userPrincipalName NVARCHAR(100)
  FirstName         NVARCHAR(50)
  LastName          NVARCHAR(50)
  Title             NVARCHAR(100)
  ManagerId         INT           FK → Users.UserId  (self-referencing)
  userEmail         NVARCHAR(100)

TABLE: dbo.Nominations
  NominationId          INT IDENTITY  PK
  NominatorId           INT           FK → Users.UserId
  BeneficiaryId         INT           FK → Users.UserId
  ApproverId            INT           FK → Users.UserId
  Status                NVARCHAR(20)  -- exact values: Pending | Approved | Rejected | Payed
  DollarAmount          INT
  NominationDescription NVARCHAR(500)
  NominationDate        DATE
  ApprovedDate          DATETIME2
  PayedDate             DATETIME2
────────────────────────────────────────────────────────────

STRICT RULES:
1. Return ONLY a valid T-SQL SELECT query — no markdown, no explanation, no semicolons.
2. Always alias joins: u_nom (nominator), u_ben (beneficiary), u_app (approver).
3. Name searches: LOWER(u.FirstName + ' ' + u.LastName) LIKE LOWER('%<term>%')
4. Use TOP N for ranking/limit queries.
5. Date ranges: use DATEADD() and CAST(GETDATE() AS DATE).
6. Status values are case-sensitive: Pending, Approved, Rejected, Payed.
7. Never use INSERT, UPDATE, DELETE, DROP, ALTER, EXEC or any DDL/DML.
8. If the question cannot be answered from this schema, return exactly: UNSUPPORTED
"""

_SAFETY_KEYWORDS = ("insert", "update", "delete", "drop", "alter", "exec", "execute", "truncate", "merge")


def _is_safe(sql: str) -> bool:
    """Reject any non-SELECT statements as a safety guard."""
    lowered = sql.lower().strip()
    if not lowered.startswith("select") and lowered != "unsupported":
        return False
    return not any(kw in lowered for kw in _SAFETY_KEYWORDS)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def generate_sql(client: OpenAI, question: str, model: str | None = None) -> str | None:
    """
    Translate a natural language question into a T-SQL SELECT query.

    Returns:
        str   — a valid T-SQL SELECT query ready to execute
        None  — if the question is not answerable from the schema
                (agent returned UNSUPPORTED or safety check failed)

    Raises:
        Exception — on Azure OpenAI API errors (let the caller handle/log)
    """
    deployment = model or os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")    

    logger.info("sql_agent: generating SQL for question: %s", question[:80])
    logger.debug("sql_agent: using deployment: %s", deployment)

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": _SCHEMA_CONTEXT},
                {
                    "role": "user",
                    "content": (
                        f'Translate this question into a T-SQL SELECT query:\n\n"{question}"\n\n'
                        "Return ONLY the SQL. If unanswerable from the schema, return: UNSUPPORTED"
                    ),
                },
            ],
            temperature=0,       # deterministic — we want exact SQL, not creative variation
            max_tokens=512,
        )
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "not found" in error_str.lower():
            logger.error(
                f"Azure OpenAI Endpoint: {os.getenv('AZURE_OPENAI_ENDPOINT', '')} returned 404 for deployment '{deployment} not found'. "                
                f"Verify AZURE_OPENAI_MODEL env var matches your actual deployment name in Azure. "
                f"Error: {e}"
            )
        raise

    raw = response.choices[0].message.content
    if raw is None:
        logger.warning("sql_agent: LLM returned empty content")
        return None

    raw = raw.strip()

    # Strip accidental markdown fences
    raw = raw.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()

    if raw.upper() == "UNSUPPORTED":
        logger.info("sql_agent: question not answerable from schema — falling back to RAG")
        return None

    if not _is_safe(raw):
        logger.warning("sql_agent: unsafe SQL rejected: %s", raw[:120])
        return None

    logger.info("sql_agent: SQL generated successfully:\n%s", raw)
    return raw