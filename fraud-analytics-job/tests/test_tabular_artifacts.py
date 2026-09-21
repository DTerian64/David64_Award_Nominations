"""T5 immutable Tabular candidate and serving bundle tests."""

from __future__ import annotations

import json
import pickle

import pytest

from modeling import train_tabular_model
from modeling.tabular import evaluate_tabular_candidates
from modeling.tabular.artifacts import write_tabular_bundle
from modeling.tabular.serving import fit_selected_for_serving
from tests.test_tabular_candidate_selection import _t4_policy
from tests.test_tabular_random_forest_candidate import _feature_dataset


def test_bundle_contains_both_candidates_and_selected_serving_refit(tmp_path):
    dataset = _feature_dataset()
    policy = _t4_policy()
    evaluation = evaluate_tabular_candidates(dataset, policy)
    selected = evaluation.selection.selected_architecture
    serving = fit_selected_for_serving(dataset, selected, policy)

    bundle_dir, artifacts = write_tabular_bundle(
        output_dir=tmp_path,
        tenant_id=5,
        tenant_name="Synthetics Inc",
        model_version="tabular-v1-test",
        feature_dataset=dataset,
        evaluation=evaluation,
        serving_fit=serving,
        training_policy=policy,
        embed_model_name="test-embedding-model",
    )

    assert (bundle_dir / "manifest.json").is_file()
    for architecture in ("random_forest", "tabular_mlp"):
        candidate = bundle_dir / "candidates" / architecture
        assert (candidate / "model.pkl").is_file()
        assert (candidate / "metrics.json").is_file()
        assert (candidate / "score_distribution.png").is_file()
    serving_path = bundle_dir / "serving" / "model.pkl"
    assert serving_path.is_file()
    evaluation_chart = (
        bundle_dir / "evaluation" / "selected_candidate_score_distribution.png"
    )
    assert evaluation_chart.is_file()
    assert not (bundle_dir / "serving" / "score_distribution.png").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert manifest["artifact_type"] == "tabular_integrity_model"
    assert manifest["tenant_id"] == 5
    assert manifest["selection"]["selected_architecture"] == selected
    assert set(manifest["candidates"]) == {"random_forest", "tabular_mlp"}
    for architecture in ("random_forest", "tabular_mlp"):
        fit = manifest["candidates"][architecture]["fit"]
        candidate = evaluation.candidates[architecture]
        assert fit["fit_scope"] == "TEMPORAL_TRAINING_PARTITION"
        assert fit["training_rows"] == candidate.metrics["training_rows"]
        assert fit["fraud_rows"] == candidate.metrics["training_fraud_rows"]
        assert fit["evaluation_status"] == "OUT_OF_TIME_HOLDOUT"
        assert fit["evaluation_rows"] == candidate.metrics["evaluation_rows"]
        assert fit["model_statistics"]["model_family"] in {
            "TREE_ENSEMBLE",
            "FEED_FORWARD_NEURAL_NETWORK",
        }
        permutation = manifest["candidates"][architecture][
            "permutation_importance"
        ]
        assert permutation["holdout_row_count"] == len(
            evaluation.candidates[architecture].evaluation_targets
        )
        assert permutation["repeats"] == policy.permutation_importance_repeats
        assert {row["name"] for row in permutation["features"]} == set(
            dataset.schema.feature_columns
        )
        assert "permutation_importance" not in manifest["candidates"][
            architecture
        ]["metrics"]
    importance = manifest["candidates"]["random_forest"]["feature_importance"]
    assert [row["importance"] for row in importance] == sorted(
        [row["importance"] for row in importance], reverse=True
    )
    assert {row["name"] for row in importance} == set(
        dataset.schema.feature_columns
    )
    assert sum(row["importance"] for row in importance) == pytest.approx(1.0)
    rf_statistics = manifest["candidates"]["random_forest"]["fit"][
        "model_statistics"
    ]
    assert rf_statistics["tree_count"] == policy.rf_estimators
    assert rf_statistics["total_node_count"] == evaluation.candidates[
        "random_forest"
    ].metrics["tree_node_count"]
    assert rf_statistics["total_leaf_count"] > 0
    assert rf_statistics["maximum_observed_depth"] <= policy.rf_max_depth
    assert manifest["serving"]["architecture"] == selected
    serving_fit = manifest["serving"]["fit"]
    assert serving_fit["fit_scope"] == "ALL_MATURED_LABELS"
    assert serving_fit["selected_from_candidate"] == selected
    assert serving_fit["training_rows"] == serving.training_rows
    assert serving_fit["fraud_rows"] == serving.training_fraud_rows
    assert serving_fit["evaluation_status"] == "NOT_APPLICABLE_AFTER_FULL_REFIT"
    assert manifest["evaluation"] == {
        "architecture": selected,
        "fit_scope": "TEMPORAL_TRAINING_PARTITION",
        "evaluation_scope": "OUT_OF_TIME_HOLDOUT",
        "visualization_path": (
            "evaluation/selected_candidate_score_distribution.png"
        ),
        "source_candidate_model_path": f"candidates/{selected}/model.pkl",
        "source_candidate_metrics_path": f"candidates/{selected}/metrics.json",
    }
    assert all(item[0].is_file() for item in artifacts)
    assert b"modeling.tabular" not in serving_path.read_bytes()

    with serving_path.open("rb") as stream:
        payload = pickle.load(stream)
    assert payload["tenant_id"] == 5
    assert payload["architecture"] == selected
    assert payload["feature_schema_id"] == dataset.schema.schema_id
    assert payload["training_lineage"] == serving_fit
    assert type(payload["model"]) is type(serving.model)
    assert payload["preprocessing"]["feature_columns"] == list(
        dataset.schema.feature_columns
    )
    assert payload["preprocessing"]["imputer"] is not None
    assert payload["preprocessing"]["scaler"] is not None


def test_publish_uses_tenant_first_immutable_prefix(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    serving = bundle / "serving" / "model.pkl"
    manifest = bundle / "manifest.json"
    serving.parent.mkdir(parents=True)
    serving.write_bytes(b"model")
    manifest.write_text("{}", encoding="utf-8")
    uploads = []

    def record_upload(path, *, blob_folder, blob_filename):
        uploads.append((path, blob_folder, blob_filename))
        return True

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "test-storage")
    monkeypatch.setattr(train_tabular_model, "upload_artifact", record_upload)

    train_tabular_model._publish_bundle(
        tenant_id=5,
        model_version="tabular-v1-test",
        bundle_dir=bundle,
        artifacts=[(serving, "serving_model"), (manifest, "manifest")],
    )

    assert uploads == [
        (
            serving,
            "tenant_5/tabular/tabular-v1-test/serving",
            "model.pkl",
        ),
        (
            manifest,
            "tenant_5/tabular/tabular-v1-test",
            "manifest.json",
        ),
    ]


def test_incomplete_publish_does_not_allow_activation(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    model = bundle / "serving" / "model.pkl"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "test-storage")
    monkeypatch.setattr(
        train_tabular_model,
        "upload_artifact",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(RuntimeError, match="not completely uploaded"):
        train_tabular_model._publish_bundle(
            tenant_id=5,
            model_version="tabular-v1-test",
            bundle_dir=bundle,
            artifacts=[(model, "serving_model")],
        )
