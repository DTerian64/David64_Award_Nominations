"""The active RF artifact and historical scorer are nomination-only."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from modeling import train_rf_model


GRAPH_DERIVED_FEATURES = {
    "GraphCycleFlag",
    "GraphReciprocalFlag",
    "GraphClusterSize",
    "SuperNominatorFlag",
}


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


def test_rf_feature_contract_excludes_graph_analytics_outputs():
    assert GRAPH_DERIVED_FEATURES.isdisjoint(train_rf_model.P2P_FEATURE_COLUMNS)
    assert "HasReciprocalNomination" in train_rf_model.P2P_FEATURE_COLUMNS
    assert "TransactionalPhraseScore" in train_rf_model.P2P_FEATURE_COLUMNS


def test_transactional_phrase_score_is_continuous_and_capped():
    score = train_rf_model.transactional_phrase_score

    assert score("Consistently exceeded expectations.") == 0.0
    assert score("You helped me, so I owe them in return.") == 0.5
    assert score(
        "You helped me, saved my deadline and my project; I owe them, "
        "will nominate them back in return."
    ) == 1.0


def test_rf_training_query_does_not_read_graph_snapshots():
    connection = _Connection()
    empty = pd.DataFrame()

    with (
        patch.object(train_rf_model, "get_db_connection", return_value=connection),
        patch.object(train_rf_model.pd, "read_sql", return_value=empty) as read_sql,
        patch.object(train_rf_model.labels_mod, "load_labels", return_value=empty),
        patch.object(
            train_rf_model.labels_mod,
            "attach_training_labels",
            side_effect=lambda frame, _labels: frame,
        ),
    ):
        train_rf_model.load_data(tenant_id=3)

    query = read_sql.call_args.args[0]
    assert "UserGraphFlags" not in query
    assert "ApproverPairFlags" not in query


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
            "feature_contract": train_rf_model.RF_FEATURE_CONTRACT,
            "p2p_model": classifier,
            "p2p_feature_columns": ["Amount", "PairNominationCount"],
            "amount_mean": 100.0,
            "amount_std": 25.0,
            "category_fraud_rate": {},
            "global_fraud_rate": 0.1,
            "embed_model_name": "test-embedding",
            "transactional_phrase_rule_version": (
                train_rf_model.TRANSACTIONAL_PHRASE_RULE_VERSION
            ),
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
        assert manifest["data_profile"]["feature_contract"] == "rf-native-v3"
        assert manifest["data_profile"]["transactional_phrase_rule_version"] == (
            train_rf_model.TRANSACTIONAL_PHRASE_RULE_VERSION
        )


def test_rf_cold_start_does_not_read_graph_findings():
    rows = []
    for index in range(60):
        row = {
            column: float(index % (position + 2))
            for position, column in enumerate(train_rf_model.P2P_FEATURE_COLUMNS)
        }
        row["IsFraud"] = 0
        rows.append(row)

    with patch.object(
        train_rf_model,
        "get_db_connection",
        side_effect=AssertionError("RF cold-start must not query Graph Analytics"),
    ):
        result = train_rf_model.bootstrap_fraud_labels(
            pd.DataFrame(rows), tenant_id=1
        )

    assert result is not None
    assert int(result["IsFraud"].sum()) >= 5


def test_rf_visualization_title_uses_tenant_name():
    class _Axis:
        def __init__(self):
            self.title = None

        def hist(self, *_args, **_kwargs):
            pass

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, _label):
            pass

        def set_title(self, title):
            self.title = title

        def legend(self):
            pass

    axis = _Axis()
    frame = pd.DataFrame({"IsFraud": [0, 1]})

    with (
        patch.object(train_rf_model.plt, "subplots", return_value=(object(), axis)),
        patch.object(train_rf_model.plt, "tight_layout"),
        patch.object(train_rf_model.plt, "savefig"),
        patch.object(train_rf_model.plt, "close"),
        patch.object(train_rf_model, "_upload_artefact"),
    ):
        train_rf_model.create_visualizations(
            frame,
            np.array([0.1, 0.9]),
            tenant_id=3,
            tenant_name="Contoso Awards",
        )

    assert axis.title == "Nomination Fraud Score Distribution — Contoso Awards"
