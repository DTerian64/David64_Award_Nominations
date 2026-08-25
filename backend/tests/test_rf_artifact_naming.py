"""Tests for rolling-compatible Random Forest artifact naming.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest tests.test_rf_artifact_naming -v
"""

import unittest

import fraud_ml


class RfArtifactNamingTests(unittest.TestCase):
    def test_canonical_name_is_random_forest(self):
        self.assertEqual(
            fraud_ml.FraudDetector._blob_name(3),
            "random_forest_tenant_3.pkl",
        )

if __name__ == "__main__":
    unittest.main()
