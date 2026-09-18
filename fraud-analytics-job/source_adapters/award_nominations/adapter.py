"""Award Nomination adapter orchestration."""

from __future__ import annotations

from typing import Any

from integrity_data import IntegrityDataset
from source_adapters.contracts import SourceReadRequest

from .capabilities import AWARD_NOMINATION_CAPABILITIES
from .extract import fetch_nominations, fetch_tenant, fetch_users
from .map import ADAPTER_NAME, ADAPTER_VERSION, SOURCE_SYSTEM, map_award_nomination_rows


class AwardNominationAdapter:
    """Extract and validate Award Nomination data without training a model."""

    source_system = SOURCE_SYSTEM
    adapter_name = ADAPTER_NAME
    adapter_version = ADAPTER_VERSION
    capabilities = AWARD_NOMINATION_CAPABILITIES

    def load(self, connection: Any, request: SourceReadRequest) -> IntegrityDataset:
        tenant = fetch_tenant(connection, request.tenant_id)
        users = fetch_users(connection, request.tenant_id)
        nominations = fetch_nominations(connection, request)
        return map_award_nomination_rows(
            tenant=tenant,
            user_rows=users,
            nomination_rows=nominations,
            request=request,
            capabilities=self.capabilities,
        )
