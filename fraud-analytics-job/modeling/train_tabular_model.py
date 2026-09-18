"""Train, compare, publish, and activate the tenant Tabular model family."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from integrity_engine.artifact_paths import tabular_bundle_prefix
from sentence_transformers import SentenceTransformer

from feature_builders.tabular import AwardNominationTabularV1FeatureBuilder
from modeling.tabular import (
    TabularTrainingPolicy,
    evaluate_tabular_candidates,
)
from modeling.tabular.artifacts import write_tabular_bundle
from modeling.tabular.serving import fit_selected_for_serving
from source_adapters.award_nominations import AwardNominationAdapter
from source_adapters.contracts import SourceReadRequest
from utils.component_status import upsert_component_status
from utils.db_conn import connect
from utils.model_artifacts import upload_artifact
from utils.tenant_model_config import get_tenant_embed_model, get_tenants


logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "Output"


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


def _record_status(**kwargs) -> None:
    connection = connect()
    try:
        # RF remains the database compatibility component name until the
        # model-neutral API migration; diagnostics carry the true architecture.
        upsert_component_status(connection, component="RF", **kwargs)
    finally:
        connection.close()


def main(tenants_to_process: list | None = None) -> None:
    """Evaluate both architectures and atomically activate each tenant winner."""

    discovery = connect()
    try:
        tenants = get_tenants(discovery)
    finally:
        discovery.close()
    if tenants_to_process is not None:
        tenants = [row for row in tenants if row[0] in tenants_to_process]

    run_id = str(uuid.uuid4())
    failures: list[int] = []
    for tenant_id, tenant_name in tenants:
        try:
            as_of = datetime.now(timezone.utc)
            source = connect()
            try:
                dataset = AwardNominationAdapter().load(
                    source,
                    SourceReadRequest(
                        tenant_id=tenant_id,
                        as_of_exclusive=as_of,
                        window_days=None,
                    ),
                )
            finally:
                source.close()

            embed_model_name = get_tenant_embed_model(tenant_id)
            features = AwardNominationTabularV1FeatureBuilder().build(
                dataset,
                embed_model=SentenceTransformer(embed_model_name),
            )
            policy = TabularTrainingPolicy()
            evaluation = evaluate_tabular_candidates(features, policy)
            selected = evaluation.selection.selected_architecture
            if selected is None:
                _record_status(
                    tenant_id=tenant_id,
                    attempt_status="SKIPPED",
                    reason_code=evaluation.selection.selection_reason,
                    reason_detail="No Tabular candidate passed selection guardrails.",
                    diagnostics={
                        "selection": evaluation.selection.candidate_evaluations,
                        "source_snapshot_id": features.source_snapshot_id,
                    },
                    run_id=run_id,
                )
                continue

            serving_fit = fit_selected_for_serving(
                features, selected, policy
            )
            model_version = (
                f"tabular-v1-{as_of:%Y%m%d%H%M%S}-t{tenant_id}-"
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
            diagnostics = {
                "artifact_bundle_prefix": tabular_bundle_prefix(
                    tenant_id, model_version
                ),
                "source_snapshot_id": features.source_snapshot_id,
                "feature_schema_id": features.schema.schema_id,
                "selected_architecture": selected,
                "selection_reason": evaluation.selection.selection_reason,
                "candidate_evaluations": evaluation.selection.candidate_evaluations,
                "serving_refit_training_count": serving_fit.training_rows,
            }
            _record_status(
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
        except Exception as exc:
            failures.append(tenant_id)
            logger.exception("Tenant %d Tabular training failed", tenant_id)
            try:
                _record_status(
                    tenant_id=tenant_id,
                    attempt_status="FAILED",
                    reason_code="TABULAR_TRAINING_FAILED",
                    reason_detail=str(exc),
                    run_id=run_id,
                )
            except Exception:
                logger.exception("Tenant %d Tabular failure status was not persisted", tenant_id)

    if failures:
        raise RuntimeError(f"Tabular training failed for tenant(s): {failures}")


if __name__ == "__main__":
    main()
