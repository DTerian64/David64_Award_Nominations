"""Train, compare, publish, and activate the tenant Tabular model family."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from integrity_engine.artifact_paths import tabular_bundle_prefix
from sentence_transformers import SentenceTransformer

from systems.award_nominations.features.tabular import (
    AwardNominationTabularV1FeatureBuilder,
)
from . import (
    TabularTrainingPolicy,
    evaluate_tabular_candidates,
)
from .artifacts import write_tabular_bundle
from .serving import fit_selected_for_serving
from source_adapters.contracts import SourceReadRequest
from systems.award_nominations.dataset import load_award_nomination_dataset
from integrity_sentinel.component_status import upsert_component_status
from integrity_sentinel.db import connect as connect_sentinel
from integrity_sentinel.analytics_coordinator import LeaseLostError
from systems.award_nominations.source.connection import connect as connect_award
from utils.model_artifacts import upload_artifact
from utils.stage_result import TenantStageResult
from systems.award_nominations.source.tenant_config import (
    get_tenant_embed_model,
    get_tenant_name,
    get_tenants,
    get_tenant_tabular_window,
)


logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).resolve().parents[4] / "Output"


def _publish_bundle(
    *,
    tenant_id: int,
    model_version: str,
    bundle_dir: Path,
    artifacts: list[tuple[Path, str]],
) -> None:
    prefix = tabular_bundle_prefix(tenant_id, model_version)
    uploads: list[bool] = []
    for path, _role in artifacts:
        relative = path.relative_to(bundle_dir)
        parent = relative.parent.as_posix()
        blob_folder = prefix if parent == "." else f"{prefix}/{parent}"
        uploads.append(
            upload_artifact(
                path,
                blob_folder=blob_folder,
                blob_filename=relative.name,
            )
        )
    if not os.getenv("AZURE_STORAGE_ACCOUNT") or not all(uploads):
        raise RuntimeError(
            "Tabular bundle was not completely uploaded; serving version preserved"
        )


def _record_status(
    *,
    lease_fence: Callable[[object], None] | None = None,
    **kwargs,
) -> None:
    connection = connect_sentinel()
    try:
        if lease_fence is not None:
            lease_fence(connection)
        # RF remains the database compatibility component name until the
        # model-neutral API migration; diagnostics carry the true architecture.
        upsert_component_status(connection, component="RF", **kwargs)
    finally:
        connection.close()


def process_tenant(
    tenant_id: int,
    run_id: str,
    lease_guard: Callable[[], None] | None = None,
    lease_fence: Callable[[object], None] | None = None,
    data_as_of_utc: datetime | None = None,
) -> TenantStageResult:
    """Evaluate, publish, and activate the Tabular winner for one tenant."""
    try:
        as_of = data_as_of_utc or datetime.now(timezone.utc)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        else:
            as_of = as_of.astimezone(timezone.utc)
        source = connect_award()
        try:
            tenant_name = get_tenant_name(source, tenant_id)
            window_days = get_tenant_tabular_window(source, tenant_id)
            sentinel = connect_sentinel()
            try:
                dataset = load_award_nomination_dataset(
                SourceReadRequest(
                    tenant_id=tenant_id,
                    as_of_exclusive=as_of,
                    # One target window plus its warm-up history. Context rows
                    # build features but are not fitted/evaluated.
                    window_days=2 * window_days,
                ),
                    source_connection=source,
                    sentinel_connection=sentinel,
                )
            finally:
                sentinel.close()
        finally:
            source.close()

        embed_model_name = get_tenant_embed_model(tenant_id)
        features = AwardNominationTabularV1FeatureBuilder().build(
            dataset,
            embed_model=SentenceTransformer(embed_model_name),
            window_days=window_days,
        )
        policy = TabularTrainingPolicy()
        evaluation = evaluate_tabular_candidates(features, policy)
        selected = evaluation.selection.selected_architecture
        if selected is None:
            if lease_guard is not None:
                lease_guard()
            reason = evaluation.selection.selection_reason
            skip_diagnostics = {
                "selection": evaluation.selection.candidate_evaluations,
                "source_snapshot_id": features.source_snapshot_id,
                "window_days": window_days,
            }
            _record_status(
                lease_fence=lease_fence,
                tenant_id=tenant_id,
                attempt_status="SKIPPED",
                reason_code=reason,
                reason_detail="No Tabular candidate passed selection guardrails.",
                diagnostics=skip_diagnostics,
                run_id=run_id,
            )
            return TenantStageResult.skipped(
                reason,
                diagnostics=skip_diagnostics,
            )

        serving_fit = fit_selected_for_serving(features, selected, policy)
        model_version = (
            f"tabular-v2-{as_of:%Y%m%d%H%M%S}-t{tenant_id}-"
            f"{run_id.replace('-', '')[:8]}"
        )
        bundle_dir, artifacts = write_tabular_bundle(
            output_dir=OUTPUT_DIR,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            model_version=model_version,
            feature_dataset=features,
            evaluation=evaluation,
            serving_fit=serving_fit,
            training_policy=policy,
            embed_model_name=embed_model_name,
        )
        _publish_bundle(
            tenant_id=tenant_id,
            model_version=model_version,
            bundle_dir=bundle_dir,
            artifacts=artifacts,
        )
        if lease_guard is not None:
            lease_guard()
        diagnostics = {
            "window_days": window_days,
            "history_feature_contract": features.fitted_state["history_feature_contract"],
            "artifact_bundle_prefix": tabular_bundle_prefix(tenant_id, model_version),
            "source_snapshot_id": features.source_snapshot_id,
            "feature_schema_id": features.schema.schema_id,
            "selected_architecture": selected,
            "selection_reason": evaluation.selection.selection_reason,
            "candidate_evaluations": evaluation.selection.candidate_evaluations,
            "serving_refit_training_count": serving_fit.training_rows,
        }
        _record_status(
            lease_fence=lease_fence,
            tenant_id=tenant_id,
            attempt_status="SUCCEEDED",
            serving_status="AVAILABLE",
            serving_version=model_version,
            serving_as_of=as_of,
            diagnostics=diagnostics,
            run_id=run_id,
        )
        logger.info(
            "Tenant %d Tabular winner activated: %s (%s)",
            tenant_id,
            selected,
            model_version,
        )
        return TenantStageResult.succeeded(
            published_version=model_version,
            diagnostics={"selected_architecture": selected},
        )
    except LeaseLostError:
        raise
    except Exception as exc:
        logger.exception("Tenant %d Tabular training failed", tenant_id)
        try:
            _record_status(
                lease_fence=lease_fence,
                tenant_id=tenant_id,
                attempt_status="FAILED",
                reason_code="TABULAR_TRAINING_FAILED",
                reason_detail=str(exc),
                run_id=run_id,
            )
        except Exception:
            logger.exception("Tenant %d Tabular failure status was not persisted", tenant_id)
        raise


def main(tenants_to_process: list | None = None) -> None:
    """Standalone multi-tenant entry point; coordinated runs call process_tenant."""
    discovery = connect_award()
    try:
        tenants = get_tenants(discovery)
    finally:
        discovery.close()
    if tenants_to_process is not None:
        tenants = [row for row in tenants if row[0] in tenants_to_process]

    run_id = str(uuid.uuid4())
    failures: list[int] = []
    for tenant_id, _tenant_name in tenants:
        try:
            process_tenant(tenant_id, run_id)
        except Exception:
            failures.append(tenant_id)

    if failures:
        raise RuntimeError(f"Tabular training failed for tenant(s): {failures}")


if __name__ == "__main__":
    main()
