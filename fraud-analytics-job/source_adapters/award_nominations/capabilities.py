"""Capabilities guaranteed by the Award Nomination adapter."""

from source_adapters.contracts import SourceCapability

AWARD_NOMINATION_CAPABILITIES = frozenset(capability.value for capability in (
    SourceCapability.DIRECTED_ACTOR_PAIR,
    SourceCapability.AMOUNT,
    SourceCapability.CURRENCY,
    SourceCapability.CATEGORY,
    SourceCapability.TEXT,
    SourceCapability.EVENT_STATUS,
    SourceCapability.REVIEWED_OUTCOMES,
    SourceCapability.BEHAVIOR_LABELS,
))
