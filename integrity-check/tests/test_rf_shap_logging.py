"""Tests for nomination-scoped Random Forest SHAP observability.

Purpose:
    Ensure every SHAP path records a durable status with nomination and tenant
    context so it can be persisted in dbo.Nomination_Logs.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest tests.test_rf_shap_logging -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import random_forest_check
from utils.azure_credential import credential


class RfExplanationAuthenticationTests(unittest.TestCase):
    def setUp(self):
        cache = patch.object(random_forest_check, "_llm_client", None)
        cache.start()
        self.addCleanup(cache.stop)

    def test_explanation_uses_shared_identity_without_api_key_and_reuses_client(self):
        environment = {
            "AZURE_OPENAI_ENDPOINT": "https://test.invalid",
            "AZURE_OPENAI_DEPLOYMENT": "configured-deployment",
            "AZURE_OPENAI_API_VERSION": "2024-08-01-preview",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("azure.identity.get_bearer_token_provider") as provider,
            patch("openai.AzureOpenAI") as client_factory,
        ):
            completion = client_factory.return_value.chat.completions.create
            completion.return_value.choices = [
                MagicMock(message=MagicMock(content="  Generated RF explanation.  "))
            ]
            for _ in range(2):
                result = random_forest_check._generate_explanation(
                    [{"feature": "AmountZScore", "raw_value": 1.8, "contribution": 0.13}],
                    52, "MEDIUM",
                )
                self.assertEqual(result, "Generated RF explanation.")
            provider.assert_called_once_with(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            client_factory.assert_called_once_with(
                azure_ad_token_provider=provider.return_value,
                api_version=environment["AZURE_OPENAI_API_VERSION"],
                azure_endpoint=environment["AZURE_OPENAI_ENDPOINT"],
            )
            self.assertEqual(completion.call_count, 2)
            self.assertEqual(completion.call_args.kwargs["model"], "configured-deployment")
            self.assertIn("MEDIUM", completion.call_args.kwargs["messages"][0]["content"])

    def test_missing_endpoint_fails_clearly_before_constructing_client(self):
        with patch.dict(os.environ, {}, clear=True), patch("openai.AzureOpenAI") as factory:
            with self.assertRaisesRegex(ValueError, "AZURE_OPENAI_ENDPOINT not set"):
                random_forest_check._generate_explanation([], 52, "MEDIUM")
        factory.assert_not_called()
        self.assertIsNone(random_forest_check._llm_client)

    def test_failed_initialization_can_retry_without_caching_failure(self):
        client = MagicMock()
        with (
            patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": "https://test.invalid"}, clear=True),
            patch("azure.identity.get_bearer_token_provider"),
            patch("openai.AzureOpenAI", side_effect=[RuntimeError("client unavailable"), client]),
        ):
            with self.assertRaisesRegex(RuntimeError, "client unavailable"):
                random_forest_check._get_llm_client()
            self.assertIsNone(random_forest_check._llm_client)
            self.assertIs(random_forest_check._get_llm_client(), client)


class _RandomForest:
    def __init__(self, probability: float):
        self.probability = probability

    def predict_proba(self, _features):
        return np.array([[1.0 - self.probability, self.probability]])


def _find_call(mock, message: str):
    return next(call for call in mock.call_args_list if call.args[0] == message)


class RfShapLoggingTests(unittest.TestCase):
    def _assess(
        self,
        probability,
        *,
        shap_result=None,
        shap_error=None,
        explanation_error=None,
    ):
        model_data = {
            "p2p_model": _RandomForest(probability),
            "model_version": "rf-test",
        }
        patches = [
            patch.object(random_forest_check, "_get_model", return_value=model_data),
            patch.object(
                random_forest_check,
                "_build_features",
                return_value=(np.zeros((1, 2)), {"AmountZScore": 2.5}, 0.1),
            ),
            patch.object(
                random_forest_check,
                "_score_routing_thresholds",
                return_value={"critical": 80, "high": 60, "medium": 40, "low": 20},
            ),
            patch.object(random_forest_check, "_warning_flags", return_value=[]),
            patch.object(
                random_forest_check,
                "_generate_explanation",
                return_value="Generated explanation.",
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4] as generate_explanation, patch.object(
            random_forest_check, "_compute_shap", return_value=shap_result
        ) as compute_shap, patch.object(
            random_forest_check.logger, "info"
        ) as info, patch.object(
            random_forest_check.logger, "warning"
        ) as warning:
            if shap_error is not None:
                compute_shap.side_effect = shap_error
            if explanation_error is not None:
                generate_explanation.side_effect = explanation_error
            result = random_forest_check.assess(
                {"nomination_id": 13869}, tenant_id=3
            )
        return result, compute_shap, info, warning

    def test_completed_shap_log_is_nomination_scoped_and_contains_features(self):
        contributions = [
            {"feature": "AmountZScore", "raw_value": 2.5, "contribution": 0.31},
            {"feature": "PairNominationCount", "raw_value": 7.0, "contribution": 0.22},
        ]
        result, compute_shap, info, warning = self._assess(
            0.81, shap_result=contributions
        )

        self.assertEqual(result["shap_status"], "COMPLETED")
        self.assertIsNone(result["shap_reason"])
        self.assertEqual(result["llm_explanation"], "Generated explanation.")
        self.assertEqual(result["llm_explanation_status"], "COMPLETED")
        self.assertIsNone(result["llm_explanation_reason"])
        compute_shap.assert_called_once()
        warning.assert_not_called()

        completed = _find_call(info, "RF SHAP assessment completed")
        self.assertEqual(completed.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(completed.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(completed.kwargs["extra"]["shap_feature_count"], 2)
        self.assertEqual(completed.kwargs["extra"]["top_features"], contributions)

        llm_completed = _find_call(info, "RF LLM explanation completed")
        self.assertEqual(llm_completed.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(llm_completed.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(
            llm_completed.kwargs["extra"]["llm_explanation"],
            "Generated explanation.",
        )

    def test_llm_explanation_is_generated_for_every_flagged_rf_risk(self):
        contributions = [
            {"feature": "AmountZScore", "raw_value": 2.5, "contribution": 0.31}
        ]
        for probability, expected_risk in (
            (0.48, "MEDIUM"),
            (0.68, "HIGH"),
            (0.88, "CRITICAL"),
        ):
            with self.subTest(risk=expected_risk):
                result, _compute_shap, info, warning = self._assess(
                    probability, shap_result=contributions
                )
                self.assertEqual(result["risk_level"], expected_risk)
                self.assertEqual(result["llm_explanation"], "Generated explanation.")
                self.assertEqual(result["llm_explanation_status"], "COMPLETED")
                self.assertIsNone(result["llm_explanation_reason"])
                _find_call(info, "RF LLM explanation starting")
                _find_call(info, "RF LLM explanation completed")
                warning.assert_not_called()

    def test_llm_failure_uses_logged_review_fallback_without_blocking(self):
        contributions = [
            {"feature": "AmountZScore", "raw_value": 2.5, "contribution": 0.31}
        ]
        result, _compute_shap, _info, warning = self._assess(
            0.68,
            shap_result=contributions,
            explanation_error=RuntimeError("LLM unavailable"),
        )

        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["llm_explanation_status"], "FALLBACK")
        self.assertEqual(result["llm_explanation_reason"], "generation_error")
        self.assertIn("Please review", result["llm_explanation"])

        fallback = _find_call(warning, "RF LLM explanation fallback used")
        self.assertEqual(fallback.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(fallback.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(fallback.kwargs["extra"]["error"], "LLM unavailable")

    def test_failed_shap_log_is_nomination_scoped_and_result_is_explicit(self):
        result, compute_shap, _info, warning = self._assess(
            0.81, shap_error=RuntimeError("explainer failed")
        )

        self.assertEqual(result["shap_status"], "FAILED")
        self.assertEqual(result["shap_reason"], "computation_error")
        self.assertEqual(result["shap_explanations"], [])
        self.assertIsNone(result["llm_explanation"])
        self.assertEqual(result["llm_explanation_status"], "SKIPPED")
        self.assertEqual(result["llm_explanation_reason"], "shap_unavailable")
        compute_shap.assert_called_once()

        failed = _find_call(warning, "RF SHAP assessment failed")
        self.assertEqual(failed.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(failed.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(failed.kwargs["extra"]["error"], "explainer failed")

    def test_below_threshold_shap_skip_is_nomination_scoped(self):
        result, compute_shap, info, warning = self._assess(0.10)

        self.assertEqual(result["shap_status"], "SKIPPED")
        self.assertEqual(result["shap_reason"], "risk_below_medium")
        self.assertIsNone(result["llm_explanation"])
        self.assertEqual(result["llm_explanation_status"], "SKIPPED")
        self.assertEqual(result["llm_explanation_reason"], "risk_below_medium")
        compute_shap.assert_not_called()
        warning.assert_not_called()

        skipped = _find_call(info, "RF SHAP assessment skipped")
        self.assertEqual(skipped.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(skipped.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(skipped.kwargs["extra"]["shap_reason"], "risk_below_medium")


if __name__ == "__main__":
    unittest.main()
