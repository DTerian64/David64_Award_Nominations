"""Tests for rolling-compatible Random Forest artifact naming.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\backend"
    python -m unittest tests.test_rf_artifact_naming -v
"""

import unittest

from utils.rf_model_cache import RandomForestModelCache


class RfArtifactNamingTests(unittest.TestCase):
    def test_canonical_name_is_tenant_scoped_tabular_serving_model(self):
        self.assertEqual(
            RandomForestModelCache._blob_name(3, "tabular-v1-run"),
            "tenant_3/tabular/tabular-v1-run/serving/model.pkl",
        )

if __name__ == "__main__":
    unittest.main()
