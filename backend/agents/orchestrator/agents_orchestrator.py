"""
agents/orchestrator/agents_orchestrator.py
──────────────────────────────────────────
Multi-agent fraud investigation orchestrator powered by Anthropic Claude.

Architecture
────────────
  ┌──────────────────────────────────────────────────────────────────┐
  │  AgentsOrchestrator  (Anthropic Claude — planning & synthesis)    │
  │        │                                                         │
  │        ├─ call_fraud_analyst ──► FraudAnalystAgent  [sub-agent]  │
  │        │                         AskAgent(schema+fraud+graph)    │
  │        │                         Azure OpenAI — SQL, scores,     │
  │        │                         graph traversal                 │
  │        │                                                         │
  │        ├─ call_export_agent  ──► ExportAgent        [sub-agent]  │
  │        │                         AskAgent(schema+exports)        │
  │        │                         Azure OpenAI — SQL + file build │
  │        │                                                         │
  │        └─ call_notification_agent ► NotificationAgent [sub-agent] │
  │                                  AskAgent(base+notifications)    │
  │                                  send_email, add_to_calendar     │
  └──────────────────────────────────────────────────────────────────┘

Why two model providers?
  The orchestrator uses Anthropic Claude for planning — a deliberate
  demonstration that multi-agent systems can mix LLMs.  Sub-agents use
  your existing Azure OpenAI deployment (GPT-4.1) for execution.

Anthropic SDK vs OpenAI SDK — key differences shown here
  OpenAI:    response.choices[0].message.tool_calls  (list or None)
             tool result role: "tool"
             stop signal: absence of tool_calls

  Anthropic: response.content  (list of TextBlock / ToolUseBlock)
             tool result:  {"type": "tool_result", "tool_use_id": ..., "content": ...}
             stop signal:  response.stop_reason == "end_turn"

Usage
─────
  orchestrator = AgentsOrchestrator()
  result = await orchestrator.investigate(
      "Find CRITICAL fraud nominations involving Dana Taylor and export to Excel",
      tenant_id=1,
  )
  print(result["answer"])
  if result["export_url"]:
      print("Download:", result["export_url"])
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from agents.ask_agent import AskAgent, AskResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompt.md"

# ── Orchestrator tool schemas (Anthropic format) ──────────────────────────────
# These are NOT the sub-agents' own tools — they are the two delegation tools
# the orchestrator uses to hand off work.  The sub-agents' real tools
# (query_database, export_to_excel, etc.) are invisible to the orchestrator.

_ORCHESTRATOR_TOOLS: list[dict] = [
    {
        "name": "call_fraud_analyst",
        "description": (
            "Delegate a data-gathering or analysis task to the Fraud Analyst sub-agent. "
            "The analyst queries the SQL database, runs fraud model scores, and traverses "
            "the nomination graph (nominator → beneficiary → approver relationships). "
            "Always call this first — before call_export_agent."
        ),
        "input_schema": {
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
    {
        "name": "call_export_agent",
        "description": (
            "Delegate a file-export task to the Export sub-agent. "
            "Use only when the user explicitly requests an Excel, PDF, or CSV file. "
            "Always call call_fraud_analyst first so you can pass its findings here. "
            "The export agent needs context about what data to query."
        ),
        "input_schema": {
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
    {
        "name": "call_notification_agent",
        "description": (
            "Delegate email and calendar tasks to the Notification sub-agent. "
            "Use when the user asks to send an email, notify a colleague, or schedule "
            "a follow-up calendar event. "
            "Always call call_fraud_analyst first if you need data to include in the notification. "
            "Pass the analyst's findings in your question so the agent can compose "
            "a meaningful email body or calendar description."
        ),
        "input_schema": {
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
]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """Full result from a AgentsOrchestrator.investigate() call."""
    question:   str
    answer:     str                        # Orchestrator's synthesised answer
    analyst:    AskResult | None = None    # Fraud analyst sub-agent result
    export:     AskResult | None = None    # Export sub-agent result
    export_url: str | None = None          # Download URL (if exported)
    iterations: int = 0                   # Orchestrator loop count
    error:      str | None = None


# ── AgentsOrchestrator ─────────────────────────────────────────────────────────

class AgentsOrchestrator:
    """
    Two-tier fraud investigation agent.

    Tier 1 — Orchestrator (Anthropic Claude): plans and delegates.
    Tier 2 — Sub-agents (AskAgent, Azure OpenAI): execute with tools.

    The orchestrator never queries the database or builds files itself.
    It coordinates sub-agents and synthesises their results.
    """

    # Max orchestrator loop iterations.  Each iteration may delegate to one
    # sub-agent.  Sub-agents have their own _MAX_ITERATIONS internally.
    _MAX_ITERATIONS = 8

    def __init__(self) -> None:
        # Anthropic async client — reads ANTHROPIC_API_KEY from the environment.
        self._client = anthropic.AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )
        self._model = os.getenv("ORCHESTRATOR_MODEL", "claude-sonnet-4-5")
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()

        # Sub-agents are regular AskAgent instances with a focused skill set.
        # Narrowing skills keeps each agent's context lean and its tool menu
        # unambiguous — the analyst has no export tools, the exporter has no
        # graph tools.
        self._fraud_analyst      = AskAgent(skills=["base", "schema", "fraud", "graph"])
        self._export_agent       = AskAgent(skills=["base", "schema", "exports"])
        self._notification_agent = AskAgent(skills=["base", "notifications"])

        logger.info(
            "AgentsOrchestrator: ready (model=%s, sub-agents=fraud_analyst+export)",
            self._model,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def investigate(
        self,
        question:  str,
        tenant_id: int = 0,
    ) -> OrchestratorResult:
        """
        Run a coordinated fraud investigation.

        The orchestrator decides whether to call the fraud analyst, the
        export agent, or both — and in what order.  It loops until it
        produces a final answer or hits _MAX_ITERATIONS.

        Returns an OrchestratorResult with the synthesised answer plus
        the raw sub-agent results for inspection / debugging.
        """
        logger.info(
            "AgentsOrchestrator.investigate: '%s' (tenant_id=%d)",
            question[:80], tenant_id,
        )

        try:
            return await self._run_loop(question, tenant_id)
        except Exception as exc:
            logger.error("AgentsOrchestrator.investigate failed: %s", exc, exc_info=True)
            return OrchestratorResult(
                question=question,
                answer="",
                error=str(exc),
            )

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(self, question: str, tenant_id: int) -> OrchestratorResult:
        result = OrchestratorResult(question=question, answer="")

        # Inject tenant context into the system prompt so the orchestrator
        # passes it correctly when delegating to sub-agents.
        system = (
            self._system_prompt
            + f"\n\n## Tenant Context\nTenantId = {tenant_id}. "
            "Include this in every question you pass to sub-agents."
        )

        # Anthropic's message format — note: no separate system message object;
        # system is a top-level parameter on messages.create().
        messages: list[dict] = [{"role": "user", "content": question}]

        for iteration in range(self._MAX_ITERATIONS):
            result.iterations = iteration + 1
            logger.debug("AgentsOrchestrator: loop iteration %d", iteration + 1)

            # ── Ask the orchestrator what to do next ──────────────────────────
            response = await self._client.messages.create(
                model      = self._model,
                max_tokens = 2048,
                system     = system,
                tools      = _ORCHESTRATOR_TOOLS,
                messages   = messages,
            )

            logger.debug(
                "AgentsOrchestrator: stop_reason=%s, content_blocks=%d",
                response.stop_reason, len(response.content),
            )

            # ── Orchestrator finished planning → extract final answer ──────────
            # Anthropic signals "done" via stop_reason == "end_turn", not by
            # returning an empty tool list (OpenAI convention).
            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                result.answer = final_text.strip()
                logger.info(
                    "AgentsOrchestrator: done in %d iteration(s)", iteration + 1
                )
                return result

            # ── Orchestrator wants to delegate — process tool_use blocks ───────
            # Append the full assistant turn (may mix text + tool_use blocks).
            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue  # TextBlock narration — skip, already in messages

                tool_result_content = await self._dispatch_delegation(
                    block=block,
                    result=result,
                    tenant_id=tenant_id,
                )

                # Anthropic tool result format — different from OpenAI's "tool" role.
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,      # must match the ToolUseBlock id
                    "content":     tool_result_content,
                })

            # Feed all tool results back in a single user turn.
            # Anthropic requires tool results in the "user" role, not "tool".
            messages.append({"role": "user", "content": tool_results})

        # Loop exhausted
        logger.warning("AgentsOrchestrator: hit max iterations (%d)", self._MAX_ITERATIONS)
        result.answer = (
            "The investigation reached the maximum planning steps without a final answer. "
            "Try breaking the question into smaller parts."
        )
        result.error = "max_iterations_exceeded"
        return result

    async def _dispatch_delegation(
        self,
        block:     anthropic.types.ToolUseBlock,
        result:    OrchestratorResult,
        tenant_id: int,
    ) -> str:
        """
        Route a tool_use block to the correct sub-agent and return
        a string summary to feed back to the orchestrator.
        """
        sub_question: str = block.input.get("question", "")

        if block.name == "call_fraud_analyst":
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

        if block.name == "call_export_agent":
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

        if block.name == "call_notification_agent":
            logger.info("AgentsOrchestrator: → notification_agent: '%s'", sub_question[:60])
            sub = await self._notification_agent.ask(
                sub_question, tenant_id=tenant_id
            )
            logger.info("AgentsOrchestrator: ← notification_agent done")
            return f"Notification result: {sub.answer}\nError: {sub.error or 'none'}"

        logger.warning("AgentsOrchestrator: unknown tool '%s'", block.name)
        return f"Unknown tool: {block.name}"
