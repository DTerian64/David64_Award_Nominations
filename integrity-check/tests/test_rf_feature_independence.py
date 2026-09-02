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
    "TransactionalPhraseScore",
]


class _Scaler:
    def transform(self, values):
        return values


class RfFeatureIndependenceTests(unittest.TestCase):
    def test_transactional_phrase_score_matches_training_contract(self):
        score = random_forest_check.transactional_phrase_score

        self.assertEqual(score("Consistently exceeded expectations."), 0.0)
        self.assertEqual(score("You helped me, so I owe them in return."), 0.5)
        self.assertEqual(
            score(
                "You helped me, saved my deadline and my project; I owe them, "
                "will nominate them back in return."
            ),
            1.0,
        )

    def test_legacy_graph_dependent_artifact_is_rejected(self):
        self.assertFalse(random_forest_check._is_independent_rf_artifact({
            "p2p_feature_columns": [*RF_FEATURE_COLUMNS, "GraphCycleFlag"],
            "feature_contract": "rf-native-v3",
            "transactional_phrase_rule_version": "transactional-phrase-score-v1",
        }))
        self.assertTrue(random_forest_check._is_independent_rf_artifact({
            "p2p_feature_columns": RF_FEATURE_COLUMNS,
            "feature_contract": "rf-native-v3",
            "transactional_phrase_rule_version": "transactional-phrase-score-v1",
        }))
        self.assertFalse(random_forest_check._is_independent_rf_artifact({
            "p2p_feature_columns": RF_FEATURE_COLUMNS[:-1],
            "feature_contract": "rf-native-v2",
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
            "description": "You helped me, so I owe them in return.",
            "category_id": None,
        }

        scaled, feature_values, _similarity = random_forest_check._build_features(
            details, model_data
        )

        self.assertEqual(scaled.shape, (1, len(RF_FEATURE_COLUMNS)))
        self.assertEqual(feature_values["HasReciprocalNomination"], 1)
        self.assertEqual(feature_values["TransactionalPhraseScore"], 0.5)
        self.assertFalse(any(name.startswith("Graph") for name in feature_values))
        self.assertNotIn("SuperNominatorFlag", feature_values)


if __name__ == "__main__":
    unittest.main()
