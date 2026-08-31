"""RF serving must remain independent from Graph Analytics outputs."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import random_forest_check


RF_FEATURE_COLUMNS = [
    "Amount",
    "DayOfWeek",
    "Month",
    "IsWeekend",
    "NominatorTotalNominations",
    "NominatorAvgAmount",
    "NominatorStdAmount",
    "NominatorUniqueBeneficiaries",
    "BeneficiaryTotalReceived",
    "BeneficiaryAvgAmountReceived",
    "HasReciprocalNomination",
    "PairNominationCount",
    "AmountZScore",
    "IsHighAmount",
    "NominatorConcentrationRatio",
    "CategoryFraudRate",
    "DescriptionCosineSim",
    "DescriptionEmbDistance",
]


class _Scaler:
    def transform(self, values):
        return values


class RfFeatureIndependenceTests(unittest.TestCase):
    def test_legacy_graph_dependent_artifact_is_rejected(self):
        self.assertFalse(random_forest_check._is_independent_rf_artifact({
            "p2p_feature_columns": [*RF_FEATURE_COLUMNS, "GraphCycleFlag"],
        }))
        self.assertTrue(random_forest_check._is_independent_rf_artifact({
            "p2p_feature_columns": RF_FEATURE_COLUMNS,
        }))

    @patch.object(random_forest_check, "_get_embed_model")
    @patch.object(random_forest_check.db, "get_beneficiary_descriptions", return_value=[])
    @patch.object(random_forest_check.db, "get_pair_nomination_count", return_value=2)
    @patch.object(random_forest_check.db, "check_reciprocal_nomination", return_value=True)
    @patch.object(random_forest_check.db, "get_beneficiary_history", return_value=[])
    @patch.object(random_forest_check.db, "get_nominator_history", return_value=[])
    def test_feature_builder_uses_rf_native_inputs_only(
        self,
        _nominator_history,
        _beneficiary_history,
        _reciprocal,
        _pair_count,
        _descriptions,
        _embed_model,
    ):
        model_data = {
            "p2p_feature_columns": RF_FEATURE_COLUMNS,
            "p2p_scaler": _Scaler(),
            "amount_mean": 100.0,
            "amount_std": 20.0,
            "category_fraud_rate": {},
            "global_fraud_rate": 0.0,
        }
        details = {
            "nomination_id": 11,
            "tenant_id": 3,
            "nominator_id": 21,
            "beneficiary_id": 31,
            "amount": 120.0,
            "description": "",
            "category_id": None,
        }

        scaled, feature_values, _similarity = random_forest_check._build_features(
            details, model_data
        )

        self.assertEqual(scaled.shape, (1, len(RF_FEATURE_COLUMNS)))
        self.assertEqual(feature_values["HasReciprocalNomination"], 1)
        self.assertFalse(any(name.startswith("Graph") for name in feature_values))
        self.assertNotIn("SuperNominatorFlag", feature_values)
        self.assertNotIn("TransactionalLanguageFlag", feature_values)


if __name__ == "__main__":
    unittest.main()
