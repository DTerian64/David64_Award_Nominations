"""The production trainer and worker use identical bounded prior-only inputs."""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "integrity-check"))
from inference import random_forest_check as live
from utils import db
sys.path.append(str(root / "fraud-analytics-job"))
from systems.award_nominations.features.tabular.award_nomination_tabular_v1 import (
    TABULAR_V1_FEATURE_COLUMNS, add_semantic_features, extract_features,
)


class Identity:
    def transform(self, value):
        return value


class Encoder:
    def encode(self, texts, **kwargs):
        vectors = np.array([[len(text) + 1, sum(map(ord, text)) % 19 + 1, 1] for text in texts], dtype=float)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def artifact(architecture="random_forest"):
    return {"tenant_id": 5, "artifact_type": "tabular_integrity_model",
            "architecture": architecture, "feature_schema_id": "award-nomination-tabular:tabular-v2",
            "history_window_days": 180, "history_feature_contract": "tabular-causal-window-v1",
            "feature_columns": list(TABULAR_V1_FEATURE_COLUMNS), "model": object(),
            "preprocessing": {"imputer": Identity(), "scaler": Identity(),
                              "category_fraud_rate": {}, "global_fraud_rate": 0.0}}


@pytest.mark.parametrize("architecture", ["random_forest", "tabular_mlp"])
def test_full_feature_vector_matches_training_with_expiration_and_semantics(architecture):
    time = datetime(2026, 9, 26)
    rows = [
        {"NominationId": 1, "NominatorId": 1, "BeneficiaryId": 2, "Amount": 9000,
         "NominationDate": time - timedelta(days=181), "NominationDescription": "Old"},
        {"NominationId": 2, "NominatorId": 1, "BeneficiaryId": 2, "Amount": 100,
         "NominationDate": time - timedelta(days=180), "NominationDescription": "Boundary"},
        {"NominationId": 3, "NominatorId": 2, "BeneficiaryId": 1, "Amount": 300,
         "NominationDate": time - timedelta(days=1), "NominationDescription": "Past authored text"},
        {"NominationId": 4, "NominatorId": 1, "BeneficiaryId": 2, "Amount": 200,
         "NominationDate": time, "NominationDescription": "Target helped me"},
        {"NominationId": 5, "NominatorId": 1, "BeneficiaryId": 2, "Amount": 10000,
         "NominationDate": time + timedelta(days=1), "NominationDescription": "Future"},
    ]
    frame = pd.DataFrame(rows)
    encoded = add_semantic_features(frame, Encoder(), 180)
    training, _, _ = extract_features(encoded, 180)
    target = rows[3]
    details = {"nomination_id": 4, "nominator_id": 1, "beneficiary_id": 2, "amount": 200,
               "nomination_date": time.replace(tzinfo=timezone.utc), "description": target["NominationDescription"]}
    with patch.object(live.db, "get_tabular_history_rows", return_value=rows[1:3]) as get_history, \
         patch.object(live, "_get_embed_model", return_value=Encoder()), \
         patch.object(live.db, "get_nominator_history", side_effect=AssertionError("Legacy history must not be used")):
        scaled, values, _ = live._build_features(details, artifact(architecture))
    np.testing.assert_allclose(scaled[0], training.loc[3, list(TABULAR_V1_FEATURE_COLUMNS)].to_numpy(float))
    assert values["PairNominationCount"] == 1
    assert values["HasReciprocalNomination"] == 1
    assert values["DescriptionCosineSim"] > 0
    assert get_history.call_args.kwargs["window_days"] == 180
    # Changing future nominations must not change any earlier model input.
    frame.loc[4, "Amount"] = 999999
    frame.loc[4, "NominationDescription"] = "Completely different"
    changed, _, _ = extract_features(add_semantic_features(frame, Encoder(), 180), 180)
    np.testing.assert_allclose(changed.loc[3, list(TABULAR_V1_FEATURE_COLUMNS)].to_numpy(float), scaled[0])


def test_new_artifacts_require_positive_recorded_window_and_contract():
    data = artifact()
    assert live._is_independent_rf_artifact(data)
    for days in (None, 0, -1, True):
        data["history_window_days"] = days
        assert not live._is_independent_rf_artifact(data)
    data["history_window_days"] = 180
    data["history_feature_contract"] = "incorrect"
    assert not live._is_independent_rf_artifact(data)


def test_sql_is_tenant_scoped_bounded_prior_only_and_preserves_hrbp_rejections():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.description = [(name,) for name in ("NominationId", "NominatorId", "BeneficiaryId", "Amount", "NominationDate", "NominationDescription")]
    cursor.fetchall.return_value = [(1, 2, 3, 100, datetime(2026, 1, 1), "Text")]
    @contextmanager
    def connection():
        yield conn
    with patch.object(db, "_get_conn", connection):
        result = db.get_tabular_history_rows(5, target_nomination_id=9,
            target_time=datetime(2026, 9, 26, tzinfo=timezone.utc), window_days=180)
    statement, params = cursor.execute.call_args.args
    assert "u.TenantId=?" in statement
    assert "n.NominationDate >= DATEADD" in statement
    assert "n.NominationId < ?" in statement
    assert "PendingHRBPReview" in statement
    assert "Fraud Detection (Description)" in statement
    assert "HRBP Review" not in statement
    assert params[0:2] == (5, 180)
    assert params[-1] == 9
    assert params[2].tzinfo is None
    assert result[0]["NominationId"] == 1
