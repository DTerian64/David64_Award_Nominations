"""The same frozen heterogeneous encoder is used by training and inference.

Import this module explicitly: deterministic core users do not need PyG.
"""
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATv2Conv, GraphConv, HeteroConv, SAGEConv

RELATIONS = [
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("nomination", "rev_nominates", "user"),
    ("user", "rev_benefits", "nomination"),
    ("nomination", "belongs_to", "category"),
    ("category", "rev_belongs_to", "nomination"),
]
GRAPH_ENCODERS = ("graphsage", "gcn", "gatv2")


def message_passing_layer(architecture, out_dim):
    if architecture == "graphsage":
        return SAGEConv((-1, -1), out_dim)
    if architecture == "gcn":
        return GraphConv((-1, -1), out_dim, aggr="mean")
    if architecture == "gatv2":
        return GATv2Conv((-1, -1), out_dim, heads=1, concat=False, add_self_loops=False)
    raise ValueError(f"Unsupported graph encoder: {architecture}")


class HeteroEncoder(nn.Module):
    def __init__(self, hidden_dim=64, out_dim=64, num_layers=2, architecture="graphsage"):
        super().__init__()
        if architecture not in GRAPH_ENCODERS or num_layers < 1:
            raise ValueError("Invalid graph encoder architecture or depth")
        self.convs = nn.ModuleList([
            HeteroConv({relation: message_passing_layer(
                architecture, out_dim if i == num_layers - 1 else hidden_dim,
            ) for relation in RELATIONS}, aggr="mean")
            for i in range(num_layers)
        ])
        self.out_dim, self.architecture = out_dim, architecture

    def forward(self, x_dict, edge_index_dict):
        for i, conv in enumerate(self.convs):
            x_dict = conv(x_dict, edge_index_dict)
            if i < len(self.convs) - 1:
                x_dict = {key: F.relu(value) for key, value in x_dict.items()}
        return x_dict
