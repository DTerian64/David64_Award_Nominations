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
            "artifact_type": "random_forest",
            "tenant_id": 7,
            "models": {"p2p": {"available": True}, "approver": {"available": True}},
            "training": {"p2p_auc": 0.8, "appr_auc": 0.7},
        }).encode()

        result = model_artifacts.get_manifest(tenant_id=7, component="rf")

        self.assertEqual(result["tenant_id"], 7)
        self.assertEqual(list(result["models"]), ["p2p"])
        self.assertEqual(result["retired_components"], ["approver"])
        self.assertNotIn("appr_auc", result["training"])
        self.assertEqual(download.call_args.args[0], "random_forest_tenant_7.manifest.json")

    @patch("utils.model_artifacts._download")
    def test_gnn_manifest_rejects_a_tenant_mismatch(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "graph_neural_network",
            "tenant_id": 8,
        }).encode()

        with self.assertRaisesRegex(ValueError, "tenant"):
            model_artifacts.get_manifest(tenant_id=7, component="gnn")

    @patch("utils.model_artifacts._download")
    def test_rf_visualization_uses_server_constructed_tenant_blob_name(self, download):
        download.side_effect = [json.dumps({
            "schema_version": 1,
            "artifact_type": "random_forest",
            "tenant_id": 9,
            "models": {"p2p": {"available": True}},
        }).encode(), b"png"]

        self.assertEqual(model_artifacts.get_rf_visualization(9), b"png")
        self.assertEqual(download.call_args_list[0].args[0], "random_forest_tenant_9.manifest.json")
        self.assertEqual(download.call_args_list[1].args[0], "random_forest_tenant_9.png")

    @patch("utils.model_artifacts._download")
    def test_rf_visualization_hides_a_legacy_approver_chart(self, download):
        download.return_value = json.dumps({
            "schema_version": 1,
            "artifact_type": "random_forest",
            "tenant_id": 9,
            "models": {"p2p": {}, "approver": {}},
        }).encode()

        self.assertIsNone(model_artifacts.get_rf_visualization(9))
        self.assertEqual(download.call_count, 1)


if __name__ == "__main__":
    unittest.main()
