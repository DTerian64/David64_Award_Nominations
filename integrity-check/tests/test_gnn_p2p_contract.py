"""Regression tests for the nomination-only GNN serving contract."""

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch


os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import gnn_check


FEATURES = [
    "Amount",
    "AmountZScore",
    "DayOfWeek",
    "Month",
    "IsWeekend",
    "IsHighAmount",
]


class _RecordingModule(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.input_shape = None

    def forward(self, values):
        self.input_shape = tuple(values.shape)
        return torch.tensor([0.0])


class GnnP2PContractTests(unittest.TestCase):
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
            patch.object(gnn_check.db, "get_tenant_integrity_config", return_value={}),
        ):
            result = gnn_check._assess_gnn_inner(details, tenant_id=7)

        self.assertTrue(result["model_available"])
        self.assertEqual(lookup.call_args.kwargs["user_ids"], [1, 2])
        self.assertEqual(module.input_shape, (1, 2 * 4 + len(FEATURES)))
        self.assertNotIn("approver", " ".join(result["warning_flags"]).lower())

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


if __name__ == "__main__":
    unittest.main()
