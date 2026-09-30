"""Compatibility alias for Award Nominations forecasting."""

import sys

from source_adapters.award_nominations import forecast_models as _implementation

sys.modules[__name__] = _implementation
