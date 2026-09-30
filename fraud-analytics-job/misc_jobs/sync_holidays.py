"""Compatibility alias for Award Nominations holiday synchronization."""

import sys

from source_adapters.award_nominations import sync_holidays as _implementation

sys.modules[__name__] = _implementation
