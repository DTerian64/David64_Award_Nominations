"""Failure classes mapped to explicit Service Bus settlement outcomes."""

class PermanentExtensionError(RuntimeError):
    """The request cannot succeed without changing its data or artifacts."""

class TransientExtensionError(RuntimeError):
    """The request may succeed on a later Service Bus delivery."""
