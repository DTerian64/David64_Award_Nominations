"""Tests for integrity-check's rolling-compatible RF artifact lookup.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest discover -s tests -p "test_rf_artifact_naming.py" -v
"""

import os
import pickle
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import random_forest_check


class _Download:
    def __init__(self, payload):
        self.payload = payload

    def readall(self):
        return self.payload


class _BlobClient:
    def __init__(self, name, attempts, payload):
        self.name = name
        self.attempts = attempts
        self.payload = payload

    def download_blob(self):
        self.attempts.append(self.name)
        return _Download(self.payload)


class _BlobService:
    def __init__(self, attempts, payload):
        self.attempts = attempts
        self.payload = payload

    def get_blob_client(self, *, container, blob):
        return _BlobClient(blob, self.attempts, self.payload)


class RfArtifactNamingTests(unittest.TestCase):
    def test_integrity_check_loads_canonical_blob_name(self):
        attempts = []
        payload = pickle.dumps({
            "model_version": "rf-test",
            "feature_contract": "rf-native-v3",
            "transactional_phrase_rule_version": "transactional-phrase-score-v1",
            "p2p_feature_columns": [
                "Amount", "HasReciprocalNomination", "TransactionalPhraseScore",
            ],
        })
        fake_service = _BlobService(attempts, payload)

        with patch.object(random_forest_check, "_STORAGE_KEY", "test-key"), patch(
            "azure.storage.blob.BlobServiceClient.from_connection_string",
            return_value=fake_service,
        ):
            result = random_forest_check._stream_from_blob(3)

        self.assertEqual(result["model_version"], "rf-test")
        self.assertEqual(attempts, ["random_forest_tenant_3.pkl"])


if __name__ == "__main__":
    unittest.main()
