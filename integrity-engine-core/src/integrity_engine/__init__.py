"""Deterministic integrity scoring shared by production and ELCE."""

__version__ = "0.4.0"

from .graph import (
    CandidateNomination,
    CandidateDetectorEvaluation,
    EvaluationLimitExceeded,
    GraphInferenceSnapshot,
    RingEvaluation,
    SnapshotNomination,
    calculate_graph_finding_score,
    derive_graph_finding_severity,
    evaluate_candidate_edge_for_ring,
    evaluate_candidate_detectors,
    evaluate_bipartite_dense_block,
    evaluate_copy_paste,
    evaluate_no_finding,
    evaluate_super_beneficiary,
    evaluate_super_nominator,
    evaluate_temporal_burst,
)

__all__ = [
    "CandidateNomination",
    "CandidateDetectorEvaluation",
    "EvaluationLimitExceeded",
    "GraphInferenceSnapshot",
    "RingEvaluation",
    "SnapshotNomination",
    "calculate_graph_finding_score",
    "derive_graph_finding_severity",
    "evaluate_candidate_edge_for_ring",
    "evaluate_candidate_detectors",
    "evaluate_bipartite_dense_block",
    "evaluate_copy_paste",
    "evaluate_no_finding",
    "evaluate_super_beneficiary",
    "evaluate_super_nominator",
    "evaluate_temporal_burst",
    "__version__",
]
