import os
import json
import asyncio
import logging
import time
from dotenv import load_dotenv
from openai import AzureOpenAI
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from logging_config import setup_logging, get_logger

# ─────────────────────────────────────────────
# Load .env then initialise logging
# (must happen before any other code logs)
# ─────────────────────────────────────────────
load_dotenv()
setup_logging()

logger = get_logger("server")

# ─────────────────────────────────────────────
# Azure OpenAI Client
# ─────────────────────────────────────────────
logger.info("Initialising Azure OpenAI client...")

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT","")
AZURE_API_KEY  = os.getenv("AZURE_OPENAI_KEY","")
AZURE_API_VER  = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-14")
DEPLOYMENT     = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

logger.info("Azure endpoint  : %s", AZURE_ENDPOINT)
logger.info("Deployment name : %s", DEPLOYMENT)
logger.info("API version     : %s", AZURE_API_VER)
logger.info("API key         : %s", "SET ✓" if AZURE_API_KEY else "NOT SET ✗")  # never log the actual key

client = AzureOpenAI(
    azure_endpoint=AZURE_ENDPOINT,
    api_key=AZURE_API_KEY,
    api_version=AZURE_API_VER,
)
logger.info("Azure OpenAI client ready")

# ─────────────────────────────────────────────
# Schema context
# ─────────────────────────────────────────────
SCHEMA_CONTEXT = """
You are a SQL query generator for a Nominations & Recognition system.

DATABASE SCHEMA:
TABLE: dbo.Users
  UserId INT PK, userPrincipalName NVARCHAR(100), FirstName NVARCHAR(50),
  LastName NVARCHAR(50), Title NVARCHAR(100), ManagerId INT FK->Users, userEmail NVARCHAR(100)

TABLE: dbo.Nominations
  NominationId INT IDENTITY PK, NominatorId INT FK->Users, BeneficiaryId INT FK->Users,
  ApproverId INT FK->Users, Status NVARCHAR(20) [Pending|Approved|Rejected|Payed],
  DollarAmount INT, NominationDescription NVARCHAR(500), NominationDate DATE,
  ApprovedDate DATETIME2, PayedDate DATETIME2

RULES:
1. Use aliases: u_nom (nominator), u_ben (beneficiary), u_app (approver).
2. Name lookups: LOWER(u.FirstName + ' ' + u.LastName) LIKE LOWER('%<n>%')
3. Always add meaningful column aliases.
4. Return ONLY valid T-SQL — no markdown, no explanation.
5. Use TOP N for rankings. Use DATEADD/GETDATE() for date ranges.
6. Status values are exact: Pending, Approved, Rejected, Payed.
"""

TEMPLATE_QUERIES = {
    "pending_approvals_by_approver": """SELECT u_app.FirstName + ' ' + u_app.LastName AS ApproverName, COUNT(*) AS PendingCount
FROM dbo.Nominations n JOIN dbo.Users u_app ON n.ApproverId = u_app.UserId
WHERE n.Status = 'Pending'
GROUP BY u_app.UserId, u_app.FirstName, u_app.LastName ORDER BY PendingCount DESC""",

    "top_5_nominators": """SELECT TOP 5 u_nom.FirstName + ' ' + u_nom.LastName AS NominatorName,
COUNT(*) AS TotalNominations, SUM(n.DollarAmount) AS TotalDollars
FROM dbo.Nominations n JOIN dbo.Users u_nom ON n.NominatorId = u_nom.UserId
GROUP BY u_nom.UserId, u_nom.FirstName, u_nom.LastName ORDER BY TotalNominations DESC""",

    "approved_dollars_last_30_days": """SELECT SUM(DollarAmount) AS TotalApprovedDollars, COUNT(*) AS TotalApprovedNominations
FROM dbo.Nominations
WHERE Status = 'Approved' AND ApprovedDate >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))""",

    "status_summary": """SELECT Status, COUNT(*) AS Count, SUM(DollarAmount) AS TotalDollars, AVG(DollarAmount) AS AvgDollarAmount
FROM dbo.Nominations GROUP BY Status ORDER BY Count DESC""",
}


# ─────────────────────────────────────────────
# Helper — log token usage + latency after each AI call
# ─────────────────────────────────────────────
def _log_ai_call(fn_name: str, response, elapsed: float) -> None:
    usage = getattr(response, "usage", None)
    if usage:
        logger.info(
            "[%s] tokens — prompt: %d | completion: %d | total: %d | latency: %.2fs",
            fn_name, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, elapsed,
        )
    else:
        logger.info("[%s] completed in %.2fs (no usage data)", fn_name, elapsed)


# ─────────────────────────────────────────────
# AI helpers
# ─────────────────────────────────────────────
def generate_sql(question: str) -> str:
    log = get_logger("generate_sql")
    log.info("Question: %s", question)
    log.debug("Calling Azure OpenAI (deployment=%s)...", DEPLOYMENT)

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": SCHEMA_CONTEXT},
            {"role": "user", "content": f'Convert to T-SQL:\n\n"{question}"\n\nReturn ONLY the SQL query.'},
        ],
        max_tokens=1024,
        temperature=0,
    )
    elapsed = time.perf_counter() - t0
    _log_ai_call("generate_sql", response, elapsed)

    raw = response.choices[0].message.content.strip()
    log.debug("Raw AI response:\n%s", raw)

    sql = raw.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
    log.info("Generated SQL:\n%s", sql)
    return sql


