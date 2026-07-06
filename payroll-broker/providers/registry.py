"""
registry.py — Provider registry
================================
Maps payroll_providers.name → PayrollProvider instance.

The worker and webhook routers look up the right provider object here at
runtime using the name stored in the payroll_providers DB row.

To add a new provider:
  1. Implement providers/<name>/provider.py
  2. Import and register it below.
"""

from providers.base import PayrollProvider
from providers.gusto import GustoProvider
from providers.rippling import RipplingProvider

PROVIDER_REGISTRY: dict[str, PayrollProvider] = {
    "gusto":    GustoProvider(),
    "rippling": RipplingProvider(),
    # "workday": WorkdayProvider(),
    # "adp":     AdpProvider(),
}
