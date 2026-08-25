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
from unittest.mock import patch

import numpy as np


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import random_forest_check


class _RandomForest:
    def __init__(self, probability: float):
        self.probability = probability

    def predict_proba(self, _features):
        return np.array([[1.0 - self.probability, self.probability]])


def _find_call(mock, message: str):
    return next(call for call in mock.call_args_list if call.args[0] == message)


class RfShapLoggingTests(unittest.TestCase):
    def _assess(self, probability, *, shap_result=None, shap_error=None):
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
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch.object(
            random_forest_check, "_compute_shap", return_value=shap_result
        ) as compute_shap, patch.object(
            random_forest_check.logger, "info"
        ) as info, patch.object(
            random_forest_check.logger, "warning"
        ) as warning:
            if shap_error is not None:
                compute_shap.side_effect = shap_error
            result = random_forest_check.assess(
                {"nomination_id": 13869}, tenant_id=3
            )
        return result, compute_shap, info, warning

    def test_completed_shap_log_is_nomination_scoped_and_contains_features(self):
        contributions = [
            {"feature": "AmountZScore", "raw_value": 2.5, "contribution": 0.31},
            {"feature": "GraphCycleFlag", "raw_value": 1.0, "contribution": 0.22},
        ]
        result, compute_shap, info, warning = self._assess(
            0.81, shap_result=contributions
        )

        self.assertEqual(result["shap_status"], "COMPLETED")
        self.assertIsNone(result["shap_reason"])
        compute_shap.assert_called_once()
        warning.assert_not_called()

        completed = _find_call(info, "RF SHAP assessment completed")
        self.assertEqual(completed.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(completed.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(completed.kwargs["extra"]["shap_feature_count"], 2)
        self.assertEqual(completed.kwargs["extra"]["top_features"], contributions)

    def test_failed_shap_log_is_nomination_scoped_and_result_is_explicit(self):
        result, compute_shap, _info, warning = self._assess(
            0.81, shap_error=RuntimeError("explainer failed")
        )

        self.assertEqual(result["shap_status"], "FAILED")
        self.assertEqual(result["shap_reason"], "computation_error")
        self.assertEqual(result["shap_explanations"], [])
        compute_shap.assert_called_once()

        failed = _find_call(warning, "RF SHAP assessment failed")
        self.assertEqual(failed.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(failed.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(failed.kwargs["extra"]["error"], "explainer failed")

    def test_below_threshold_shap_skip_is_nomination_scoped(self):
        result, compute_shap, info, warning = self._assess(0.10)

        self.assertEqual(result["shap_status"], "SKIPPED")
        self.assertEqual(result["shap_reason"], "risk_below_medium")
        compute_shap.assert_not_called()
        warning.assert_not_called()

        skipped = _find_call(info, "RF SHAP assessment skipped")
        self.assertEqual(skipped.kwargs["extra"]["nomination_id"], 13869)
        self.assertEqual(skipped.kwargs["extra"]["tenant_id"], 3)
        self.assertEqual(skipped.kwargs["extra"]["shap_reason"], "risk_below_medium")


if __name__ == "__main__":
    unittest.main()
