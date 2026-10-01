"""Evaluation strategies for GNN candidates.

Serving selection and diagnostic evaluation intentionally live in separate
packages. ``selection_by_holdout_pr_auc`` handles v2, while
``selection_by_temporal_validation`` handles v4. V3 selects per specialist.
"""
