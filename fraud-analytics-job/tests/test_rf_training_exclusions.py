"""Ensure explicit HRBP exclusions cannot re-enter RF cold-start training.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\fraud-analytics-job"
    python -m unittest tests.test_rf_training_exclusions -v
"""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from modeling import train_rf_model


class _IsolationForest:
    def fit(self, _features):
        return self

    def predict(self, features):
        predictions = np.ones(len(features), dtype=int)
        predictions[:5] = -1
        return predictions


class RFTrainingExclusionTests(unittest.TestCase):
    def test_explicitly_excluded_row_survives_bootstrap_as_unlabelled(self):
        rows = []
        for nomination_id in range(1, 12):
            row = {
                column: 0.0 for column in train_rf_model.P2P_FEATURE_COLUMNS
            }
            row["NominationId"] = nomination_id
            row["IsFraud"] = pd.NA if nomination_id == 11 else 0
            rows.append(row)

        with patch.object(
            train_rf_model, "IsolationForest", return_value=_IsolationForest()
        ), patch.object(
            train_rf_model,
            "get_db_connection",
            side_effect=RuntimeError("graph DB not needed in this unit test"),
        ):
            result = train_rf_model.bootstrap_fraud_labels(
                pd.DataFrame(rows), tenant_id=3
            )

        self.assertIsNotNone(result)
        excluded = result.loc[result["NominationId"] == 11].iloc[0]
        self.assertTrue(pd.isna(excluded["IsFraud"]))
        self.assertEqual(int(result["IsFraud"].sum()), 5)


if __name__ == "__main__":
    unittest.main()
