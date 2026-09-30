"""Compatibility alias for the Integrity Sentinel Graph stage."""

import sys

from integrity_sentinel import graph_analytics as _implementation

sys.modules[__name__] = _implementation
