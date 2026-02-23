"""
agents/ask_agent.py
───────────────────
Orchestrates the full analytics Q&A flow for /api/admin/analytics/ask.

Flow:
    User question
        │
        ├─► detect_export_format()     — scan for "excel" / "pdf" / "csv" keywords
        │
        ├─► sql_agent.generate_sql()   — NL → T-SQL (or None if unanswerable)
        │       │
        │       ├─ SQL found  → sqlhelper.run_query() → targeted rows
        │       └─ SQL None   → _build_full_context() → broad RAG fallback
        │
        ├─► OpenAI.chat.completions    — rows/context + question → answer
        │
        ├─► if export requested:
        │       mcp_export_client      — spawns Analytics_Export_Service MCP server
        │           → creates file on disk (xlsx / pdf / csv)
        │           → returns file_path
        │
        └─► AskResult  — returned to main.py (pure data, no HTTP concerns)

main.py only needs:
    from agents import AskAgent, AskResult

    agent  = AskAgent()
    result = await agent.ask(question)
    return {"question": result.question, "answer": result.answer}
"""

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from openai import OpenAI

import sqlhelper
from .sql_agent import generate_sql
from .mcp_export_client import export_analytics, detect_export_format

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# System prompt — loaded from .md file at startup
# ─────────────────────────────────────────────────────────────────────────────
def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "ask_agent_system_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"ask_agent system prompt not found at: {prompt_path}\n"
            "Place ask_agent_system_prompt.md alongside ask_agent.py"
        )
    content = prompt_path.read_text(encoding="utf-8")
    logger.info("ask_agent: system prompt loaded from %s (%d chars)", prompt_path, len(content))
    return content

_SYSTEM_PROMPT = _load_system_prompt()


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass — plain data out of the agent, no HTTP / FastAPI coupling
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class AskResult:
    question:      str
    answer:        str
    sql:           str | None = None   # the T-SQL that was executed, if any
    rows_fetched:  int        = 0      # how many rows came back from SQL
    used_rag:      bool       = False  # True when broad fallback was used
    export_format: str | None = None   # "excel" | "pdf" | "csv" if export was requested
    export_path:   str | None = None   # absolute path to the created file
    export_size:   int        = 0      # file size in bytes
    error:         str | None = None   # set if something went wrong


