"""Tenant-safe model representation blob access."""

import json
import unittest
from unittest.mock import patch

from utils import model_artifacts


class ModelArtifactTests(unittest.TestCase):
    @patch("utils.model_artifacts._download")
    def test_rf_manifest_uses_server_constructed_tenant_blob_name(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "tabular_integrity_model",
            "tenant_id": 7,
            "model_version": "tabular-v1-selected",
            "selection": {"selected_architecture": "random_forest"},
        }).encode()

        result = model_artifacts.get_manifest(
            tenant_id=7,
            component="rf",
            model_version="tabular-v1-selected",
        )

        self.assertEqual(result["tenant_id"], 7)
        self.assertEqual(
            result["selection"]["selected_architecture"], "random_forest"
        )
        self.assertEqual(
            download.call_args.args[0],
            "tenant_7/tabular/tabular-v1-selected/manifest.json",
        )

    @patch("utils.model_artifacts._download")
    def test_gnn_manifest_rejects_a_tenant_mismatch(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "graph_neural_network",
            "tenant_id": 8,
            "model_version": "gnn-v2-selected",
        }).encode()

        with self.assertRaisesRegex(ValueError, "tenant"):
            model_artifacts.get_manifest(
                tenant_id=7,
                component="gnn",
                model_version="gnn-v2-selected",
            )
        self.assertEqual(
            download.call_args.args[0],
            "tenant_7/gnn/gnn-v2-selected/manifest.json",
        )

    @patch("utils.model_artifacts._download")
    def test_gnn_manifest_uses_the_active_versioned_bundle(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "graph_neural_network",
            "tenant_id": 7,
            "model_version": "gnn-v2-selected",
        }).encode()

        model_artifacts.get_manifest(
            tenant_id=7,
            component="gnn",
            model_version="gnn-v2-selected",
        )

        self.assertEqual(
            download.call_args.args[0],
            "tenant_7/gnn/gnn-v2-selected/manifest.json",
        )

    @patch("utils.model_artifacts._download")
    def test_gnn_manifest_rejects_an_invalid_version_path(self, download):
        with self.assertRaisesRegex(ValueError, "version"):
            model_artifacts.get_manifest(
                tenant_id=7,
                component="gnn",
                model_version="../tenant_8/other",
            )
        download.assert_not_called()

    @patch("utils.model_artifacts._download")
    def test_gnn_manifest_rejects_a_version_mismatch(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "graph_neural_network",
            "tenant_id": 7,
            "model_version": "gnn-v2-other",
        }).encode()

        with self.assertRaisesRegex(ValueError, "version"):
            model_artifacts.get_manifest(
                tenant_id=7,
                component="gnn",
                model_version="gnn-v2-selected",
            )

    @patch("utils.model_artifacts._download")
    def test_rf_visualization_uses_server_constructed_tenant_blob_name(self, download):
        download.side_effect = [json.dumps({
            "schema_version": 1,
            "artifact_type": "tabular_integrity_model",
            "tenant_id": 9,
            "model_version": "tabular-v1-selected",
        }).encode(), b"png"]

        self.assertEqual(
            model_artifacts.get_rf_visualization(9, "tabular-v1-selected"),
            b"png",
        )
        self.assertEqual(
            download.call_args_list[0].args[0],
            "tenant_9/tabular/tabular-v1-selected/manifest.json",
        )
        self.assertEqual(
            download.call_args_list[1].args[0],
            "tenant_9/tabular/tabular-v1-selected/serving/score_distribution.png",
        )

    @patch("utils.model_artifacts._download")
    def test_rf_visualization_requires_a_registered_version(self, download):
        self.assertIsNone(model_artifacts.get_rf_visualization(9, None))
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
