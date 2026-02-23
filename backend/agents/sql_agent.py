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
from pathlib import Path
from openai import OpenAI

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Reuse the same env vars already in main.py
# ─────────────────────────────────────────────
_client: OpenAI | None = None

def _get_client() -> OpenAI:
    """Lazy singleton — reuses main.py's Azure OpenAI env vars."""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.getenv("AZURE_OPENAI_KEY", ""),
            base_url=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        )
        logger.info("sql_agent: OpenAI client initialised")
    return _client


# ─────────────────────────────────────────────
# System prompt — loaded from .md file at startup
# Override location with SQL_AGENT_PROMPT_PATH env var
# ─────────────────────────────────────────────
def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "sql_agent_system_prompt.md"    
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"sql_agent system prompt not found at: {prompt_path}\n"
            "Set SQL_AGENT_PROMPT_PATH env var or place sql_agent_system_prompt.md "
            "alongside sql_agent.py"
        )
    content = prompt_path.read_text(encoding="utf-8")
    logger.info("sql_agent: system prompt loaded from %s (%d chars)", prompt_path, len(content))
    return content

_SCHEMA_CONTEXT = _load_system_prompt()

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
def generate_sql(question: str, model: str | None = None) -> str | None:
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
    client     = _get_client()

    logger.info("sql_agent: generating SQL for question: %s", question[:80])

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

    raw = response.choices[0].message.content.strip()

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