def classify_query(question: str) -> dict:
    log = get_logger("classify_query")
    log.debug("Classifying: %s", question)

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": "Classify SQL query intents. Respond with JSON only: { category, entities, aggregation }"},
            {"role": "user", "content": (
                f'Classify: "{question}"\n'
                "Categories: pending_approvals|nominations_by_nominator|nominations_by_beneficiary"
                "|dollar_amount_summary|status_summary|top_nominators|top_beneficiaries|approval_metrics|general"
            )},
        ],
        max_tokens=256,
        temperature=0,
        response_format={"type": "json_object"},
    )
    elapsed = time.perf_counter() - t0
    _log_ai_call("classify_query", response, elapsed)

    raw = response.choices[0].message.content.strip()
    log.debug("Classification response: %s", raw)

    try:
        result = json.loads(raw)
        log.info("Intent: %s", result.get("category", "unknown"))
        return result
    except Exception as exc:
        log.warning("Failed to parse classification JSON: %s — falling back to 'general'", exc)
        return {"category": "general", "entities": [], "aggregation": "unknown"}


def validate_sql(question: str, sql: str) -> dict:
    log = get_logger("validate_sql")
    log.info("Validating SQL for: %s", question)
    log.debug("SQL:\n%s", sql)

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": SCHEMA_CONTEXT},
            {"role": "user", "content": (
                f'Does this SQL correctly answer the question?\n\n'
                f'QUESTION: "{question}"\n\nSQL:\n{sql}\n\n'
                'Respond with JSON: { "valid": bool, "issues": [], "suggestions": [] }'
            )},
        ],
        max_tokens=512,
        temperature=0,
        response_format={"type": "json_object"},
    )
    elapsed = time.perf_counter() - t0
    _log_ai_call("validate_sql", response, elapsed)

    raw = response.choices[0].message.content.strip()
    log.debug("Validation response: %s", raw)

    try:
        result = json.loads(raw)
        log.info("Valid: %s | Issues: %d", result.get("valid"), len(result.get("issues", [])))
        return result
    except Exception as exc:
        log.error("Failed to parse validation JSON: %s", exc)
        return {"valid": None, "issues": ["Could not parse validation response"], "suggestions": []}


# ─────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────
app     = Server("nominations-sql-agent")
mcp_log = get_logger("mcp")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    mcp_log.debug("list_tools called")
    return [
        types.Tool(
            name="generate_sql",
            description="Convert a natural language question about nominations into a T-SQL query",
            inputSchema={
                "type": "object",
                "properties": {"question": {"type": "string", "description": "Natural language question"}},
                "required": ["question"],
            },
        ),
        types.Tool(
            name="generate_sql_batch",
            description="Convert multiple natural language questions into T-SQL queries",
            inputSchema={
                "type": "object",
                "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
                "required": ["questions"],
            },
        ),
        types.Tool(
            name="get_template_queries",
            description="Return pre-built SQL templates for common Nominations queries",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="validate_sql_intent",
            description="Validate that a SQL query matches the intended question",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "sql":      {"type": "string"},
                },
                "required": ["question", "sql"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    mcp_log.info("▶ Tool called: %s", name)
    mcp_log.debug("Arguments: %s", json.dumps(arguments))
    t0 = time.perf_counter()

    try:
        if name == "generate_sql":
            question = arguments["question"]
            try:
                sql    = generate_sql(question)
                intent = classify_query(question)
                result = {"success": True, "question": question, "sql": sql, "intent": intent, "provider": "azure-openai"}
            except Exception as e:
                mcp_log.error("generate_sql failed: %s", e, exc_info=True)
                result = {"success": False, "error": str(e)}

        elif name == "generate_sql_batch":
            questions = arguments["questions"]
            mcp_log.info("Batch size: %d questions", len(questions))
            results = []
            for i, question in enumerate(questions, 1):
                mcp_log.info("Batch item %d/%d: %s", i, len(questions), question)
                try:
                    results.append({"question": question, "sql": generate_sql(question), "success": True})
                except Exception as e:
                    mcp_log.error("Batch item %d failed: %s", i, e, exc_info=True)
                    results.append({"question": question, "error": str(e), "success": False})
            result = {"results": results}

        elif name == "get_template_queries":
            mcp_log.info("Returning %d templates", len(TEMPLATE_QUERIES))
            result = {"templates": TEMPLATE_QUERIES}

        elif name == "validate_sql_intent":
            result = validate_sql(arguments["question"], arguments["sql"])

        else:
            mcp_log.warning("Unknown tool: %s", name)
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        mcp_log.critical("Unhandled exception in call_tool(%s): %s", name, e, exc_info=True)
        result = {"success": False, "error": f"Internal server error: {str(e)}"}

    elapsed = time.perf_counter() - t0
    mcp_log.info("◀ Tool %s finished in %.2fs", name, elapsed)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
async def main():
    logger.info("MCP server ready — listening on stdio")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
    logger.info("MCP server shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