# ─────────────────────────────────────────────────────────────────────────────
# AskAgent
# ─────────────────────────────────────────────────────────────────────────────
class AskAgent:
    """
    Stateless analytics agent.  Instantiate once per request (or share across
    requests — it holds no per-request state).
    """

    def __init__(self, openai_client: OpenAI | None = None):
        """
        Args:
            openai_client: Optional pre-built client (useful for testing).
                           If None, builds one from env vars on first use.
        """
        self._client   = openai_client
        self._deployment = os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")

    # ── public entry point ────────────────────────────────────────────────────
    async def ask(self, question: str) -> AskResult:
        """
        Full Q&A pipeline.  Async so it fits naturally into FastAPI endpoints
        without blocking the event loop on I/O-heavy paths.

        Returns an AskResult.  Never raises — errors are captured in result.error
        so main.py can decide how to surface them.
        """
        logger.info("AskAgent.ask: %s", question[:80])

        try:
            client = self._get_client()

            # ── Step 1: detect export intent early ───────────────────────────
            export_format = detect_export_format(question)
            if export_format:
                logger.info("AskAgent: export requested — format=%s", export_format)

            # ── Step 2: SQL agent → context ───────────────────────────────────
            sql, rows, analytics_context, used_rag = self._build_context(question)

            # ── Step 3: LLM answer ────────────────────────────────────────────
            answer = self._call_llm(client, question, analytics_context, export_format)

            # ── Step 4: export via MCP (only if requested) ────────────────────
            export_path: str | None = None
            export_size: int        = 0

            if export_format:
                logger.info("AskAgent: calling MCP export server (format=%s, rows=%d)",
                            export_format, len(rows))
                export_result = await export_analytics(
                    format   = export_format,
                    question = question,
                    answer   = answer,
                    rows     = rows,
                )
                if export_result.get("status") == "success":
                    export_path = export_result.get("download_url")   # blob SAS URL
                    export_size = export_result.get("file_size_bytes", 0)
                    logger.info("AskAgent: export uploaded to blob → %s (%d bytes)",
                                export_path, export_size)
                else:
                    # Export failed — still return the answer, just log the issue
                    logger.error("AskAgent: export failed — %s", export_result.get("message"))

            return AskResult(
                question      = question,
                answer        = answer,
                sql           = sql,
                rows_fetched  = len(rows),
                used_rag      = used_rag,
                export_format = export_format,
                export_path   = export_path,
                export_size   = export_size,
            )

        except Exception as e:
            logger.error("AskAgent.ask failed: %s", e, exc_info=True)
            return AskResult(
                question = question,
                answer   = "",
                error    = str(e),
            )

    # ── private helpers ───────────────────────────────────────────────────────
    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key      = os.getenv("AZURE_OPENAI_KEY", ""),                
                base_url = os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            )
            logger.info("AskAgent: OpenAI client initialised (deployment=%s)", self._deployment)
        return self._client

    def _build_context(
        self,
        question: str,
    ) -> tuple[str | None, list, str, bool]:
        """
        Returns (sql, rows, analytics_context, used_rag).

        Tries sql_agent first for a targeted query.
        Falls back to full data dump if sql_agent returns None.
        """
        # ── try targeted SQL ──────────────────────────────────────────────────
        sql  = generate_sql(question)
        rows = []

        if sql:
            try:
                rows = sqlhelper.run_query(sql)
                logger.info("AskAgent: targeted query returned %d rows", len(rows))
                context = self._format_targeted_context(question, sql, rows)
                return sql, rows, context, False
            except Exception as query_err:
                logger.warning(
                    "AskAgent: targeted query failed (%s) — falling back to RAG", query_err
                )

        # ── fallback: broad context ───────────────────────────────────────────
        logger.info("AskAgent: using full RAG context")
        context = self._build_full_context()
        return None, [], context, True

    def _format_targeted_context(self, question: str, sql: str, rows: list) -> str:
        """Format SQL result rows into a concise LLM-ready context block."""
        if not rows:
            rows_text = "  (no results)"
        else:
            lines = [f"  Row {i}: {', '.join(str(v) for v in row)}"
                     for i, row in enumerate(rows[:50], 1)]
            if len(rows) > 50:
                lines.append(f"  ... and {len(rows) - 50} more rows (truncated)")
            rows_text = "\n".join(lines)

        return (
            f'TARGETED QUERY RESULT for: "{question}"\n\n'
            f"SQL executed:\n{sql}\n\n"
            f"Results ({len(rows)} rows):\n{rows_text}"
        )

    def _build_full_context(self) -> str:
        """
        Broad data fetch — fallback for open-ended questions that can't be
        answered with a single targeted SQL query.
        """
        overview            = sqlhelper.get_analytics_overview()
        approval_metrics    = sqlhelper.get_approval_metrics()
        diversity_metrics   = sqlhelper.get_diversity_metrics()
        department_spending = sqlhelper.get_department_spending()
        top_recipients      = sqlhelper.get_top_recipients(limit=5)
        top_nominators      = sqlhelper.get_top_nominators(limit=5)

        ctx = (
            "AWARD NOMINATION ANALYTICS DATA:\n\n"
            "Overview:\n"
            f"- Total Nominations: {overview.get('totalNominations', 0)}\n"
            f"- Total Amount Spent: ${overview.get('totalAmount', 0):,}\n"
            f"- Approved: {overview.get('approvedCount', 0)}\n"
            f"- Pending: {overview.get('pendingCount', 0)}\n"
            f"- Average Award: ${overview.get('avgAmount', 0):.2f}\n"
            f"- Rejection Rate: {overview.get('rejectionRate', 0) * 100:.1f}%\n\n"
            "Approval Metrics:\n"
            f"- Approved: {approval_metrics.get('approvedCount', 0)}\n"
            f"- Rejected: {approval_metrics.get('rejectedCount', 0)}\n"
            f"- Avg Days to Approval: {approval_metrics.get('avgDaysToApproval', 0):.1f}\n"
            f"- Approval Rate: {approval_metrics.get('approvalRate', 0) * 100:.1f}%\n\n"
            "Diversity Metrics:\n"
            f"- Unique Recipients: {diversity_metrics.get('uniqueRecipients', 0)}\n"
            f"- Gini Coefficient: {diversity_metrics.get('giniCoefficient', 0):.3f}\n"
            f"- Top Recipient Share: {diversity_metrics.get('topRecipientPercent', 0):.1f}%\n\n"
            "Top Recipients:"
        )
        for r in top_recipients:
            ctx += f"\n- {r[1]} {r[2]}: {r[3]} awards, ${r[4]:,}"

        ctx += "\n\nTop Nominators:"
        for n in top_nominators:
            ctx += f"\n- {n[1]} {n[2]}: {n[3]} nominations, ${n[4]:,}"

        ctx += "\n\nDepartment Breakdown:"
        for dept in department_spending:
            ctx += f"\n- {dept[0]}: {dept[1]} awards, ${dept[2]:,} total, ${dept[3]:.0f} avg"

        return ctx

    def _call_llm(self, client: OpenAI, question: str, context: str,
                  export_format: str | None = None) -> str:
        """Send context + question to the LLM and return the answer string."""
        system_prompt = _SYSTEM_PROMPT

        if export_format:
            # Tell the LLM the file is being created separately — don't dump raw data
            system_prompt += (
                f"\n\nIMPORTANT: The user has requested a {export_format.upper()} export. "
                f"A {export_format.upper()} file is being generated automatically with the full data. "
                "Do NOT reproduce the raw data or generate file content in your response. "
                "Instead, briefly confirm what data will be in the export file and provide any useful insights or summary."
            )

        user_prompt = f"{context}\n\nQuestion: {question}\n\nProvide a detailed, data-driven response."

        logger.debug("AskAgent: calling LLM (deployment=%s)", self._deployment)

        try:
            response = client.chat.completions.create(
                model    = self._deployment,
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = 0.7,
                max_tokens  = 1000,
            )
        except Exception as api_err:
            error_str = str(api_err)
            if "404" in error_str or "not found" in error_str.lower():
                logger.error(
                    "AskAgent: deployment '%s' not found — check AZURE_OPENAI_MODEL env var",
                    self._deployment,
                )
            raise

        content = response.choices[0].message.content
        if content is None:
            logger.warning("AskAgent: LLM returned None content")
            raise ValueError("LLM returned an empty response")

        return content.strip()