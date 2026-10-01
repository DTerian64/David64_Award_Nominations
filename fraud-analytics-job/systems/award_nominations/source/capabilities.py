"""Capabilities guaranteed by the Award Nomination adapter."""

from source_adapters.contracts import SourceCapability

AWARD_NOMINATION_SOURCE_CAPABILITIES = frozenset(capability.value for capability in (
    SourceCapability.DIRECTED_ACTOR_PAIR,
    SourceCapability.AMOUNT,
    SourceCapability.CURRENCY,
    SourceCapability.CATEGORY,
    SourceCapability.TEXT,
    SourceCapability.EVENT_STATUS,
))

AWARD_NOMINATION_CAPABILITIES = AWARD_NOMINATION_SOURCE_CAPABILITIES | frozenset(
    {
        SourceCapability.REVIEWED_OUTCOMES.value,
        SourceCapability.BEHAVIOR_LABELS.value,
    }
)
