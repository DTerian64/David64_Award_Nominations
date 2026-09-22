"""GNN v4: one graph encoder, one overall verdict, and masked pattern heads.

The overall logit is the *only* GNN routing score. Pattern logits are
independent, multi-label evidence and never replace or aggregate into it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn import functional as F

from .graph import BASE_NOMINATION_FEATURE_COLUMNS
from .model import EdgeDecoder, GRAPH_ENCODERS, HeteroEncoder
from .specialists.contracts import BEHAVIOR_TRACKS
from .specialists.feature_contracts import FEATURE_CONTRACTS


@dataclass(frozen=True)
class PatternTargets:
    """One ordered target population; mask=0 means unadjudicated, not negative."""

    overall: np.ndarray
    patterns: np.ndarray
    mask: np.ndarray


def build_pattern_targets(labelled: pd.DataFrame, nomination_ids: list[int]) -> PatternTargets:
    """Use only model-neutral dispositions and explicit confirmed patterns.

    Synthetic ground truth declares a complete taxonomy. Human-confirmed fraud
    is partial until the review workflow explicitly records completeness; an
    absent pattern on such a row is therefore masked, never silently negative.
    """
    required = {"NominationId", "IsFraud", "LabelSource", "ConfirmedPatterns"}
    if required - set(labelled.columns):
        raise ValueError(f"Missing label columns: {sorted(required - set(labelled.columns))}")
    rows = labelled.set_index("NominationId", verify_integrity=True)
    overall = np.zeros(len(nomination_ids), dtype=np.float32)
    patterns = np.zeros((len(nomination_ids), len(BEHAVIOR_TRACKS)), dtype=np.float32)
    mask = np.zeros_like(patterns)
    for i, nomination_id in enumerate(nomination_ids):
        row = rows.loc[nomination_id]
        fraud = int(row["IsFraud"])
        if fraud not in (0, 1):
            raise ValueError("Supervised disposition must be binary")
        overall[i] = fraud
        if fraud == 0:
            mask[i, :] = 1
            continue
        confirmed = {str(value).strip().upper() for value in row["ConfirmedPatterns"]}
        unknown = confirmed - set(BEHAVIOR_TRACKS)
        if unknown:
            raise ValueError(f"Unknown confirmed pattern(s): {sorted(unknown)}")
        complete = str(row["LabelSource"]).lower() == "synthetic_ground_truth"
        for j, key in enumerate(BEHAVIOR_TRACKS):
            if key in confirmed:
                patterns[i, j] = 1
                mask[i, j] = 1
            elif complete:
                mask[i, j] = 1
    return PatternTargets(overall, patterns, mask)


def feature_indices(columns: list[str], head_contracts: dict[str, str]) -> dict[str, list[int]]:
    """Resolve contract slices against the one full causal feature vector."""
    result = {}
    for key, contract in head_contracts.items():
        if key not in BEHAVIOR_TRACKS or contract not in FEATURE_CONTRACTS:
            raise ValueError(f"Unsupported pattern head contract: {key}/{contract}")
        result[key] = [columns.index(name) for name in FEATURE_CONTRACTS[contract]]
    return result


class MultiHeadDecoder(nn.Module):
    """Pure-Torch decoder; its state dict can be reconstructed without PyG."""

    def __init__(self, emb_dim: int, feature_columns: list[str], contracts: dict[str, str]):
        super().__init__()
        self.overall = EdgeDecoder(emb_dim, len(feature_columns))
        self.patterns = nn.ModuleDict({
            key: EdgeDecoder(emb_dim, len(indices))
            for key, indices in feature_indices(feature_columns, contracts).items()
        })
        self.indices = feature_indices(feature_columns, contracts)

    def forward(self, z_nom: torch.Tensor, z_ben: torch.Tensor, x: torch.Tensor) -> dict[str, torch.Tensor]:
        logits = {"OVERALL": self.overall(z_nom, z_ben, x)}
        for key, decoder in self.patterns.items():
            logits[key] = decoder(z_nom, z_ben, x[:, self.indices[key]])
        return logits


class SharedMultiHeadModel(nn.Module):
    """All pattern heads use the same two-layer heterogeneous encoder."""

    def __init__(
        self, architecture: str, graph: dict, contracts: dict[str, str],
        hidden_dim: int = 64, emb_dim: int = 64,
    ):
        super().__init__()
        if architecture not in (*GRAPH_ENCODERS, "raw_feature_mlp", "engineered_graph_mlp"):
            raise ValueError(f"Unsupported v4 architecture: {architecture}")
        self.architecture = architecture
        self.feature_columns = list(graph["nomination_feature_columns"])
        self.encoder = (
            HeteroEncoder(hidden_dim=hidden_dim, out_dim=emb_dim, architecture=architecture)
            if architecture in GRAPH_ENCODERS else None
        )
        self.overall_indices = (
            [self.feature_columns.index(name) for name in BASE_NOMINATION_FEATURE_COLUMNS]
            if architecture == "raw_feature_mlp" else list(range(len(self.feature_columns)))
        )
        self.emb_dim = (
            emb_dim if self.encoder is not None else
            0 if architecture == "raw_feature_mlp" else int(graph["data"]["user"].x.shape[1])
        )
        decoder_columns = [self.feature_columns[i] for i in self.overall_indices]
        self.decoder = MultiHeadDecoder(
            self.emb_dim, decoder_columns,
            {} if architecture == "raw_feature_mlp" else contracts,
        )

    def embed_users(self, data) -> torch.Tensor:
        if self.encoder is not None:
            return self.encoder(data.x_dict, data.edge_index_dict)["user"]
        if self.architecture == "raw_feature_mlp":
            return data["user"].x.new_zeros((data["user"].x.shape[0], 0))
        return data["user"].x

    def forward(self, data, pairs: torch.Tensor, x: torch.Tensor) -> dict[str, torch.Tensor]:
        z = self.embed_users(data)
        return self.decoder(
            z[pairs[:, 0]], z[pairs[:, 1]], x[:, self.overall_indices]
        )


def masked_joint_loss(
    logits: dict[str, torch.Tensor], targets: PatternTargets,
    overall_weight: float = 1.0, pattern_total_weight: float = 1.0,
) -> torch.Tensor:
    """Balance each binary task, averaging only heads with known labels."""
    def balanced(logit: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        positives = target.sum()
        negatives = target.numel() - positives
        weight = (negatives / positives.clamp(min=1)).detach()
        return F.binary_cross_entropy_with_logits(logit, target, pos_weight=weight)

    overall = torch.as_tensor(targets.overall, dtype=torch.float32)
    loss = overall_weight * balanced(logits["OVERALL"], overall)
    head_losses = []
    for j, key in enumerate(BEHAVIOR_TRACKS):
        if key not in logits:
            continue
        known = torch.as_tensor(targets.mask[:, j].astype(bool))
        if known.any():
            values = torch.as_tensor(targets.patterns[:, j], dtype=torch.float32)
            head_losses.append(balanced(logits[key][known], values[known]))
    if head_losses:
        loss = loss + pattern_total_weight * torch.stack(head_losses).mean()
    return loss
