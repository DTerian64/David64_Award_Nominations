"""Ensure explicit HRBP exclusions cannot re-enter RF cold-start training.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\fraud-analytics-job"
    python -m unittest tests.test_rf_training_exclusions -v
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    def test_explicitly_excluded_row_survives_bootstrap_without_a_label(self):
        rows = []
        for nomination_id in range(1, 12):
            row = {
                column: 0.0 for column in train_rf_model.P2P_FEATURE_COLUMNS
            }
            row["NominationId"] = nomination_id
            row["IsFraud"] = pd.NA if nomination_id == 11 else 0
            row["LabelSource"] = (
                train_rf_model.labels_mod.SOURCE_EXCLUDED
                if nomination_id == 11
                else train_rf_model.labels_mod.SOURCE_UNLABELLED
            )
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


def _label_frame(fraud=8, legitimate=40, unlabelled=100, excluded=3):
    sources = (
        [("hrbp", 1)] * fraud + [("hrbp", 0)] * legitimate
        + [("unlabelled", pd.NA)] * unlabelled + [("excluded", pd.NA)] * excluded
    )
    rows = []
    for index, (source, label) in enumerate(sources):
        row = {
            column: float(index % (position + 2))
            for position, column in enumerate(train_rf_model.P2P_FEATURE_COLUMNS)
        }
        row.update(NominationId=index + 1, IsFraud=label, LabelSource=source)
        rows.append(row)
    return pd.DataFrame(rows, columns=[
        *train_rf_model.P2P_FEATURE_COLUMNS, 'NominationId', 'IsFraud', 'LabelSource',
    ])


class RFTrainingTransitionTests(unittest.TestCase):
    def test_48_human_labels_use_bootstrap_without_changing_human_or_source_data(self):
        source = _label_frame()
        before = source.copy(deep=True)
        with patch.object(train_rf_model, 'IsolationForest', return_value=_IsolationForest()) as iso:
            training, stats = train_rf_model.prepare_rf_training_data(source, 1)
        iso.assert_called_once()
        self.assertEqual(stats['training_mode'], 'BOOTSTRAP_HYBRID')
        self.assertEqual(stats['human_label_count'], 48)
        self.assertEqual(stats['pseudo_label_count'], 100)
        self.assertEqual(stats['pseudo_fraud_count'], 5)
        self.assertEqual(stats['excluded_count'], 3)
        self.assertEqual(len(training), 148)
        self.assertEqual(training.iloc[:48]['IsFraud'].tolist(), [1] * 8 + [0] * 40)
        self.assertNotIn('excluded', training['LabelSource'].values)
        pd.testing.assert_frame_equal(source, before)
        # The GNN's shared human-only filter cannot consume RF pseudo-labels.
        self.assertEqual(len(train_rf_model.labels_mod.human_confirmed(training)), 48)

    def test_supervised_threshold_and_class_balance_control_transition(self):
        for fraud, legit, expected in [(5, 45, 'SUPERVISED'), (5, 44, 'BOOTSTRAP_HYBRID'),
                                       (1, 49, 'BOOTSTRAP_HYBRID'), (0, 50, 'BOOTSTRAP_HYBRID'),
                                       (50, 0, 'BOOTSTRAP_HYBRID'), (0, 0, 'BOOTSTRAP')]:
            with self.subTest(fraud=fraud, legitimate=legit):
                with patch.object(train_rf_model, 'IsolationForest', return_value=_IsolationForest()) as iso:
                    training, stats = train_rf_model.prepare_rf_training_data(
                        _label_frame(fraud, legit), 1,
                    )
                self.assertIsNotNone(training)
                self.assertEqual(stats['training_mode'], expected)
                self.assertEqual(iso.call_count, 0 if expected == 'SUPERVISED' else 1)
                if expected == 'SUPERVISED':
                    self.assertEqual(len(training), 50)
                    self.assertEqual(stats['pseudo_label_count'], 0)

    def test_insufficient_candidates_skip_without_fitting_isolation_forest(self):
        for source in [_label_frame(unlabelled=0), _label_frame(0, 0, 10, 100),
                       _label_frame(1, 49, 0), _label_frame(0, 0, 0, 0)]:
            with self.subTest(rows=len(source)):
                with patch.object(train_rf_model, 'IsolationForest') as iso:
                    training, stats = train_rf_model.prepare_rf_training_data(source, 1)
                iso.assert_not_called()
                self.assertIsNone(training)
                self.assertTrue(stats['skipped'])
                self.assertEqual(stats['reason_code'], 'BOOTSTRAP_UNAVAILABLE')

    def test_bootstrap_with_no_anomalies_skips_instead_of_training_one_class(self):
        iso = MagicMock()
        iso.predict.side_effect = lambda features: np.ones(len(features), dtype=int)
        with patch.object(train_rf_model, 'IsolationForest', return_value=iso):
            training, stats = train_rf_model.prepare_rf_training_data(_label_frame(0, 0), 1)
        self.assertIsNone(training)
        self.assertTrue(stats['skipped'])
        self.assertEqual(stats['pseudo_fraud_count'], 0)

    def test_legacy_predictions_and_exclusions_do_not_become_supervised_labels(self):
        source = _label_frame(5, 45, 0, 2)
        source.loc[source['LabelSource'] == 'excluded', 'IsFraud'] = 1
        source.loc[49, 'LabelSource'] = 'model'
        training, stats = train_rf_model.prepare_rf_training_data(source, 1)
        self.assertIsNone(training)
        self.assertEqual(stats['human_label_count'], 49)
        self.assertEqual(stats['training_samples'], 49)

    def test_invalid_human_label_is_an_error_not_a_bootstrap_candidate(self):
        source = _label_frame()
        source.loc[0, 'IsFraud'] = pd.NA
        with patch.object(train_rf_model, 'IsolationForest') as iso:
            with self.assertRaisesRegex(ValueError, 'Human RF training labels'):
                train_rf_model.prepare_rf_training_data(source, 1)
        iso.assert_not_called()

    def test_train_model_uses_hybrid_labels_and_publishes_diagnostics(self):
        source = _label_frame()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(train_rf_model, 'OUTPUT_DIR', root),
                patch.object(train_rf_model, 'get_tenant_embed_model', return_value='test'),
                patch.object(train_rf_model, 'SentenceTransformer'),
                patch.object(train_rf_model, 'add_semantic_features', side_effect=lambda df, _: df),
                patch.object(train_rf_model, 'extract_features', side_effect=lambda df: (df, {}, 0.0)),
                patch.object(train_rf_model, 'IsolationForest', return_value=_IsolationForest()),
                patch.object(train_rf_model, '_upload_artefact'),
                patch.object(train_rf_model, 'create_visualizations') as visualize,
                patch.object(train_rf_model, '_write_rf_manifest', return_value=root / 'manifest.json') as manifest,
            ):
                model, stats = train_rf_model.train_model(source, 1, 'Test Tenant')
            self.assertEqual(stats['training_samples'], 148)
            self.assertEqual(stats['training_mode'], 'BOOTSTRAP_HYBRID')
            self.assertEqual(stats['evaluation_basis'], 'BOOTSTRAP_LABEL_HOLDOUT_NOT_INDEPENDENT')
            self.assertEqual(model['training_label_summary']['human_label_count'], 48)
            self.assertEqual(manifest.call_args.kwargs['training_metrics'], stats)
            self.assertEqual(len(visualize.call_args.args[0]), 148)

    def test_job_records_insufficient_labels_as_skipped_and_preserves_serving_model(self):
        with (
            patch.object(train_rf_model, 'get_db_connection'),
            patch.object(train_rf_model, 'get_tenants', return_value=[(1, 'Test Tenant')]),
            patch.object(train_rf_model, 'load_data', return_value=_label_frame(unlabelled=0, excluded=0)),
            patch.object(train_rf_model, 'SentenceTransformer') as embeddings,
            patch.object(train_rf_model, '_upload_artefact') as upload,
            patch.object(train_rf_model, '_record_rf_status') as record,
        ):
            train_rf_model.main([1])
        embeddings.assert_not_called()
        upload.assert_not_called()
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs['attempt_status'], 'SKIPPED')
        self.assertEqual(record.call_args.kwargs['diagnostics']['human_label_count'], 48)
        self.assertNotIn('serving_status', record.call_args.kwargs)
        self.assertNotIn('serving_version', record.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
