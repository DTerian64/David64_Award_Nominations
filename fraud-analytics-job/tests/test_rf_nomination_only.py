"""The active RF artifact and historical scorer are nomination-only."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from modeling import train_rf_model


class _Scaler:
    def transform(self, values):
        return values


class _Model:
    def predict_proba(self, values):
        fraud = np.full(len(values), 0.25)
        return np.column_stack((1 - fraud, fraud))


class _Cursor:
    def __init__(self):
        self.statements = []
        self.fast_executemany = False

    def execute(self, statement):
        self.statements.append(statement)

    def executemany(self, statement, _rows):
        self.statements.append(statement)

    def close(self):
        pass


class _Connection:
    def __init__(self):
        self.cursor_instance = _Cursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass

    def close(self):
        pass


def test_historical_rf_scoring_never_writes_approver_scores():
    frame = pd.DataFrame([{column: 0.0 for column in train_rf_model.P2P_FEATURE_COLUMNS}])
    frame["NominationId"] = [101]
    model_data = {
        "p2p_model": _Model(),
        "p2p_scaler": _Scaler(),
        "p2p_feature_columns": train_rf_model.P2P_FEATURE_COLUMNS,
    }
    connection = _Connection()

    with patch.object(train_rf_model, "get_db_connection", return_value=connection):
        train_rf_model.score_and_save_historical(frame, model_data, tenant_id=1)

    sql = "\n".join(connection.cursor_instance.statements)
    assert "P2P_FraudScores" in sql
    assert "Appr_FraudScores" not in sql


def test_rf_manifest_contains_only_the_nomination_model():
    classifier = RandomForestClassifier(n_estimators=2, max_depth=2, random_state=42)
    classifier.fit([[0, 0], [0, 1], [1, 0], [1, 1]], [0, 0, 1, 1])

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pkl_path = root / "random_forest_tenant_1.pkl"
        png_path = root / "random_forest_tenant_1.png"
        pkl_path.write_bytes(b"model")
        png_path.write_bytes(b"chart")
        model_data = {
            "model_version": "rf-test-t1",
            "p2p_model": classifier,
            "p2p_feature_columns": ["Amount", "PairNominationCount"],
            "amount_mean": 100.0,
            "amount_std": 25.0,
            "category_fraud_rate": {},
            "global_fraud_rate": 0.1,
            "embed_model_name": "test-embedding",
        }

        with patch.object(train_rf_model, "OUTPUT_DIR", root):
            manifest_path = train_rf_model._write_rf_manifest(
                tenant_id=1,
                model_data=model_data,
                training_metrics={"p2p_auc": 0.8},
                pkl_path=pkl_path,
                png_path=png_path,
            )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert list(manifest["models"]) == ["p2p"]
        assert "approver" not in json.dumps(manifest).lower()
