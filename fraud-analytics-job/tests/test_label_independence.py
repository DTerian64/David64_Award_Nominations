import sys
import unittest
from pathlib import Path

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
        ])
        result = labels.human_confirmed(frame)
        self.assertEqual(result["NominationId"].tolist(), [1, 4])
        self.assertEqual(result["IsFraud"].tolist(), [1, 0])

    def test_missing_label_contract_fails_loudly(self):
        with self.assertRaises(ValueError):
            labels.human_confirmed(pd.DataFrame([{"NominationId": 1}]))


if __name__ == "__main__":
    unittest.main()
