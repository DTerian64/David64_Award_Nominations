"""
agents/ask_agent.py
───────────────────
Analytics Q&A agent powered by an OpenAI tool-calling loop.

The LLM decides which tools to call, in what order, and with what arguments.
Python executes the tools and feeds results back — no keyword detection,
no hardcoded pipeline, no MCP subprocess.

Flow (determined by the LLM at runtime, not by Python):
    ┌─────────────────────────────────────────────────────┐
    │  User question                                      │
    │       │                                             │
    │       ▼                                             │
    │  LLM + TOOLS (loop)                                 │
    │    ├─ call query_database(question)    → rows       │
    │    ├─ call get_analytics_overview()   → summary     │
    │    ├─ call export_to_excel(...)       → SAS URL     │
    │    ├─ call export_to_pdf(...)         → SAS URL     │
    │    ├─ call export_to_csv(...)         → SAS URL     │
    │    └─ final text answer (no tool call)              │
    │                                                     │
    └─► AskResult  (returned to main.py)                  │

main.py contract (unchanged):
    from agents import AskAgent, AskResult

    agent  = AskAgent()
    result = await agent.ask(question)
    return {"question": result.question, "answer": result.answer}
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
    ChatCompletionToolParam,
)

from .tools import TOOLS, dispatch

logger = logging.getLogger(__name__)

# Max tool-call iterations per request — prevents runaway loops
_MAX_ITERATIONS = 10


# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────
def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "ask_agent_system_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"ask_agent system prompt not found at: {prompt_path}\n"
            "Place ask_agent_system_prompt.md alongside ask_agent.py"
        )
    content = prompt_path.read_text(encoding="utf-8")
    logger.info("ask_agent: system prompt loaded (%d chars)", len(content))
    return content

_SYSTEM_PROMPT = _load_system_prompt()


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass  —  pure data, no HTTP / FastAPI coupling
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolCall:
    """Record of a single tool invocation during the agent loop."""
    name:   str
    args:   dict
    result: dict


@dataclass
class AskResult:
    question:      str
    answer:        str
    tool_calls:    list[ToolCall] = field(default_factory=list)
    # Convenience accessors derived from tool_calls
    sql:           str | None = None   # last SQL executed, if any
    rows_fetched:  int        = 0      # row count from last query_database call    
    export_format: str | None = None   # "excel" | "pdf" | "csv" if exported
    export_path:   str | None = None   # SAS download URL
    export_size:   int        = 0      # bytes
    error:         str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# AskAgent
# ─────────────────────────────────────────────────────────────────────────────
class AskAgent:
    """
    Stateless analytics agent built on an OpenAI tool-calling loop.

    The LLM decides which tools to use — Python just executes them and
    feeds results back into the conversation until the model stops calling tools.
    """

    def __init__(self, openai_client: AzureOpenAI | None = None):
        self._client     = openai_client
        self._deployment = os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")

    # ── public entry point ────────────────────────────────────────────────────
    async def ask(
        self,
        question:     str,
        tenant_id:    int  = 0,
        current_user: dict | None = None,
    ) -> AskResult:
        """
        Run the full tool-calling agent loop for a question.

        tenant_id     — caller's internal TenantId; injected into the system
                        prompt and enforced at the SQL execution layer.
        current_user  — dict from get_current_user_with_impersonation containing
                        at minimum UserId, FirstName, LastName, Title.
                        Injected into the system prompt so the LLM can resolve
                        first-person references ("my nominations", "my fraud score").
        Never raises — errors are captured in AskResult.error.
        """
        logger.info(
            "AskAgent.ask: %s (tenant_id=%d, user_id=%s)",
            question[:80],
            tenant_id,
            current_user.get("UserId") if current_user else "unknown",
        )

        try:
            client   = self._get_client()
            messages: list[ChatCompletionMessageParam] = self._build_initial_messages(
                question, tenant_id, current_user
            )

            tool_calls_log: list[ToolCall] = []

            # ── Tool-calling loop ─────────────────────────────────────────────
            for iteration in range(_MAX_ITERATIONS):
                logger.debug("AskAgent: loop iteration %d", iteration + 1)

                response = client.chat.completions.create(
                    model       = self._deployment,
                    messages    = messages,
                    tools       = TOOLS,
                    tool_choice = "auto",
                    parallel_tool_calls  = False,
                    temperature = 0.7,
                    max_tokens  = 2000,
                )

                msg: ChatCompletionMessage = response.choices[0].message

                # Append assistant turn to history                
                messages.append(cast(ChatCompletionMessageParam, msg.model_dump(exclude_unset=True)))

                # ── No tool calls → final answer ──────────────────────────────
                if not msg.tool_calls:
                    answer = (msg.content or "").strip()
                    if not answer:
                        raise ValueError("LLM returned an empty response")

                    logger.info(
                        "AskAgent: finished in %d iteration(s), %d tool call(s)",
                        iteration + 1, len(tool_calls_log)
                    )
                    return self._build_result(question, answer, tool_calls_log)

                # ── Execute each requested tool ───────────────────────────────
                for tc in msg.tool_calls:
                    if not isinstance(tc, ChatCompletionMessageToolCall):
                        logger.warning("AskAgent: skipping unknown tool call type: %s", type(tc))
                        continue
                    tool_name = tc.function.name
                    tool_args = json.loads(tc.function.arguments or "{}")

                    logger.info("AskAgent: tool_call → %s(%s)", tool_name,
                                ", ".join(f"{k}=..." for k in tool_args))

                    result_json = await dispatch(tool_name, tool_args, tenant_id)
                    result_dict = json.loads(result_json)

                    tool_calls_log.append(ToolCall(
                        name   = tool_name,
                        args   = tool_args,
                        result = result_dict,
                    ))

                    # Feed result back into conversation
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc.id,
                        "content":      result_json,
                    })

            # Loop exhausted without a final answer
            logger.warning("AskAgent: hit max iterations (%d)", _MAX_ITERATIONS)
            return AskResult(
                question   = question,
                answer     = "I reached the maximum number of tool calls without a final answer. Please try a more specific question.",
                tool_calls = tool_calls_log,
                error      = "max_iterations_exceeded",
            )

        except Exception as e:
            logger.error("AskAgent.ask failed: %s", e, exc_info=True)
            return AskResult(question=question, answer="", error=str(e))

    # ── private helpers ───────────────────────────────────────────────────────
    def _get_client(self) -> AzureOpenAI:
        if self._client is None:
            # Use AzureOpenAI (not the plain OpenAI client) so the SDK constructs
            # the correct Azure path:
            #   {azure_endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...
            #
            # The plain OpenAI client with base_url simply appends /chat/completions,
            # which gives a 404 against Azure OpenAI APIM.
            #
            # AZURE_OPENAI_ENDPOINT must be the bare resource URL, e.g.:
            #   https://my-resource.openai.azure.com/
            # Do NOT append /openai/v1 — AzureOpenAI handles the full path itself.
            self._client = AzureOpenAI(
                api_key         = os.getenv("AZURE_OPENAI_KEY", ""),
                azure_endpoint  = os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                api_version     = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            )
            logger.info("AskAgent: AzureOpenAI client initialised (deployment=%s)", self._deployment)
        return self._client

    def _build_initial_messages(
        self,
        question:     str,
        tenant_id:    int,
        current_user: dict | None = None,
    ) -> list[ChatCompletionMessageParam]:
        """Construct the opening system + user messages, injecting tenant and current-user context."""

        tenant_context = (
            f"\n\n## Tenant Context\n"
            f"You are operating on behalf of **TenantId = {tenant_id}**.\n"
            f"Every SQL query you generate MUST include a TenantId filter (Rule 9). "
            f"The server will reject queries that are missing this filter."
        )

        if current_user:
            first   = current_user.get("FirstName", "")
            last    = current_user.get("LastName",  "")
            user_id = current_user.get("UserId",    "")
            title   = current_user.get("Title",     "")
            user_context = (
                f"\n\n## Current User\n"
                f"The person asking this question is currently logged in as:\n"
                f"- **UserId:** {user_id}\n"
                f"- **Name:** {first} {last}\n"
                f"- **Title:** {title}\n"
                f"- **TenantId:** {tenant_id}\n\n"
                f"When the question uses **\"I\"**, **\"me\"**, **\"my\"**, or **\"mine\"**, "
                f"resolve them to **UserId = {user_id}**. "
                f"Never ask the user for their identity — it is already known."
            )
        else:
            user_context = ""

        return [
            ChatCompletionSystemMessageParam(
                role    = "system",
                content = _SYSTEM_PROMPT + tenant_context + user_context,
            ),
            ChatCompletionUserMessageParam(role="user", content=question),
        ]

    def _build_result(
        self,
        question: str,
        answer: str,
        tool_calls_log: list[ToolCall],
    ) -> AskResult:
        """
        Derive convenience fields from the tool call log so callers
        don't have to iterate through tool_calls themselves.
        """
        result = AskResult(
            question   = question,
            answer     = answer,
            tool_calls = tool_calls_log,
        )

        for tc in tool_calls_log:
            if tc.name == "query_database" and tc.result.get("status") == "success":
                result.sql          = tc.result.get("sql")
                result.rows_fetched = tc.result.get("row_count", 0)            

            if tc.name in ("export_to_excel", "export_to_pdf", "export_to_csv"):
                if tc.result.get("status") == "success":
                    result.export_format = tc.name.replace("export_to_", "")
                    result.export_path   = tc.result.get("download_url")
                    result.export_size   = tc.result.get("file_size_bytes", 0)

        return result