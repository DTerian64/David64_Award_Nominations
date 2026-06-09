"""
description_check.py — Pre-ML nomination description quality checks
====================================================================

Runs two sequential checks before the ML fraud model, using per-tenant
thresholds from DescCheckConfig (loaded from dbo.Tenants.desc_check_config).

Check A — Category Alignment  (auto-reject)
    Embeds the nomination description and the category description, then
    computes cosine similarity.  Nominations scoring below the tenant's
    category_alignment_threshold are rejected outright — the HRBP never
    sees them.  If the nomination has no category (category_description is
    None), this check is skipped automatically.

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
    )

    result is a CheckResult:
        action   str        "reject" | "flag" | "pass"
        reason   str | None  human-readable explanation (for rejection email /
                             HRBP warning flag), None when action == "pass"
        check    str | None  "category_alignment" | "duplicate_description" | None
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

import db
from db import DescCheckConfig

logger = logging.getLogger("integrity_check.description_check")

# ── Embed model cache (shared with fraud_check.py via the same process) ───────
# Imported lazily to avoid a hard startup dependency — the sentence-transformers
# package may not be installed in every environment (e.g. unit test runners).

def _get_embed_model(model_name: str):
    """
    Delegate to fraud_check's per-model cache so we never load the same
    model twice in the same process.
    """
    import fraud_check
    return fraud_check._get_embed_model(model_name)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    action: str             # "reject" | "flag" | "pass"
    reason: Optional[str]   # human-readable; None when action == "pass"
    check:  Optional[str]   # "category_alignment" | "duplicate_description" | None


_PASS = CheckResult(action="pass", reason=None, check=None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D normalised vectors."""
    return float(np.dot(a, b))


def _embed(model, texts: list[str]) -> np.ndarray:
    """Encode a list of texts into L2-normalised embeddings."""
    return model.encode(texts, normalize_embeddings=True)


# ── Check A: category alignment ───────────────────────────────────────────────

def _check_category_alignment(
    description:          str,
    category_description: str,
    config:               DescCheckConfig,
) -> CheckResult:
    """
    Returns "reject" if the description is semantically unrelated to the
    nomination category.  Threshold of 0.0 effectively disables the check.
    """
    if config.category_alignment_threshold <= 0.0:
        return _PASS

    model   = _get_embed_model(config.embed_model)
    embs    = _embed(model, [description, category_description])
    sim     = _cosine_sim(embs[0], embs[1])

    logger.debug(
        "Category alignment: sim=%.4f threshold=%.4f",
        sim, config.category_alignment_threshold,
    )

    if sim < config.category_alignment_threshold:
        reason = (
            f"The description doesn't appear to relate to the "
            f"'{category_description}' category "
            f"(similarity {sim:.2f}, minimum {config.category_alignment_threshold:.2f}). "
            f"Please describe a specific action or behaviour that reflects "
            f"'{category_description}'."
        )
        return CheckResult(action="reject", reason=reason, check="category_alignment")

    return _PASS


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
        return _PASS

    model    = _get_embed_model(config.embed_model)
    desc_emb = _embed(model, [description])[0]
    prior_embs = _embed(model, prior_descs)

    sims        = [_cosine_sim(desc_emb, e) for e in prior_embs]
    max_sim     = max(sims)
    most_similar_idx = int(np.argmax(sims))

    logger.debug(
        "Duplicate check: max_sim=%.4f threshold=%.4f nominator=%d",
        max_sim, config.duplicate_similarity_threshold, nominator_id,
    )

    if max_sim >= config.duplicate_similarity_threshold:
        snippet = prior_descs[most_similar_idx][:80].replace("\n", " ")
        reason  = (
            f"Description is highly similar to a prior nomination by the same "
            f"nominator (similarity {max_sim:.2f}). "
            f"Most similar previous description: \"{snippet}…\""
        )
        return CheckResult(action="flag", reason=reason, check="duplicate_description")

    return _PASS


# ── Public API ────────────────────────────────────────────────────────────────

def check(
    description:          str,
    category_description: Optional[str],
    nominator_id:         int,
    config:               DescCheckConfig,
    nomination_id:        Optional[int] = None,
) -> CheckResult:
    """
    Run Check A then Check B.  Check A failure short-circuits (no point running
    Check B on a description that will be rejected anyway).

    Args:
        description:          The nomination description text.
        category_description: The tenant's category label, or None if the
                              tenant uses no categories.
        nominator_id:         Used to look up the nominator's prior descriptions.
        config:               Per-tenant thresholds from DescCheckConfig.
        nomination_id:        The current nomination's ID — excluded from the
                              duplicate lookup so a nomination is never compared
                              against itself (the row is already committed to the
                              DB before this event fires).

    Returns:
        CheckResult with action "reject", "flag", or "pass".
    """
    desc = (description or "").strip()

    if not desc:
        # Empty description — treated as a structural failure.  Should have
        # been caught by the API layer, but defend here too.
        return CheckResult(
            action="reject",
            reason="Nomination description is empty.",
            check="category_alignment",
        )

    # ── Check A ───────────────────────────────────────────────────────────────
    if category_description:
        result_a = _check_category_alignment(desc, category_description, config)
        if result_a.action == "reject":
            logger.info(
                "Check A failed (category_alignment) for nominator %d", nominator_id
            )
            return result_a

    # ── Check B ───────────────────────────────────────────────────────────────
    result_b = _check_duplicate_description(desc, nominator_id, config, nomination_id)
    if result_b.action == "flag":
        logger.info(
            "Check B flagged (duplicate_description) for nominator %d", nominator_id
        )
        return result_b

    return _PASS
