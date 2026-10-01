import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.award_nominations.features import labels


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

    def test_supervised_targets_combine_human_and_isolated_synthetic_labels(self):
        frame = pd.DataFrame([
            {"NominationId": 1, "IsFraud": 1, "LabelSource": labels.SOURCE_HRBP},
            {
                "NominationId": 2,
                "IsFraud": 0,
                "LabelSource": labels.SOURCE_SYNTHETIC,
            },
            {"NominationId": 3, "IsFraud": 1, "LabelSource": labels.SOURCE_MODEL},
            {
                "NominationId": 4,
                "IsFraud": pd.NA,
                "LabelSource": labels.SOURCE_EXCLUDED,
            },
        ])

        result = labels.supervised_targets(frame)

        self.assertEqual(result["NominationId"].tolist(), [1, 2])
        self.assertEqual(result["IsFraud"].tolist(), [1, 0])
        self.assertEqual(labels.human_confirmed(frame)["NominationId"].tolist(), [1])

    def test_excluded_review_is_explicit_but_not_a_training_target(self):
        frame = pd.DataFrame([
            {"NominationId": 5, "IsFraud": pd.NA, "LabelSource": labels.SOURCE_EXCLUDED},
        ])
        stats = labels.summarise(frame, tenant_id=3)
        result = labels.human_confirmed(frame)

        self.assertEqual(stats["n_excluded"], 1)
        self.assertEqual(stats["n_hrbp"], 0)
        self.assertTrue(result.empty)

    def test_canonical_excluded_outcomes_take_precedence_over_provenance(self):
        for provenance in ("HUMAN_INVESTIGATION", "SYNTHETIC_GROUND_TRUTH"):
            with self.subTest(provenance=provenance):
                dataset = SimpleNamespace(
                    snapshot=SimpleNamespace(is_synthetic_tenant=True),
                    events=(
                        SimpleNamespace(
                            event_id="5",
                            status="Approved",
                            attributes={},
                        ),
                    ),
                    labels=(
                        SimpleNamespace(
                            event_id="5",
                            disposition="EXCLUDED",
                            provenance=provenance,
                            reviewed_by="reviewer",
                            reviewed_at=None,
                            metadata={},
                            behavior_labels=(),
                        ),
                    ),
                )

                frame = labels.label_frame(dataset)

                self.assertEqual(frame.loc[0, "LabelSource"], labels.SOURCE_EXCLUDED)
                self.assertTrue(pd.isna(frame.loc[0, "IsFraud"]))
                self.assertTrue(labels.supervised_targets(frame).empty)

    def test_missing_label_contract_fails_loudly(self):
        with self.assertRaises(ValueError):
            labels.human_confirmed(pd.DataFrame([{"NominationId": 1}]))

    @patch.object(labels, "label_frame")
    def test_loader_projects_an_already_canonical_dataset(self, project):
        dataset = object()
        expected = pd.DataFrame(
            [{"NominationId": 9, "IsFraud": pd.NA, "LabelSource": "excluded"}]
        )
        project.return_value = expected

        result = labels.load_labels(dataset)

        project.assert_called_once_with(dataset)
        self.assertIs(result, expected)

    def test_tabular_feature_frame_receives_same_shared_labels(self):
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


class ConfirmedPatternContractTests(unittest.TestCase):
    def _row(self, metadata: dict, *, is_fraud: int = 1) -> dict:
        return {
            "LabelSource": labels.SOURCE_SYNTHETIC,
            "IsFraud": is_fraud,
            "TrainingDispositionMetadataJson": metadata,
        }

    def test_v4_fraud_uses_exact_directly_persisted_pattern(self):
        row = self._row({
            "generator_version": "synthetics-inc-v4.0",
            "scenario_family": "RING",
            "confirmed_patterns": ["RING"],
        })

        self.assertEqual(labels._confirmed_patterns(row), ("RING",))

    def test_v4_requires_confirmed_patterns(self):
        row = self._row({
            "generator_version": "synthetics-inc-v4.0",
            "scenario_family": "RING",
        })

        with self.assertRaisesRegex(ValueError, "require confirmed_patterns"):
            labels._confirmed_patterns(row)

    def test_scenario_family_is_never_used_as_a_legacy_fallback(self):
        row = self._row({
            "generator_version": "synthetics-inc-v3.0",
            "scenario_family": "BIPARTITE_DENSE_BLOCK",
        })

        self.assertEqual(labels._confirmed_patterns(row), ())

    def test_v4_rejects_pattern_that_differs_from_scenario_family(self):
        row = self._row({
            "generator_version": "synthetics-inc-v4.0",
            "scenario_family": "RING",
            "confirmed_patterns": ["RECIPROCAL"],
        })

        with self.assertRaisesRegex(ValueError, "equal to scenario_family"):
            labels._confirmed_patterns(row)

    def test_v4_legitimate_requires_empty_patterns(self):
        row = self._row({
            "generator_version": "synthetics-inc-v4.0",
            "scenario_family": "LEGITIMATE",
            "confirmed_patterns": [],
        }, is_fraud=0)

        self.assertEqual(labels._confirmed_patterns(row), ())


if __name__ == "__main__":
    unittest.main()
