"""
description_check.py — Pre-ML nomination description quality checks
====================================================================

Runs two checks before the ML fraud model, using per-tenant
thresholds from DescCheckConfig (loaded from dbo.Tenants.desc_check_config).

Check A — Description Alignment and Semantics
    Combines two independently-produced signals into one auditable decision:
    - embedding category alignment between the description and category label;
    - LLM evaluation of coherence, specificity, category fit, and amount.

    An embedding concern is not an automatic rejection.  A corroborating LLM
    category concern routes to HRBP review, while an LLM pass can adjudicate a
    weak embedding match.  If the LLM is disabled or unavailable, a weak
    embedding match routes to HRBP rather than being auto-rejected.  Only an
    incoherent/gibberish LLM result is a hard semantic rejection.

    Check A's LLM evidence requires these env vars:
        AZURE_OPENAI_ENDPOINT      — Azure OpenAI resource endpoint
        AZURE_OPENAI_DEPLOYMENT    — model deployment name (default: gpt-4o-mini)
        AZURE_OPENAI_API_VERSION   — API version  (default: 2024-08-01-preview)
    Authentication uses DefaultAzureCredential (Managed Identity in production,
    az login / env vars for local dev) — no API key required.

Check B — Duplicate Description  (HRBP flag)
    Compares the description against the nominator's own recent descriptions.
    A high cosine similarity triggers a warning flag that is passed through to
    HRBP review rather than causing an outright rejection, because the same
    description legitimately applies when a nominator recognises an entire team
    for the same project.

Public API
----------
    result = check(
        description:          str,
        category_description: str | None,
        nominator_id:         int,
        config:               DescCheckConfig,
        nomination_id:        int | None,
        amount:               float | None,
    )

    result is a CheckResult:
        action   str        "reject" | "flag" | "pass"
        reason   str | None  human-readable explanation (for rejection email /
                             HRBP warning flag), None when action == "pass"
        check    str | None  pipe-separated names of checks that fired,
                             e.g. "category_alignment|duplicate_description"
"""

from __future__ import annotations

import json as _json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from utils import db
from utils.db import DescCheckConfig

logger = logging.getLogger("integrity_check.description_check")

# ── Embed model cache (shared with random_forest_check.py) ───────────────────
# Imported lazily to avoid a hard startup dependency — the sentence-transformers
# package may not be installed in every environment (e.g. unit test runners).

def _get_embed_model(model_name: str):
    """
    Delegate to random_forest_check's per-model cache so we never load the same
    model twice in the same process.
    """
    from . import random_forest_check
    return random_forest_check._get_embed_model(model_name)


# ── LLM client cache ──────────────────────────────────────────────────────────
# AzureOpenAI client is thread-safe and expensive to construct (token provider
# initialises DefaultAzureCredential on first use).  Cache it for the process
# lifetime so every nomination does not re-initialise the credential chain.

_llm_client      = None
_llm_client_lock = threading.Lock()


def _get_llm_client():
    """
    Return a cached AzureOpenAI client, constructing it on first call.
    Returns None if AZURE_OPENAI_ENDPOINT is not set (LLM evidence unavailable).
    """
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    with _llm_client_lock:
        if _llm_client is not None:         # re-check inside lock
            return _llm_client

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            logger.warning(
                "AZURE_OPENAI_ENDPOINT not set — Check A LLM semantic evidence unavailable"
            )
            return None

        try:
            from azure.identity import get_bearer_token_provider
            from openai import AzureOpenAI

            from utils.azure_credential import credential

            token_provider = get_bearer_token_provider(
                credential,
                "https://cognitiveservices.azure.com/.default",
            )
            _llm_client = AzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token_provider=token_provider,
                api_version=os.environ.get(
                    "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
                ),
            )
            logger.info("AzureOpenAI client initialised for Check A LLM semantic evidence")
        except Exception as exc:
            logger.error("Failed to initialise AzureOpenAI client: %s", exc)
            return None

    return _llm_client


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    action: str             # "reject" | "flag" | "pass"
    reason: Optional[str]   # human-readable; None when action == "pass"
    check:  Optional[str]   # pipe-separated check names that fired
    evidence: dict = field(default_factory=dict)


