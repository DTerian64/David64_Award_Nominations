import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modeling import labels


class HumanConfirmedLabelTests(unittest.TestCase):
    def test_only_hrbp_labels_are_training_targets(self):
        frame = pd.DataFrame([
            {"NominationId": 1, "IsFraud": 1, "LabelSource": labels.SOURCE_HRBP},
            {"NominationId": 2, "IsFraud": 1, "LabelSource": labels.SOURCE_MODEL},
            {"NominationId": 3, "IsFraud": 0, "LabelSource": labels.SOURCE_UNLABELLED},
            {"NominationId": 4, "IsFraud": 0, "LabelSource": labels.SOURCE_HRBP},
            {"NominationId": 5, "IsFraud": pd.NA, "LabelSource": labels.SOURCE_EXCLUDED},
        ])
        result = labels.human_confirmed(frame)
        self.assertEqual(result["NominationId"].tolist(), [1, 4])
        self.assertEqual(result["IsFraud"].tolist(), [1, 0])

    def test_excluded_review_is_explicit_but_not_a_training_target(self):
        frame = pd.DataFrame([
            {"NominationId": 5, "IsFraud": pd.NA, "LabelSource": labels.SOURCE_EXCLUDED},
        ])
        stats = labels.summarise(frame, tenant_id=3)
        result = labels.human_confirmed(frame)

        self.assertEqual(stats["n_excluded"], 1)
        self.assertEqual(stats["n_hrbp"], 0)
        self.assertTrue(result.empty)

    def test_missing_label_contract_fails_loudly(self):
        with self.assertRaises(ValueError):
            labels.human_confirmed(pd.DataFrame([{"NominationId": 1}]))

    @patch.object(labels.pd, "read_sql")
    def test_loader_uses_model_neutral_decision_and_preserves_exclusion(
        self, read_sql
    ):
        read_sql.return_value = pd.DataFrame([
            {
                "NominationId": 9,
                "RiskLevel": "CRITICAL",
                "ConfirmedBy": "HRBP:77",
                "ConfirmedAt": "2026-08-26",
                "TrainingDisposition": "EXCLUDED",
                "IsFraud": None,
                "LabelSource": labels.SOURCE_EXCLUDED,
            }
        ])

        result = labels.load_labels(object(), tenant_id=3)

        query = read_sql.call_args.args[0]
        self.assertIn("dbo.FraudDecisionResults", query)
        self.assertIn("fdr.TrainingDisposition = 'EXCLUDED'", query)
        self.assertTrue(result.loc[0, "IsFraud"] is pd.NA)

    def test_rf_feature_frame_receives_same_shared_labels(self):
        features = pd.DataFrame([
            {"NominationId": 1, "Amount": 1000},
            {"NominationId": 2, "Amount": 2000},
        ])
        label_frame = pd.DataFrame([
            {
                "NominationId": 1,
                "IsFraud": 1,
                "LabelSource": labels.SOURCE_HRBP,
                "TrainingDisposition": "FRAUD",
            },
            {
                "NominationId": 2,
                "IsFraud": pd.NA,
                "LabelSource": labels.SOURCE_EXCLUDED,
                "TrainingDisposition": "EXCLUDED",
            },
        ])

        result = labels.attach_training_labels(features, label_frame)

        self.assertEqual(result.loc[0, "IsFraud"], 1)
        self.assertTrue(result.loc[1, "IsFraud"] is pd.NA)
        self.assertEqual(result.loc[1, "TrainingDisposition"], "EXCLUDED")


if __name__ == "__main__":
    unittest.main()
