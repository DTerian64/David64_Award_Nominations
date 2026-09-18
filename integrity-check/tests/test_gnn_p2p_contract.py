"""Regression tests for the nomination-only GNN serving contract."""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch


os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "integrity-engine-core" / "src"),
)

from inference import gnn_check


FEATURES = [
    "Amount",
    "AmountZScore",
    "DayOfWeek",
    "Month",
    "IsWeekend",
    "IsHighAmount",
]

V2_FEATURES = [
    "LogAmount",
    "CategoryRelativeAmountRobustZScore",
    "DaysBeforeGraphCutoff",
    "DayOfWeekSin",
    "DayOfWeekCos",
    "MonthSin",
    "MonthCos",
    "HistoricalStatus",
]

CAUSAL_FEATURES = [
    *V2_FEATURES,
    *gnn_check.CAUSAL_CONTEXT_FEATURE_COLUMNS,
]


class _RecordingModule(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.input_shape = None

    def forward(self, values):
        self.input_shape = tuple(values.shape)
        return torch.tensor([0.0])


class GnnP2PContractTests(unittest.TestCase):
    def setUp(self):
        gnn_check._head_cache.clear()

    def tearDown(self):
        gnn_check._head_cache.clear()

    def test_serving_uses_only_nominator_and_beneficiary_embeddings(self):
        module = _RecordingModule()
        head = {
            "model_version": "gnn-p2p-v1",
            "emb_dim": 4,
            "nomination_feature_columns": FEATURES,
            "nomination_scaler_mean": [0.0] * len(FEATURES),
            "nomination_scaler_std": [1.0] * len(FEATURES),
            "amount_mean": 100.0,
            "amount_std": 10.0,
            "_module": module,
        }
        embeddings = {
            1: (np.ones(4, dtype=np.float32), date.today(), "gnn-p2p-v1"),
            2: (np.ones(4, dtype=np.float32), date.today(), "gnn-p2p-v1"),
        }
        details = {
            "nomination_id": 10,
            "nominator_id": 1,
            "beneficiary_id": 2,
            "approver_id": 999,
            "amount": 125,
            "nomination_date": date.today(),
        }

        with (
            patch.object(gnn_check, "_get_head", return_value=head),
            patch.object(
                gnn_check.db, "get_gnn_user_embeddings", return_value=embeddings
            ) as lookup,
            patch.object(
                gnn_check.db,
                "get_active_gnn_scoring_policy",
                return_value={
                    "policy_id": 5,
                    "policy_version": 2,
                    "inference_enabled": True,
                    "stale_embedding_days": 14,
                    "thresholds": {
                        "low": 25, "medium": 45,
                        "high": 65, "critical": 85,
                    },
                },
            ),
        ):
            result = gnn_check._assess_gnn_inner(details, tenant_id=7)

        self.assertTrue(result["model_available"])
        self.assertEqual(lookup.call_args.kwargs["user_ids"], [1, 2])
        self.assertEqual(module.input_shape, (1, 2 * 4 + len(FEATURES)))
        self.assertNotIn("approver", " ".join(result["warning_flags"]).lower())

    def test_decoder_uses_the_gnn_folder(self):
        with self.assertRaisesRegex(ValueError, "serving version"):
            gnn_check._head_blob_name(7)
        self.assertEqual(
            gnn_check._head_blob_name(7, "gnn-v2-selected"),
            "tenant_7/gnn/gnn-v2-selected/serving/decoder.pt",
        )

    def test_behavior_population_accepts_new_and_legacy_artifacts(self):
        self.assertIn(
            ("Pending", "Approved", "Paid", "Rejected"),
            gnn_check._SUPPORTED_BEHAVIOR_STATUS_CONTRACTS,
        )
        self.assertIn(
            ("Pending", "Approved", "Paid"),
            gnn_check._SUPPORTED_BEHAVIOR_STATUS_CONTRACTS,
        )

    def test_decoder_rebuild_uses_two_participant_embeddings(self):
        source = torch.nn.Sequential(
            torch.nn.Linear(2 * 4 + len(FEATURES), 64),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.2),
            torch.nn.Linear(64, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 1),
        )
        rebuilt = gnn_check._build_decoder({
            "emb_dim": 4,
            "nomination_feature_columns": FEATURES,
            "decoder_hidden": [64, 32],
            "decoder_state_dict": source.state_dict(),
        })

        self.assertEqual(rebuilt[0].in_features, 2 * 4 + len(FEATURES))

    def test_idle_decoder_is_evicted(self):
        gnn_check._head_cache[(7, "old")] = ({"model_version": "old"}, 10.0)
        gnn_check._head_cache[(8, "recent")] = ({"model_version": "recent"}, 95.0)

        with patch.dict(os.environ, {"MODEL_IDLE_TTL_SECONDS": "30"}):
            evicted = gnn_check._evict_idle_heads(now=100.0)

        self.assertEqual(evicted, 1)
        self.assertNotIn((7, "old"), gnn_check._head_cache)
        self.assertIn((8, "recent"), gnn_check._head_cache)

    def test_tenant_policy_can_disable_live_gnn_without_removing_model(self):
        with (
            patch.object(
                gnn_check.db,
                "get_active_gnn_scoring_policy",
                return_value={
                    "policy_id": 5,
                    "policy_version": 2,
                    "inference_enabled": False,
                },
            ),
            patch.object(gnn_check, "_get_head") as get_head,
        ):
            result = gnn_check._assess_gnn_inner({}, tenant_id=7)

        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "DISABLED_BY_POLICY")
        self.assertEqual(result["scoring_policy_version"], 2)
        get_head.assert_not_called()

    def test_missing_active_policy_is_unavailable_without_loading_a_model(self):
        with (
            patch.object(
                gnn_check.db, "get_active_gnn_scoring_policy", return_value=None
            ),
            patch.object(gnn_check, "_get_head") as get_head,
        ):
            result = gnn_check._assess_gnn_inner({}, tenant_id=7)

        self.assertFalse(result["model_available"])
        self.assertEqual(result["unavailable_reason"], "NO_ACTIVE_POLICY")
        get_head.assert_not_called()

    def test_v2_candidate_features_match_the_serving_contract(self):
        head = {
            "feature_schema_version": "gnn-v2",
            "nomination_feature_columns": V2_FEATURES,
            "nomination_scaler_mean": [0.0] * len(V2_FEATURES),
            "nomination_scaler_std": [1.0] * len(V2_FEATURES),
            "amount_mean": 0.0,
            "amount_std": 0.0,
            "category_amount_stats": {
                "global": {"median": 100.0, "scale": 20.0},
                "categories": {"9": {"median": 200.0, "scale": 50.0}},
            },
        }
        values = gnn_check._nomination_features({
            "amount": 300.0,
            "category_id": 9,
            "nomination_date": date(2026, 9, 9),
            "status": "Paid",
        }, head)[0]

        self.assertAlmostEqual(values[0], np.log1p(300.0), places=5)
        self.assertAlmostEqual(values[1], 2.0, places=5)
        self.assertEqual(values[2], 0.0)
        self.assertEqual(values[-1], 0.0)

    def test_causal_schema_adds_only_strictly_prior_raw_topology(self):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        head = {
            "feature_schema_version": gnn_check.CAUSAL_FEATURE_SCHEMA_VERSION,
            "nomination_feature_columns": CAUSAL_FEATURES,
            "nomination_scaler_mean": [0.0] * len(CAUSAL_FEATURES),
            "nomination_scaler_std": [1.0] * len(CAUSAL_FEATURES),
            "amount_mean": 0.0,
            "amount_std": 0.0,
            "category_amount_stats": {
                "global": {"median": 100.0, "scale": 20.0},
                "categories": {},
            },
            "causal_context_window_days": 365,
        }
        history = [
            {
                "NominationId": 10,
                "NominatorId": 1,
                "BeneficiaryId": 2,
                "CreatedAt": now - timedelta(minutes=2),
            },
            {
                "NominationId": 20,
                "NominatorId": 2,
                "BeneficiaryId": 3,
                "CreatedAt": now - timedelta(minutes=1),
            },
            {
                "NominationId": 40,
                "NominatorId": 3,
                "BeneficiaryId": 1,
                "CreatedAt": now + timedelta(minutes=1),
            },
        ]

        values = gnn_check._nomination_features(
            {
                "nomination_id": 30,
                "nominator_id": 3,
                "beneficiary_id": 1,
                "amount": 100.0,
                "category_id": 1,
                "nomination_date": now,
            },
            head,
            gnn_check.causal_context_values(
                history,
                {
                    "NominationId": 30,
                    "NominatorId": 3,
                    "BeneficiaryId": 1,
                    "CreatedAt": now,
                },
                window_days=365,
            ),
        )[0]

        path_index = CAUSAL_FEATURES.index("LogReverseTwoHopPathCount")
        directed_index = CAUSAL_FEATURES.index("LogPriorDirectedPairCount")
        self.assertGreater(values[path_index], 0.0)
        self.assertEqual(values[directed_index], 0.0)

    def test_feature_bundle_records_every_named_input_before_and_after_scaling(self):
        head = {
            "feature_schema_version": "gnn-v2",
            "nomination_feature_columns": V2_FEATURES,
            "nomination_scaler_mean": [1.0] * len(V2_FEATURES),
            "nomination_scaler_std": [2.0] * len(V2_FEATURES),
            "amount_mean": 0.0,
            "amount_std": 0.0,
            "category_amount_stats": {
                "global": {"median": 100.0, "scale": 20.0},
                "categories": {},
            },
        }

        model_row, audit = gnn_check._nomination_feature_bundle(
            {
                "amount": 300.0,
                "category_id": 9,
                "nomination_date": date(2026, 9, 9),
            },
            head,
        )

        assert [item["name"] for item in audit["features"]] == V2_FEATURES
        assert len(audit["features"]) == model_row.shape[1]
        self.assertAlmostEqual(
            audit["features"][0]["pre_scaler_value"], np.log1p(300.0), places=6
        )
        self.assertAlmostEqual(
            audit["features"][0]["model_input_value"], model_row[0, 0]
        )


if __name__ == "__main__":
    unittest.main()