_PASS = CheckResult(action="pass", reason=None, check=None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D normalised vectors."""
    return float(np.dot(a, b))


def _embed(model, texts: list[str]) -> np.ndarray:
    """Encode a list of texts into L2-normalised embeddings."""
    return model.encode(texts, normalize_embeddings=True)


# ── Check A, evidence 1: embedding category alignment ────────────────────────

def _check_embedding_category_alignment(
    description:          str,
    category_description: str,
    config:               DescCheckConfig,
    nomination_id:        Optional[int] = None,
) -> CheckResult:
    """
    Return an HRBP concern when embedding similarity is below the configured
    threshold. Threshold 0.0 effectively disables this evidence source.
    """
    if config.category_alignment_threshold <= 0.0:
        return CheckResult(
            action="pass",
            reason=None,
            check=None,
            evidence={
                "available": False,
                "status": "DISABLED",
                "model": config.embed_model,
                "similarity": None,
                "threshold": config.category_alignment_threshold,
                "outcome": "skipped",
            },
        )

    model   = _get_embed_model(config.embed_model)
    embs    = _embed(model, [description, category_description])
    sim     = _cosine_sim(embs[0], embs[1])

    logger.info(
        "Embedding Category Alignment check",
        extra={
            "nomination_id": nomination_id,
            "sim":           round(float(sim), 4),
            "threshold":     config.category_alignment_threshold,
        },
    )

    if sim < config.category_alignment_threshold:
        reason = (
            f"The description doesn't appear to relate to the "
            f"'{category_description}' category "
            f"(similarity {sim:.2f}, minimum {config.category_alignment_threshold:.2f}). "
            f"Please describe a specific action or behaviour that reflects "
            f"'{category_description}'."
        )
        logger.info(
            "Embedding Category Alignment check concern",
            extra={"nomination_id": nomination_id},
        )
        return CheckResult(
            action="flag",
            reason=reason,
            check="category_alignment",
            evidence={
                "available": True,
                "status": "SUCCEEDED",
                "model": config.embed_model,
                "similarity": round(float(sim), 6),
                "threshold": config.category_alignment_threshold,
                "outcome": "concern",
            },
        )

    return CheckResult(
        action="pass",
        reason=None,
        check=None,
        evidence={
            "available": True,
            "status": "SUCCEEDED",
            "model": config.embed_model,
            "similarity": round(float(sim), 6),
            "threshold": config.category_alignment_threshold,
            "outcome": "pass",
        },
    )


# ── Check B: duplicate description ───────────────────────────────────────────

def _check_duplicate_description(
    description:           str,
    nominator_id:          int,
    config:                DescCheckConfig,
    exclude_nomination_id: int | None = None,
) -> CheckResult:
    """
    Returns "flag" if the description is near-identical to descriptions the
    same nominator has submitted before.  Uses HRBP review rather than
    outright rejection because team nominations legitimately share a description.

    exclude_nomination_id must be the current nomination's ID so the query
    does not compare the nomination against itself.
    """
    prior_descs = db.get_nominator_descriptions(nominator_id, exclude_nomination_id)
    if not prior_descs:
        return CheckResult(
            action="pass",
            reason=None,
            check=None,
            evidence={
                "available": True,
                "status": "SUCCEEDED",
                "prior_description_count": 0,
                "max_similarity": None,
                "threshold": config.duplicate_similarity_threshold,
                "outcome": "pass",
            },
        )

    model    = _get_embed_model(config.embed_model)
    desc_emb = _embed(model, [description])[0]
    prior_embs = _embed(model, prior_descs)

    sims        = [_cosine_sim(desc_emb, e) for e in prior_embs]
    max_sim     = max(sims)
    most_similar_idx = int(np.argmax(sims))

    logger.info(
        "Duplicate Descriptions check",
        extra={
            "nomination_id": exclude_nomination_id,
            "max_sim":       round(float(max_sim), 4),
            "threshold":     config.duplicate_similarity_threshold,
            "nominator_id":  nominator_id,
        },
    )

    if max_sim >= config.duplicate_similarity_threshold:
        snippet = prior_descs[most_similar_idx][:80].replace("\n", " ")
        reason  = (
            f"Description is highly similar to a prior nomination by the same "
            f"nominator (similarity {max_sim:.2f}). "
            f"Most similar previous description: \"{snippet}…\""
        )
        return CheckResult(
            action="flag",
            reason=reason,
            check="duplicate_description",
            evidence={
                "available": True,
                "status": "SUCCEEDED",
                "prior_description_count": len(prior_descs),
                "max_similarity": round(float(max_sim), 6),
                "threshold": config.duplicate_similarity_threshold,
                "matching_description_snippet": snippet,
                "outcome": "concern",
            },
        )

    return CheckResult(
        action="pass",
        reason=None,
        check=None,
        evidence={
            "available": True,
            "status": "SUCCEEDED",
            "prior_description_count": len(prior_descs),
            "max_similarity": round(float(max_sim), 6),
            "threshold": config.duplicate_similarity_threshold,
            "outcome": "pass",
        },
    )


# ── Check A, evidence 2: LLM semantic evaluation ─────────────────────────────

def _build_llm_prompt(
    description:          str,
    category_description: Optional[str],
    amount:               Optional[float],
    config:               DescCheckConfig,
) -> str:
    """
    Build the full prompt sent to the LLM.

    Structure:
      1. Base evaluation criteria (fixed)
      2. Nomination context (description, category, amount)
      3. Tenant-specific instructions (from config.llm_instructions, if set)
      4. Output format instruction

    The tenant instructions block is placed after the base criteria so it can
    explicitly override any default behaviour (e.g. "do not flag Korean-language
    descriptions as low_specificity").
    """
    category_section = (
        f"Award category description:\n{category_description}"
        if category_description
        else "Award category: not specified"
    )
    amount_section = (
        f"Award amount: {amount}"
        if amount is not None
        else "Award amount: not specified"
    )

    prompt = f"""You are evaluating an employee award nomination for quality and integrity.

{category_section}

{amount_section}

Nomination description:
{description}

Evaluate the nomination on the following dimensions and return a JSON object:

- is_coherent (bool): Is the text intelligible, meaningful language? \
Set false only for gibberish, random characters, or entirely nonsensical text.

- is_specific (bool): Does the description mention at least one concrete action, \
outcome, or measurable contribution? Generic praise without specifics = false.

- category_fit_score (float 0–1): How well does the description support the \
selected award category? 1.0 = strong and clear fit. 0.0 = no discernible fit.

- amount_justified (bool): Does the level of detail and significance described \
reasonably support the award amount? When amount is not specified, set true.

- flags (array of strings): Include every applicable flag from this list only: \
"gibberish", "generic_language", "low_specificity", "category_mismatch", \
"amount_disproportionate". Use an empty array if none apply.

- reasoning (string): One concise sentence describing the most significant \
concern found, or "No concerns." if none."""

    if config.llm_instructions:
        prompt += f"""

Organization-specific instructions — these take precedence over the defaults above:
{config.llm_instructions}"""

    prompt += """

Return only a valid JSON object with the exact keys listed above. \
No markdown fences, no extra keys, no explanation outside the JSON."""

    return prompt


def _evaluate_llm_semantics(
    description:          str,
    category_description: Optional[str],
    amount:               Optional[float],
    config:               DescCheckConfig,
    nominator_id:         int = 0,
    nomination_id:        Optional[int] = None,
) -> CheckResult:
    """
    Produce the LLM evidence used by combined Check A.

    Calls Azure OpenAI with a structured prompt and maps the JSON response to
    a CheckResult:
      - is_coherent = false            → "reject"  (gibberish / incoherent)
      - flags present OR fit < thresh  → "flag"    (HRBP review)
      - otherwise                      → "pass"

    Returns an internal ``action='unavailable'`` result when infrastructure or
    an LLM error makes this evidence unavailable. The public combined result
    remains pass/flag/reject and retains the unavailable evidence in its JSON
    contract.
    """
    client = _get_llm_client()
    if client is None:
        logger.warning(
            "Check A LLM Semantic Evaluation unavailable — client not initialized",
            extra={"nomination_id": nomination_id},
        )
        return CheckResult(
            action="unavailable",
            reason="LLM client was not initialized.",
            check="category_alignment",
            evidence={
                "available": False,
                "status": "UNAVAILABLE",
                "deployment": os.environ.get(
                    "AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"
                ),
                "unavailable_reason": "CLIENT_NOT_INITIALIZED",
                "unavailable_detail": None,
                "category_fit_threshold": config.llm_fit_threshold,
                "response": None,
            },
        )

    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    prompt     = _build_llm_prompt(description, category_description, amount, config)

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=400,
        )
        raw    = response.choices[0].message.content
        result = _json.loads(raw)

        logger.debug("Check A LLM semantic evaluation raw response: %s", raw)

    except Exception as exc:
        logger.error(
            "Check A LLM Semantic Evaluation failed — evidence unavailable",
            extra={"nomination_id": nomination_id, "error": str(exc)},
            exc_info=True,
        )
        return CheckResult(
            action="unavailable",
            reason="LLM semantic evaluation failed.",
            check="category_alignment",
            evidence={
                "available": False,
                "status": "UNAVAILABLE",
                "deployment": deployment,
                "unavailable_reason": "EVALUATION_FAILED",
                "unavailable_detail": str(exc),
                "category_fit_threshold": config.llm_fit_threshold,
                "response": None,
            },
        )

    evidence = {
        "available": True,
        "status": "SUCCEEDED",
        "deployment": deployment,
        "unavailable_reason": None,
        "unavailable_detail": None,
        "category_fit_threshold": config.llm_fit_threshold,
        # Preserve the complete structured model response. The prompt and Azure
        # response envelope are deliberately not persisted.
        "response": result,
    }

    # ── Map response to CheckResult ───────────────────────────────────────────

    # is_coherent = false → Check A hard reject
    if not result.get("is_coherent", True):
        reasoning = result.get("reasoning", "").strip()
        reason = (
            "Nomination description appears incoherent or contains gibberish. "
            + (reasoning if reasoning and reasoning != "No concerns." else "")
        ).strip()
        logger.info(
            "Check A LLM Semantic Evaluation: is_coherent=false",
            extra={
                "nomination_id": nomination_id,
                "is_coherent": False,
                "reason": reasoning,
            },
        )
        return CheckResult(
            action="reject",
            reason=reason,
            check="category_alignment",
            evidence={**evidence, "outcome": "incoherent"},
        )

    # Collect semantic flags
    llm_flags  = [str(f) for f in result.get("flags", []) if f]
    fit_score  = float(result.get("category_fit_score", 1.0))
    reasoning  = result.get("reasoning", "").strip()

    below_fit_threshold = (
        category_description is not None
        and fit_score < config.llm_fit_threshold
    )

    if llm_flags or below_fit_threshold:
        parts = []
        if llm_flags:
            parts.append(f"Semantic flags: {', '.join(llm_flags)}")
        if below_fit_threshold:
            parts.append(
                f"Category fit score {fit_score:.2f} "
                f"(threshold {config.llm_fit_threshold:.2f})"
            )
        if reasoning and reasoning != "No concerns.":
            parts.append(reasoning)

        reason = " | ".join(parts)
        logger.info(
            "Check A LLM Semantic Evaluation concern",
            extra={
                "nomination_id": nomination_id,
                "category_fit_score": round(fit_score, 4),
                "threshold": config.llm_fit_threshold,
                "flags": llm_flags,
                "is_specific": result.get("is_specific"),
                "amount_justified": result.get("amount_justified"),
                "reason": reason,
            },
        )
        return CheckResult(
            action="flag",
            reason=reason,
            check="category_alignment",
            evidence={**evidence, "outcome": "concern"},
        )

    logger.info(
        "Check A LLM Semantic Evaluation passed",
        extra={
            "nomination_id": nomination_id,
            "nominator_id": nominator_id,
            "category_fit_score": round(fit_score, 4),
            "threshold": config.llm_fit_threshold,
            "flags": llm_flags,
            "is_specific": result.get("is_specific"),
            "amount_justified": result.get("amount_justified"),
        },
    )
    return CheckResult(
        action="pass",
        reason=None,
        check=None,
        evidence={**evidence, "outcome": "pass"},
    )


def _combine_check_a(
    embedding_result: CheckResult,
    llm_result: Optional[CheckResult],
    *,
    llm_enabled: bool,
) -> tuple[CheckResult, str]:
    """Fuse Check A's independent evidence into one explainable decision.

    The decision rule intentionally avoids averaging incomparable embedding and
    LLM scores. It combines their categorical outcomes instead.
    """
    llm_available = llm_result is not None and llm_result.action != "unavailable"

    if llm_available and llm_result is not None and llm_result.action == "reject":
        return llm_result, "llm_incoherent_reject"

    embedding_concern = embedding_result.action == "flag"
    llm_concern = llm_available and llm_result is not None and llm_result.action == "flag"

    if llm_concern:
        reasons = []
        if embedding_concern and embedding_result.reason:
            reasons.append(embedding_result.reason)
        if llm_result and llm_result.reason:
            reasons.append(llm_result.reason)
        rule = (
            "embedding_and_llm_concern_hrbp"
            if embedding_concern
            else "llm_concern_hrbp"
        )
        return CheckResult(
            action="flag",
            reason=" | ".join(reasons),
            check="category_alignment",
        ), rule

    if embedding_concern and not llm_available:
        availability = "unavailable" if llm_enabled else "disabled"
        reason = (
            f"{embedding_result.reason} | LLM semantic evidence was {availability}; "
            "routed to HRBP review."
        )
        return CheckResult(
            action="flag",
            reason=reason,
            check="category_alignment",
        ), f"embedding_concern_llm_{availability}_hrbp"

    if embedding_concern:
        # The LLM evaluated the full context and found no semantic concern,
        # adjudicating the weak short-label embedding match.
        return _PASS, "llm_cleared_embedding_concern"

    if not llm_available:
        availability = "unavailable" if llm_enabled else "disabled"
        return _PASS, f"embedding_pass_llm_{availability}"

    return _PASS, "all_available_check_a_evidence_passed"


def _not_evaluated(status: str = "NOT_EVALUATED") -> dict:
    return {"available": False, "status": status, "outcome": "skipped"}


def _complete_semantic_result(
    result: CheckResult,
    *,
    embedding_result: CheckResult,
    llm_result: Optional[CheckResult],
    duplicate_result: Optional[CheckResult],
    decision_rule: str,
) -> CheckResult:
    """Return the public result with the complete versioned engine evidence."""
    llm_evidence = (
        llm_result.evidence if llm_result is not None else _not_evaluated()
    )
    duplicate_evidence = (
        duplicate_result.evidence
        if duplicate_result is not None
        else _not_evaluated()
    )
    evidence_statuses = {
        str(embedding_result.evidence.get("status", "NOT_EVALUATED")),
        str(llm_evidence.get("status", "NOT_EVALUATED")),
        str(duplicate_evidence.get("status", "NOT_EVALUATED")),
    }
    status = "PARTIAL" if "UNAVAILABLE" in evidence_statuses else "SUCCEEDED"

    return CheckResult(
        action=result.action,
        reason=result.reason,
        check=result.check,
        evidence={
            "schema_version": 1,
            "engine": "SEMANTIC",
            "available": True,
            "status": status,
            "embedding": embedding_result.evidence or _not_evaluated(),
            "llm": llm_evidence,
            "duplicate_description": duplicate_evidence,
            "combined_decision": {
                "check_a_rule": decision_rule,
                "check_a_action": (
                    "reject"
                    if decision_rule == "llm_incoherent_reject"
                    else "flag"
                    if "concern" in decision_rule and "cleared" not in decision_rule
                    else "pass"
                ),
                "action": result.action,
                "checks": result.check.split("|") if result.check else [],
                "reason": result.reason,
            },
        },
    )


# ── Public API ────────────────────────────────────────────────────────────────

def check(
    description:          str,
    category_description: Optional[str],
    nominator_id:         int,
    config:               DescCheckConfig,
    nomination_id:        Optional[int] = None,
    amount:               Optional[float] = None,
) -> CheckResult:
    """
    Run combined Check A and duplicate Check B, returning the most severe result.

    Check A combines embedding alignment and LLM semantic evidence. Only an
    incoherent LLM result is a hard rejection. Category or quality concerns are
    soft flags routed to HRBP. Check B remains an independent duplicate flag.

    Args:
        description:          The nomination description text.
        category_description: The tenant's category label, or None if the
                              tenant uses no categories.
        nominator_id:         Used to look up the nominator's prior descriptions.
        config:               Per-tenant thresholds from DescCheckConfig.
        nomination_id:        The current nomination's ID — excluded from the
                              duplicate lookup so a nomination is never compared
                              against itself.
        amount:               The award amount — passed to Check A's LLM evidence
                              justification scoring.  None if unknown.

    Returns:
        CheckResult with action "reject", "flag", or "pass".
        When multiple checks flag, their reasons are joined with " | " and
        their check names are joined with "|"
        (e.g. "category_alignment|duplicate_description").
    """
    desc = (description or "").strip()

    if not desc:
        # Empty description — structural failure; should be caught by the API
        # layer, but we defend here too.
        return CheckResult(
            action="reject",
            reason="Nomination description is empty.",
            check="category_alignment",
            evidence={
                "schema_version": 1,
                "engine": "SEMANTIC",
                "available": True,
                "status": "SUCCEEDED",
                "embedding": _not_evaluated(),
                "llm": _not_evaluated(),
                "duplicate_description": _not_evaluated(),
                "combined_decision": {
                    "check_a_rule": "empty_description_reject",
                    "check_a_action": "reject",
                    "action": "reject",
                    "checks": ["category_alignment"],
                    "reason": "Nomination description is empty.",
                },
            },
        )

    # ── Check A: combine embedding and LLM semantic evidence ─────────────────
    embedding_evaluated = bool(
        category_description and config.category_alignment_threshold > 0.0
    )
    embedding_result = (
        _check_embedding_category_alignment(
            desc, category_description, config, nomination_id
        )
        if embedding_evaluated and category_description
        else CheckResult(
            action="pass",
            reason=None,
            check=None,
            evidence={
                "available": False,
                "status": "NOT_APPLICABLE",
                "model": config.embed_model,
                "similarity": None,
                "threshold": config.category_alignment_threshold,
                "outcome": "skipped",
            },
        )
    )

    llm_result: Optional[CheckResult]
    if config.llm_category_check_enabled:
        llm_result = _evaluate_llm_semantics(
            desc,
            category_description,
            amount,
            config,
            nominator_id,
            nomination_id,
        )
    else:
        llm_result = CheckResult(
            action="unavailable",
            reason="LLM semantic evaluation is disabled by tenant configuration.",
            check="category_alignment",
            evidence={
                "available": False,
                "status": "DISABLED",
                "deployment": os.environ.get(
                    "AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"
                ),
                "unavailable_reason": "DISABLED_BY_CONFIGURATION",
                "unavailable_detail": None,
                "category_fit_threshold": config.llm_fit_threshold,
                "response": None,
            },
        )
        logger.info(
            "Check A LLM Semantic Evaluation disabled by tenant configuration",
            extra={"nomination_id": nomination_id},
        )

    result_a, decision_rule = _combine_check_a(
        embedding_result,
        llm_result,
        llm_enabled=config.llm_category_check_enabled,
    )
    embedding_state = (
        "concern" if embedding_result.action == "flag"
        else "pass" if embedding_evaluated
        else "skipped"
    )
    llm_state = (
        "disabled" if not config.llm_category_check_enabled
        else "unavailable" if llm_result.action == "unavailable"
        else "incoherent" if llm_result.action == "reject"
        else "concern" if llm_result.action == "flag"
        else "pass"
    )
    logger.info(
        "Check A combined decision",
        extra={
            "nomination_id": nomination_id,
            "nominator_id": nominator_id,
            "embedding_result": embedding_state,
            "llm_result": llm_state,
            "decision_rule": decision_rule,
            "action": result_a.action,
            "reason": result_a.reason,
        },
    )
    if result_a.action == "reject":
        return _complete_semantic_result(
            result_a,
            embedding_result=embedding_result,
            llm_result=llm_result,
            duplicate_result=None,
            decision_rule=decision_rule,
        )

    # ── Accumulate soft flags from A and B ────────────────────────────────────
    accumulated: list[CheckResult] = []
    if result_a.action == "flag":
        accumulated.append(result_a)

    # ── Check B: duplicate description ───────────────────────────────────────
    result_b = _check_duplicate_description(desc, nominator_id, config, nomination_id)
    if result_b.action == "flag":
        logger.info(
            "Duplicate Descriptions check flagged",
            extra={"nomination_id": nomination_id, "nominator_id": nominator_id},
        )
        accumulated.append(result_b)

    if not accumulated:
        final_result = _PASS

    # Single flag — return it directly (preserves original check name / reason)
    elif len(accumulated) == 1:
        final_result = accumulated[0]
    else:
        # Multiple flags — merge into one combined result
        combined_check = "|".join(r.check for r in accumulated if r.check)
        combined_reason = " | ".join(r.reason for r in accumulated if r.reason)
        final_result = CheckResult(
            action="flag",
            reason=combined_reason,
            check=combined_check,
        )

    return _complete_semantic_result(
        final_result,
        embedding_result=embedding_result,
        llm_result=llm_result,
        duplicate_result=result_b,
        decision_rule=decision_rule,
    )
