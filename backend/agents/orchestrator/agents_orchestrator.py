"""
agents/orchestrator/agents_orchestrator.py
──────────────────────────────────────────
Multi-agent fraud investigation orchestrator powered by Azure OpenAI.

Architecture
────────────
  ┌──────────────────────────────────────────────────────────────────┐
  │  AgentsOrchestrator  (GPT-4.1 — planning & synthesis)            │
  │        │                                                         │
  │        ├─ call_fraud_analyst ──► FraudAnalystAgent  [sub-agent]  │
  │        │                         AskAgent(schema+fraud+graph)    │
  │        │                         SQL, fraud scores, graph        │
  │        │                                                         │
  │        ├─ call_export_agent  ──► ExportAgent        [sub-agent]  │
  │        │                         AskAgent(schema+exports)        │
  │        │                         SQL + file build                │
  │        │                                                         │
  │        └─ call_notification_agent ► NotificationAgent [sub-agent] │
  │                                  AskAgent(base+notifications)    │
  │                                  send_email, add_to_calendar     │
  └──────────────────────────────────────────────────────────────────┘

All tiers use Azure OpenAI — same deployment, same billing, no extra keys.

Tool-calling loop (OpenAI pattern — same as AskAgent)
─────────────────────────────────────────────────────
  • LLM returns tool_calls  → dispatch to sub-agent, feed results back
  • LLM returns no tool_calls → extract text content as final answer

Usage
─────
  orchestrator = AgentsOrchestrator()
  result = await orchestrator.investigate(
      "Find CRITICAL fraud nominations involving Dana Taylor and export to Excel",
      tenant_id=1,
  )
  print(result.answer)
  if result.export_url:
      print("Download:", result.export_url)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionSystemMessageParam
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall

from agents.ask_agent import AskAgent, AskResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompt.md"

# ── Orchestrator tool schemas (OpenAI function-calling format) ────────────────
# These are the delegation tools the orchestrator uses to hand off work to
# sub-agents.  The sub-agents' own tools (query_database, export_to_excel, etc.)
# are invisible to the orchestrator.

_ORCHESTRATOR_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "call_fraud_analyst",
            "description": (
                "Delegate a data-gathering or analysis task to the Fraud Analyst sub-agent. "
                "The analyst queries the SQL database, runs fraud model scores, and traverses "
                "the nomination graph (nominator → beneficiary → approver relationships). "
                "Always call this first — before call_export_agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The specific question for the fraud analyst. Be precise — "
                            "include names, date ranges, risk levels, or any filters mentioned "
                            "by the user.  Include TenantId from the Tenant Context."
                        ),
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_export_agent",
            "description": (
                "Delegate a file-export task to the Export sub-agent. "
                "Use only when the user explicitly requests an Excel, PDF, or CSV file. "
                "Always call call_fraud_analyst first so you can pass its findings here. "
                "The export agent needs context about what data to query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The export request.  Include: what data to export, the desired "
                            "format (Excel / PDF / CSV), and a summary of the analyst's findings "
                            "so the export agent knows what SQL to run."
                        ),
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_notification_agent",
            "description": (
                "Delegate email and calendar tasks to the Notification sub-agent. "
                "Use when the user asks to send an email, notify a colleague, or schedule "
                "a follow-up calendar event. "
                "Always call call_fraud_analyst first if you need data to include in the notification. "
                "Pass the analyst's findings in your question so the agent can compose "
                "a meaningful email body or calendar description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The notification task. Include: who to notify (email address), "
                            "what action to take (send email / add to calendar), and a summary "
                            "of the findings to include. For calendar: include the desired date/time."
                        ),
                    }
                },
                "required": ["question"],
            },
        },
    },
]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """Full result from an AgentsOrchestrator.investigate() call."""
    question:   str
    answer:     str                        # Orchestrator's synthesised answer
    analyst:    AskResult | None = None    # Fraud analyst sub-agent result
    export:     AskResult | None = None    # Export sub-agent result
    export_url: str | None = None          # Download URL (if exported)
    iterations: int = 0                    # Orchestrator loop count
    error:      str | None = None


# ── AgentsOrchestrator ────────────────────────────────────────────────────────

class AgentsOrchestrator:
    """
    Two-tier fraud investigation agent built entirely on Azure OpenAI.

    Tier 1 — Orchestrator (GPT-4.1): plans and delegates via tool calls.
    Tier 2 — Sub-agents (AskAgent): execute with their own focused tool sets.

    The orchestrator never queries the database or builds files itself.
    It coordinates sub-agents and synthesises their results into a final answer.
    """

    _MAX_ITERATIONS = 8

    def __init__(self) -> None:
        self._client: AzureOpenAI | None = None   # lazy-initialised on first call
        self._deployment = os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()

        # Sub-agents with focused skill sets — each sees only the tools it needs.
        self._fraud_analyst      = AskAgent(skills=["base", "schema", "fraud", "graph", "integrity"])
        self._export_agent       = AskAgent(skills=["base", "schema", "exports", "integrity"])
        self._notification_agent = AskAgent(skills=["base", "notifications"])

        logger.info(
            "AgentsOrchestrator: ready (deployment=%s, sub-agents=fraud_analyst+export+notification)",
            self._deployment,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def investigate(
        self,
        question:  str,
        tenant_id: int = 0,
        history:   list[dict] | None = None,
    ) -> OrchestratorResult:
        """
        Run a coordinated fraud investigation.

        The orchestrator decides whether to call the fraud analyst, the export
        agent, or the notification agent — and in what order.  It loops until
        it produces a final answer (no tool calls) or hits _MAX_ITERATIONS.

        history — prior user/assistant turns from the same conversation,
                  passed so follow-up questions work without repeating context.
                  Same format as AskAgent: [{"role": "user"|"assistant", "content": str}, ...]

        Never raises — errors are captured in OrchestratorResult.error.
        """
        logger.info(
            "AgentsOrchestrator.investigate: '%s' (tenant_id=%d, history=%d turns)",
            question[:80], tenant_id, len(history) // 2 if history else 0,
        )
        try:
            return await self._run_loop(question, tenant_id, history)
        except Exception as exc:
            logger.error("AgentsOrchestrator.investigate failed: %s", exc, exc_info=True)
            return OrchestratorResult(
                question=question,
                answer="",
                error=str(exc),
            )

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(
        self,
        question:  str,
        tenant_id: int,
        history:   list[dict] | None = None,
    ) -> OrchestratorResult:
        result = OrchestratorResult(question=question, answer="")
        client = self._get_client()

        # Inject tenant context so the orchestrator passes it to sub-agents.
        system = (
            self._system_prompt
            + f"\n\n## Tenant Context\nTenantId = {tenant_id}. "
            "Include this in every question you pass to sub-agents."
        )

        # Build message list: system → prior history → new question.
        # History gives the orchestrator context from the conversation so it
        # doesn't ask the user to repeat names/IDs it already knows.
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=system),
        ]

        for turn in (history or []):
            role    = turn.get("role", "")
            content = turn.get("content", "")
            if role in ("user", "assistant"):
                messages.append(cast(ChatCompletionMessageParam, {"role": role, "content": content}))

        messages.append({"role": "user", "content": question})

        for iteration in range(self._MAX_ITERATIONS):
            result.iterations = iteration + 1
            logger.debug("AgentsOrchestrator: loop iteration %d", iteration + 1)

            response = client.chat.completions.create(
                model       = self._deployment,
                messages    = messages,
                tools       = _ORCHESTRATOR_TOOLS,
                tool_choice = "auto",
                parallel_tool_calls = False,
                temperature = 0.3,    # lower temp = more deterministic planning
                max_tokens  = 2048,
            )

            msg = response.choices[0].message

            # Append assistant turn to history
            messages.append(cast(ChatCompletionMessageParam, msg.model_dump(exclude_unset=True)))

            # ── No tool calls → orchestrator is done, extract final answer ─────
            if not msg.tool_calls:
                result.answer = (msg.content or "").strip()
                logger.info(
                    "AgentsOrchestrator: done in %d iteration(s)", iteration + 1
                )
                return result

            # ── Execute each delegation tool call ─────────────────────────────
            for tc in msg.tool_calls:
                if not isinstance(tc, ChatCompletionMessageToolCall):
                    continue

                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments or "{}")
                sub_question: str = tool_args.get("question", "")

                tool_result = await self._dispatch_delegation(
                    tool_name=tool_name,
                    sub_question=sub_question,
                    result=result,
                    tenant_id=tenant_id,
                )

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      tool_result,
                })

        # Loop exhausted without a final answer
        logger.warning("AgentsOrchestrator: hit max iterations (%d)", self._MAX_ITERATIONS)
        result.answer = (
            "The investigation reached the maximum planning steps without a final answer. "
            "Try breaking the question into smaller parts."
        )
        result.error = "max_iterations_exceeded"
        return result

    async def _dispatch_delegation(
        self,
        tool_name:    str,
        sub_question: str,
        result:       OrchestratorResult,
        tenant_id:    int,
    ) -> str:
        """Route a tool call to the correct sub-agent and return a summary string."""

        if tool_name == "call_fraud_analyst":
            logger.info("AgentsOrchestrator: → fraud_analyst: '%s'", sub_question[:60])
            sub: AskResult = await self._fraud_analyst.ask(
                sub_question, tenant_id=tenant_id
            )
            result.analyst = sub
            logger.info(
                "AgentsOrchestrator: ← fraud_analyst returned %d rows", sub.rows_fetched
            )
            return (
                f"Analyst answer: {sub.answer}\n"
                f"Rows fetched: {sub.rows_fetched}\n"
                f"SQL used: {sub.sql or 'n/a'}\n"
                f"Error: {sub.error or 'none'}"
            )

        if tool_name == "call_export_agent":
            logger.info("AgentsOrchestrator: → export_agent: '%s'", sub_question[:60])
            sub = await self._export_agent.ask(
                sub_question, tenant_id=tenant_id
            )
            result.export     = sub
            result.export_url = sub.export_path
            logger.info(
                "AgentsOrchestrator: ← export_agent: format=%s url=%s",
                sub.export_format, sub.export_path,
            )
            return (
                f"Export complete.\n"
                f"Format: {sub.export_format or 'unknown'}\n"
                f"Download URL: {sub.export_path or 'unavailable'}\n"
                f"Size: {sub.export_size} bytes\n"
                f"Error: {sub.error or 'none'}"
            )

        if tool_name == "call_notification_agent":
            logger.info("AgentsOrchestrator: → notification_agent: '%s'", sub_question[:60])
            sub = await self._notification_agent.ask(
                sub_question, tenant_id=tenant_id
            )
            logger.info("AgentsOrchestrator: ← notification_agent done")
            return f"Notification result: {sub.answer}\nError: {sub.error or 'none'}"

        logger.warning("AgentsOrchestrator: unknown tool '%s'", tool_name)
        return f"Unknown tool: {tool_name}"

    def _get_client(self) -> AzureOpenAI:
        if self._client is None:
            self._client = AzureOpenAI(
                api_key        = os.getenv("AZURE_OPENAI_KEY", ""),
                azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                api_version    = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            )
            logger.info(
                "AgentsOrchestrator: AzureOpenAI client initialised (deployment=%s)",
                self._deployment,
            )
        return self._client
