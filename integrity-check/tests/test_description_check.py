"""Tests for the integrity worker's combined description Check A.

Purpose
-------
Verify the decision policy that combines independently produced embedding
category-alignment evidence with LLM semantic evidence. The suite covers pass,
HRBP-review, LLM-unavailable, and incoherent-text rejection outcomes, plus the
nomination-log fields used to explain the combined decision. It also verifies
the explicit "Embedding Category Alignment check" log name.

Usage (PowerShell)
------------------
From this repository's integrity-check directory:

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_description_check.py" -v

The tests use mocks and synthetic evidence. They do not call Azure OpenAI,
download an embedding model, or connect to the application database.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import description_check
from utils.db import DescCheckConfig


PASS = description_check.CheckResult(action="pass", reason=None, check=None)
EMBEDDING_CONCERN = description_check.CheckResult(
    action="flag",
    reason="Embedding alignment was below its threshold.",
    check="category_alignment",
)
LLM_CONCERN = description_check.CheckResult(
    action="flag",
    reason="Semantic flags: category_mismatch",
    check="category_alignment",
)
INCOHERENT = description_check.CheckResult(
    action="reject",
    reason="Nomination description appears incoherent.",
    check="category_alignment",
)


class CheckADecisionTests(unittest.TestCase):
    def test_embedding_failure_and_llm_category_concern_route_to_hrbp(self):
        result, rule = description_check._combine_check_a(
            EMBEDDING_CONCERN,
            LLM_CONCERN,
            llm_enabled=True,
        )

        self.assertEqual(result.action, "flag")
        self.assertEqual(result.check, "category_alignment")
        self.assertIn(EMBEDDING_CONCERN.reason, result.reason)
        self.assertIn(LLM_CONCERN.reason, result.reason)
        self.assertEqual(rule, "embedding_and_llm_concern_hrbp")

    def test_llm_pass_clears_weak_embedding_match(self):
        result, rule = description_check._combine_check_a(
            EMBEDDING_CONCERN,
            PASS,
            llm_enabled=True,
        )

        self.assertEqual(result.action, "pass")
        self.assertEqual(rule, "llm_cleared_embedding_concern")

    def test_llm_concern_routes_to_hrbp_when_embedding_passes(self):
        result, rule = description_check._combine_check_a(
            PASS,
            LLM_CONCERN,
            llm_enabled=True,
        )

        self.assertEqual(result.action, "flag")
        self.assertEqual(rule, "llm_concern_hrbp")

    def test_embedding_concern_routes_to_hrbp_when_llm_unavailable(self):
        result, rule = description_check._combine_check_a(
            EMBEDDING_CONCERN,
            None,
            llm_enabled=True,
        )

        self.assertEqual(result.action, "flag")
        self.assertIn("unavailable", result.reason)
        self.assertEqual(rule, "embedding_concern_llm_unavailable_hrbp")

    def test_incoherent_llm_result_remains_a_hard_rejection(self):
        result, rule = description_check._combine_check_a(
            PASS,
            INCOHERENT,
            llm_enabled=True,
        )

        self.assertEqual(result.action, "reject")
        self.assertEqual(rule, "llm_incoherent_reject")


class _EmbeddingModel:
    def encode(self, _texts, normalize_embeddings=True):
        self.normalize_embeddings = normalize_embeddings
        return np.array([[1.0, 0.0], [0.10, np.sqrt(0.99)]])


class CheckAEvidenceTests(unittest.TestCase):
    @patch("description_check._get_embed_model", return_value=_EmbeddingModel())
    def test_embedding_log_uses_explicit_name(self, _model):
        config = DescCheckConfig(category_alignment_threshold=0.12)

        with self.assertLogs("integrity_check.description_check", level="INFO") as logs:
            result = description_check._check_embedding_category_alignment(
                "A specific but differently worded achievement.",
                "Going Above & Beyond",
                config,
                nomination_id=42,
            )

        self.assertEqual(result.action, "flag")
        messages = [record.getMessage() for record in logs.records]
        self.assertIn("Embedding Category Alignment check", messages)
        self.assertIn("Embedding Category Alignment check concern", messages)

    @patch("description_check._check_duplicate_description", return_value=PASS)
    @patch("description_check._evaluate_llm_semantics", return_value=LLM_CONCERN)
    @patch(
        "description_check._check_embedding_category_alignment",
        return_value=EMBEDDING_CONCERN,
    )
    def test_public_check_returns_combined_check_a_hrbp_flag(
        self, _embedding, _llm, _duplicate
    ):
        config = DescCheckConfig(
            category_alignment_threshold=0.12,
            llm_category_check_enabled=True,
        )

        with self.assertLogs("integrity_check.description_check", level="INFO") as logs:
            result = description_check.check(
                description="A detailed nomination description.",
                category_description="Going Above & Beyond",
                nominator_id=7,
                config=config,
                nomination_id=42,
                amount=3000,
            )

        self.assertEqual(result.action, "flag")
        self.assertEqual(result.check, "category_alignment")
        self.assertIn(EMBEDDING_CONCERN.reason, result.reason)
        self.assertIn(LLM_CONCERN.reason, result.reason)
        decision_log = next(
            record for record in logs.records
            if record.getMessage() == "Check A combined decision"
        )
        self.assertEqual(decision_log.embedding_result, "concern")
        self.assertEqual(decision_log.llm_result, "concern")
        self.assertEqual(decision_log.decision_rule, "embedding_and_llm_concern_hrbp")
        self.assertEqual(decision_log.action, "flag")


if __name__ == "__main__":
    unittest.main()
