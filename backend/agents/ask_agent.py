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
    │    ├─ call get_fraud_model_info()     → model meta  │
    │    ├─ call graph_search_user(...)     → person node │
    │    └─ final text answer (no tool call)              │
    │                                                     │
    └─► AskResult  (returned to main.py)                  │

Skill layout — each skill owns both its prompt context and its tools:

    agents/skills/
      base/         prompt.md            (no tools.py — prompt-only)
      schema/       prompt.md + tools.py (query_database, get_analytics_overview)
      exports/      prompt.md + tools.py (export_to_excel, export_to_pdf, export_to_csv)
      fraud/        prompt.md + tools.py (get_fraud_model_info)
      graph/        prompt.md + tools.py (7 graph traversal tools)

_load_skills() dynamically imports each skill's tools.py (if present) via
importlib, then concatenates SCHEMAS → self._tools and merges IMPLEMENTATIONS
→ self._dispatch.  Adding a new skill requires only dropping files into its
directory — ask_agent.py itself never needs to change.

main.py contract (unchanged):
    from agents import AskAgent, AskResult

    agent  = AskAgent()
    result = await agent.ask(question)
    return {"question": result.question, "answer": result.answer}
"""

import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, cast
from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
    ChatCompletionToolParam,
)

logger = logging.getLogger(__name__)

# Max tool-call iterations per request — prevents runaway loops
_MAX_ITERATIONS = 10

# Skills directory — one subdirectory per skill
_SKILLS_DIR = Path(__file__).parent / "skills"

# Default skill set for the Ask agent (order matters — base always first)
_DEFAULT_SKILLS = ["base", "schema", "exports", "fraud", "graph", "notifications"]


# ─────────────────────────────────────────────────────────────────────────────
# Skill loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_skills(
    skill_names: list[str],
) -> tuple[str, list[dict], dict[str, Callable]]:
    """
    Load skills from agents/skills/<name>/ directories.

    Each skill directory may contain:
      prompt.md  — natural-language instructions appended to the system prompt
      tools.py   — optional; must export SCHEMAS (list[dict]) and
                   IMPLEMENTATIONS (dict[str, async callable])

    Returns:
      prompt      — concatenated system prompt (all skills, separated by ---)
      all_schemas — flat list of OpenAI tool schemas from every skill's tools.py
      all_impls   — merged dict mapping tool name → async callable

    Raises FileNotFoundError if a named skill directory or its prompt.md is
    missing.  A missing tools.py is silently treated as "no tools" so
    prompt-only skills (like base) work without a tools file.
    """
    prompt_sections: list[str] = []
    all_schemas:     list[dict] = []
    all_impls:       dict[str, Callable] = {}

    for name in skill_names:
        skill_dir = _SKILLS_DIR / name
        if not skill_dir.is_dir():
            available = [p.name for p in _SKILLS_DIR.iterdir() if p.is_dir()]
            raise FileNotFoundError(
                f"Skill '{name}' not found at: {skill_dir}\n"
                f"Available skills: {available}"
            )

        # ── Prompt ────────────────────────────────────────────────────────────
        prompt_path = skill_dir / "prompt.md"
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Skill '{name}' is missing prompt.md at: {prompt_path}"
            )
        prompt_sections.append(prompt_path.read_text(encoding="utf-8").strip())

        # ── Tools (optional) ──────────────────────────────────────────────────
        tools_path = skill_dir / "tools.py"
        if tools_path.exists():
            spec   = importlib.util.spec_from_file_location(
                f"agents.skills.{name}.tools", tools_path
            )
            module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
            spec.loader.exec_module(module)                         # type: ignore[union-attr]

            schemas = getattr(module, "SCHEMAS", [])
            impls   = getattr(module, "IMPLEMENTATIONS", {})

            if not isinstance(schemas, list):
                raise TypeError(f"Skill '{name}' tools.py: SCHEMAS must be a list, got {type(schemas)}")
            if not isinstance(impls, dict):
                raise TypeError(f"Skill '{name}' tools.py: IMPLEMENTATIONS must be a dict, got {type(impls)}")

            # Detect duplicate tool names early
            overlap = set(impls) & set(all_impls)
            if overlap:
                raise ValueError(
                    f"Skill '{name}' tools.py defines tool(s) already registered "
                    f"by a previous skill: {sorted(overlap)}"
                )

            all_schemas.extend(schemas)
            all_impls.update(impls)
            logger.debug(
                "ask_agent: skill '%s' registered %d tool(s): %s",
                name, len(impls), list(impls),
            )
        else:
            logger.debug("ask_agent: skill '%s' has no tools.py (prompt-only)", name)

    prompt = "\n\n---\n\n".join(prompt_sections)
    logger.info(
        "ask_agent: loaded %d skill(s) [%s] — %d tools, %d prompt chars",
        len(skill_names), ", ".join(skill_names), len(all_impls), len(prompt),
    )
    return prompt, all_schemas, all_impls


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

    skills — list of skill names to load from agents/skills/<name>/ directories.
             Each skill directory contains prompt.md (required) and optionally
             tools.py (exporting SCHEMAS and IMPLEMENTATIONS).
             Defaults to all skills (base, schema, exports, fraud, graph).
             Pass a subset to create a focused agent with less context,
             e.g. AskAgent(skills=["base", "schema", "graph"]) for a
             graph-only agent that has no fraud or export instructions.
    """

    def __init__(
        self,
        openai_client: AzureOpenAI | None = None,
        skills: list[str] | None = None,
    ):
        self._client     = openai_client
        self._deployment = os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")

        prompt, schemas, impls = _load_skills(skills or _DEFAULT_SKILLS)
        self._system_prompt = prompt
        self._tools:    list[dict]            = schemas
        self._dispatch: dict[str, Any]        = impls

    # ── public entry point ────────────────────────────────────────────────────
    async def ask(
        self,
        question:     str,
        tenant_id:    int  = 0,
        current_user: dict | None = None,
        history:      list[dict] | None = None,
    ) -> AskResult:
        """
        Run the full tool-calling agent loop for a question.

        tenant_id     — caller's internal TenantId; injected into the system
                        prompt and enforced at the SQL execution layer.
        current_user  — dict from get_current_user_with_impersonation containing
                        at minimum UserId, FirstName, LastName, Title.
                        Injected into the system prompt so the LLM can resolve
                        first-person references ("my nominations", "my fraud score").
        history       — prior conversation turns as a list of
                        {"role": "user"|"assistant", "content": str} dicts.
                        Inserted between the system prompt and the new question
                        so the model has context from earlier turns.
                        Tool-call messages are NOT included — only the visible
                        human/assistant pairs.  Callers should cap this at
                        ~10 turns (20 messages) before sending.
        Never raises — errors are captured in AskResult.error.
        """
        logger.info(
            "AskAgent.ask: %s (tenant_id=%d, user_id=%s, history=%d turns)",
            question[:80],
            tenant_id,
            current_user.get("UserId") if current_user else "unknown",
            len(history) if history else 0,
        )

        try:
            client   = self._get_client()
            messages: list[ChatCompletionMessageParam] = self._build_initial_messages(
                question, tenant_id, current_user, history
            )

            tool_calls_log: list[ToolCall] = []

            # ── Tool-calling loop ─────────────────────────────────────────────
            for iteration in range(_MAX_ITERATIONS):
                logger.debug("AskAgent: loop iteration %d", iteration + 1)

                response = client.chat.completions.create(
                    model       = self._deployment,
                    messages    = messages,
                    tools       = self._tools or None,
                    tool_choice = "auto" if self._tools else "none",
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

                    result_json = await self._dispatch_tool(tool_name, tool_args, tenant_id)
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

    async def _dispatch_tool(
        self,
        tool_name: str,
        tool_args: dict,
        tenant_id: int,
    ) -> str:
        """
        Look up tool_name in self._dispatch and call the implementation.

        tenant_id is injected as a keyword argument when the implementation
        accepts it (all skill tools accept tenant_id=0 by default).

        Always returns a JSON string so the OpenAI conversation loop can
        append it directly as a tool-role message.
        """
        impl = self._dispatch.get(tool_name)
        if impl is None:
            logger.error("AskAgent: unknown tool '%s' — no implementation found", tool_name)
            return json.dumps({
                "status":  "error",
                "message": f"Unknown tool: {tool_name}",
            })
        try:
            result = await impl(**tool_args, tenant_id=tenant_id)
            return json.dumps(result, default=str)
        except TypeError:
            # impl doesn't accept tenant_id — call without it
            result = await impl(**tool_args)
            return json.dumps(result, default=str)
        except Exception as err:
            logger.error("AskAgent: tool '%s' raised: %s", tool_name, err, exc_info=True)
            return json.dumps({"status": "error", "message": str(err)})

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
        history:      list[dict] | None = None,
    ) -> list[ChatCompletionMessageParam]:
        """
        Construct the full message list for the LLM:
          [system prompt + tenant/user context]
          [prior user/assistant turns from history]
          [new user question]

        Only user and assistant roles are accepted from history — tool-call
        messages are transient and are never round-tripped through the client.
        """
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

        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(
                role    = "system",
                content = self._system_prompt + tenant_context + user_context,
            ),
        ]

        # Insert prior conversation turns (user/assistant only)
        for turn in (history or []):
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role == "user":
                messages.append(ChatCompletionUserMessageParam(role="user", content=content))
            elif role == "assistant":
                messages.append(cast(ChatCompletionMessageParam, {"role": "assistant", "content": content}))

        # New question
        messages.append(ChatCompletionUserMessageParam(role="user", content=question))
        return messages

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