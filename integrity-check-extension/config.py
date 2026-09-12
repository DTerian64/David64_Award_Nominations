"""Process configuration. Tenant behavior remains policy-driven in SQL."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service_bus_fqns: str
    service_bus_topic: str
    service_bus_subscription: str
    storage_account: str
    model_container: str
    max_message_count: int = 1
    max_wait_seconds: int = 5
    max_auto_lock_renewal_seconds: int = 900
    max_artifact_bytes: int = 1_073_741_824

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            service_bus_fqns=os.environ["SERVICE_BUS_FQNS"],
            service_bus_topic=os.environ["SERVICE_BUS_TOPIC_NAME"],
            service_bus_subscription=os.environ["SERVICE_BUS_SUBSCRIPTION_NAME"],
            storage_account=os.environ["AZURE_STORAGE_ACCOUNT"],
            model_container=os.getenv("MODEL_CONTAINER", "ml-models"),
            max_message_count=int(os.getenv("MAX_MESSAGE_COUNT", "1")),
            max_wait_seconds=int(os.getenv("MAX_WAIT_TIME_SECONDS", "5")),
            max_auto_lock_renewal_seconds=int(os.getenv("MAX_AUTO_LOCK_RENEWAL_SECONDS", "900")),
            max_artifact_bytes=int(os.getenv("MAX_GNN_ARTIFACT_BYTES", "1073741824")),
        )
