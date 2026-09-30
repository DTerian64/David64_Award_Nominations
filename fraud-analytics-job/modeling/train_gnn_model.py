"""Compatibility alias for the Integrity Sentinel GNN training stage."""

import sys

from integrity_sentinel import train_gnn_model as _implementation

sys.modules[__name__] = _implementation
