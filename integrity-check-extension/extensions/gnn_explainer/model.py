"""Restricted reconstruction of supported GNN v2 serving models."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATv2Conv, GraphConv, HeteroConv, SAGEConv

from .errors import PermanentExtensionError

RELATIONS = (
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("nomination", "rev_nominates", "user"),
    ("user", "rev_benefits", "nomination"),
    ("nomination", "belongs_to", "category"),
    ("category", "rev_belongs_to", "nomination"),
)
SUPPORTED_ARCHITECTURES = ("graphsage", "gcn", "gatv2")


def _layer(architecture: str, out_dim: int) -> nn.Module:
    if architecture == "graphsage":
        return SAGEConv((-1, -1), out_dim)
    if architecture == "gcn":
        return GraphConv((-1, -1), out_dim, aggr="mean")
    if architecture == "gatv2":
        return GATv2Conv((-1, -1), out_dim, heads=1, concat=False, add_self_loops=False)
    raise PermanentExtensionError(f"UNSUPPORTED_ARCHITECTURE:{architecture}")


class HeteroEncoder(nn.Module):
    def __init__(self, architecture: str, hidden_dim: int, out_dim: int, num_layers: int):
        super().__init__()
        self.convs = nn.ModuleList()
        for index in range(num_layers):
            dimension = out_dim if index == num_layers - 1 else hidden_dim
            self.convs.append(HeteroConv(
                {relation: _layer(architecture, dimension) for relation in RELATIONS},
                aggr="mean",
            ))

    def forward(self, x_dict, edge_index_dict):
        for index, convolution in enumerate(self.convs):
            x_dict = convolution(x_dict, edge_index_dict)
            if index < len(self.convs) - 1:
                x_dict = {name: F.relu(value) for name, value in x_dict.items()}
        return x_dict


def build_decoder(head: dict) -> nn.Sequential:
    emb_dim = int(head["emb_dim"])
    feature_count = len(head["nomination_feature_columns"])
    hidden = tuple(int(value) for value in head.get("decoder_hidden", (64, 32)))
    if len(hidden) != 2:
        raise PermanentExtensionError("INVALID_DECODER_HIDDEN_SHAPE")
    module = nn.Sequential(
        nn.Linear(2 * emb_dim + feature_count, hidden[0]), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(hidden[0], hidden[1]), nn.ReLU(), nn.Linear(hidden[1], 1),
    )
    module.load_state_dict(head["decoder_state_dict"], strict=True)
    module.eval()
    return module


def build_encoder(head: dict, x_dict: dict, edge_index_dict: dict) -> HeteroEncoder:
    module = HeteroEncoder(
        architecture=str(head.get("architecture", "")).lower(),
        hidden_dim=int(head["hidden_dim"]),
        out_dim=int(head["emb_dim"]),
        num_layers=int(head["num_layers"]),
    )
    # The PyG layers are lazy; initialize tensor shapes before strict loading.
    with torch.no_grad():
        module(x_dict, edge_index_dict)
    module.load_state_dict(head["encoder_state_dict"], strict=True)
    module.eval()
    return module
