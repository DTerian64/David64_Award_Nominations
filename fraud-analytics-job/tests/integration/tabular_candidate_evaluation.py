"""Manual read-only T4 candidate evaluation over a live tenant snapshot.

This command reads source data, trains in-memory Random Forest and Tabular MLP
candidates, and applies the deterministic selection policy. It does not write
the database, create artifacts, upload blobs, or activate a serving model.

Example
-------
python -m tests.integration.tabular_candidate_evaluation --tenant 5
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Sequence

from sentence_transformers import SentenceTransformer

from feature_builders.tabular import AwardNominationTabularV1FeatureBuilder
from modeling.tabular import evaluate_tabular_candidates
from source_adapters.award_nominations import AwardNominationAdapter
from source_adapters.award_nominations.live_smoke import _load_environment
from source_adapters.contracts import SourceReadRequest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate non-serving Tabular-v1 candidates against live data."
    )
    parser.add_argument("--tenant", required=True, type=int)
    return parser


def _candidate_summary(result: Any) -> dict[str, Any]:
    return {
        "architecture": result.architecture,
        "status": result.status,
        "feature_schema_id": result.feature_schema_id,
        "source_snapshot_id": result.source_snapshot_id,
        "metrics": dict(result.metrics),
        "holdout": (
            {
                "training_rows": len(result.holdout.train_index),
                "evaluation_rows": len(result.holdout.evaluation_index),
                "train_end": result.holdout.train_end,
                "evaluation_start": result.holdout.evaluation_start,
            }
            if result.holdout is not None
            else None
        ),
        "reason_code": result.reason_code,
        "reason_detail": result.reason_detail,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _load_environment()

    from utils.db_conn import connect
    from utils.tenant_model_config import get_tenant_embed_model

    connection = connect()
    try:
        dataset = AwardNominationAdapter().load(
            connection,
            SourceReadRequest(
                tenant_id=args.tenant,
                as_of_exclusive=datetime.now(timezone.utc),
                window_days=None,
            ),
        )
    finally:
        connection.close()

    embed_model_name = get_tenant_embed_model(args.tenant)
    feature_dataset = AwardNominationTabularV1FeatureBuilder().build(
        dataset,
        embed_model=SentenceTransformer(embed_model_name),
    )
    evaluation = evaluate_tabular_candidates(feature_dataset)
    summary = {
        "source_event_count": len(dataset.events),
        "feature_row_count": len(feature_dataset.frame),
        "feature_schema_id": feature_dataset.schema.schema_id,
        "source_snapshot_id": feature_dataset.source_snapshot_id,
        "candidates": {
            name: _candidate_summary(result)
            for name, result in evaluation.candidates.items()
        },
        "selection": {
            "status": evaluation.selection.status,
            "selected_architecture": evaluation.selection.selected_architecture,
            "selection_metric": evaluation.selection.selection_metric,
            "selection_reason": evaluation.selection.selection_reason,
            "candidate_evaluations": evaluation.selection.candidate_evaluations,
            "serving_state_changed": evaluation.selection.serving_state_changed,
        },
    }
    print(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if evaluation.selection.status == "SELECTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
