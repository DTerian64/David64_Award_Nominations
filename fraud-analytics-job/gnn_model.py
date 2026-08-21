"""
gnn_model.py — encoder / decoder for the GNN fraud model
=========================================================
Stage 3 of the fraud-analytics-job pipeline (ADR-0002).

The model is trained as one network and deployed as two pieces:

    encoder   HeteroGNN over the user/nomination graph -> per-user embeddings.
              Runs weekly in fraud-analytics-job. Its OUTPUT (the embeddings)
              is persisted to dbo.GNN_UserEmbeddings; the encoder itself goes to
              blob for audit and retraining and is never downloaded by inference.

    decoder   MLP over [z_nominator | z_beneficiary | z_approver | x_nomination].
              ~15k parameters. This is the only artifact integrity-check reads,
              which is what keeps PyTorch Geometric out of the inference image.

GraphSAGE rather than GCN because SAGE is inductive: the encoder generalises to
users added between weekly runs. GAT is a candidate for a later revision if
per-neighbour attention weights are wanted for HRBP-facing explanations.
"""

from __future__ import annotations

import logging

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import HeteroConv, SAGEConv

logger = logging.getLogger(__name__)

# Message-passing relations, including the reverse edges added by gnn_graph.
_RELATIONS = [
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("user", "approves", "nomination"),
    ("nomination", "rev_nominates", "user"),
    ("user", "rev_benefits", "nomination"),
    ("nomination", "rev_approves", "user"),
]

# Approver slot when a nomination has no approver, or the approver has no
# embedding at inference time. Matches gnn_check.py's cold-start behaviour:
# a missing approver is tolerated with a zero vector, a missing nominator or
# beneficiary suppresses the score entirely.
_NO_APPROVER = -1


class HeteroEncoder(nn.Module):
    """Two-layer heterogeneous GraphSAGE producing per-user embeddings."""

    def __init__(self, hidden_dim: int = 64, out_dim: int = 64, num_layers: int = 2):
        super().__init__()
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            dim = out_dim if i == num_layers - 1 else hidden_dim
            self.convs.append(
                HeteroConv(
                    {rel: SAGEConv((-1, -1), dim) for rel in _RELATIONS},
                    aggr="mean",
                )
            )
        self.out_dim = out_dim

    def forward(self, x_dict, edge_index_dict):
        for i, conv in enumerate(self.convs):
            x_dict = conv(x_dict, edge_index_dict)
            if i < len(self.convs) - 1:
                x_dict = {k: F.relu(v) for k, v in x_dict.items()}
        return x_dict


class EdgeDecoder(nn.Module):
    """
    Scores one nomination from three user embeddings plus its own features.

    Deployed standalone to integrity-check. Keep this class free of any
    torch_geometric import — the inference image has torch but not PyG.
    """

    def __init__(self, emb_dim: int, n_nom_features: int, hidden: tuple[int, int] = (64, 32)):
        super().__init__()
        in_dim = 3 * emb_dim + n_nom_features
        h1, h2 = hidden
        self.net = nn.Sequential(
            nn.Linear(in_dim, h1), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(h1, h2),     nn.ReLU(),
            nn.Linear(h2, 1),
        )
        self.emb_dim = emb_dim
        self.n_nom_features = n_nom_features

    def forward(self, z_nom, z_ben, z_appr, x_nom):
        return self.net(torch.cat([z_nom, z_ben, z_appr, x_nom], dim=-1)).squeeze(-1)


class GNNFraudModel(nn.Module):
    """Encoder + decoder, trained end to end."""

    def __init__(self, hidden_dim: int = 64, emb_dim: int = 64, n_nom_features: int = 7):
        super().__init__()
        self.encoder = HeteroEncoder(hidden_dim=hidden_dim, out_dim=emb_dim)
        self.decoder = EdgeDecoder(emb_dim=emb_dim, n_nom_features=n_nom_features)
        self.emb_dim = emb_dim

    def embed_users(self, data) -> torch.Tensor:
        return self.encoder(data.x_dict, data.edge_index_dict)["user"]

    def score(self, z_users: torch.Tensor, triples: torch.Tensor, x_nom: torch.Tensor):
        nom_i, ben_i, appr_i = triples[:, 0], triples[:, 1], triples[:, 2]
        z_nom = z_users[nom_i]
        z_ben = z_users[ben_i]
        has_appr = appr_i != _NO_APPROVER
        z_appr = torch.zeros_like(z_nom)
        if has_appr.any():
            z_appr[has_appr] = z_users[appr_i[has_appr]]
        return self.decoder(z_nom, z_ben, z_appr, x_nom)

    def forward(self, data, triples, x_nom):
        return self.score(self.embed_users(data), triples, x_nom)


# ── Metrics ───────────────────────────────────────────────────────────────────

def pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """
    Average precision. Implemented here rather than pulled from sklearn so this
    module has no sklearn dependency; train_fraud_model.py already owns that.
    Returns nan when only one class is present.
    """
    y_true = np.asarray(y_true).astype(int)
    if y_true.sum() == 0 or y_true.sum() == len(y_true):
        return float("nan")
    order = np.argsort(-np.asarray(y_score))
    y = y_true[order]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / y.sum())


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = np.asarray(y_true).astype(int)
    n_pos, n_neg = int(y_true.sum()), int((1 - y_true).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(np.asarray(y_score))
    ranks = np.empty(len(y_true), dtype=float)
    ranks[order] = np.arange(1, len(y_true) + 1)
    return float((ranks[y_true == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


# ── Training ──────────────────────────────────────────────────────────────────

def train_gnn(
    graph: dict,
    y_train: np.ndarray,
    y_eval: np.ndarray,
    hidden_dim: int = 64,
    emb_dim: int = 64,
    epochs: int = 300,
    lr: float = 0.01,
    weight_decay: float = 5e-4,
    patience: int = 40,
    seed: int = 42,
    log_every: int = 50,
) -> tuple[GNNFraudModel, dict]:
    """
    Train encoder + decoder end to end on the training targets, early-stopping on
    evaluation PR-AUC.

    graph is the dict returned by gnn_graph.build_hetero_data().

    Note on early stopping: selecting the epoch by evaluation PR-AUC does let the
    evaluation window influence model selection, so the returned eval metric is
    mildly optimistic. It is retained because with the label volumes involved a
    third split would leave too little to select on — but the shadow-mode gate in
    ADR-0002 is judged on live weekly runs against human-confirmed labels, not on
    this number.
    """
    torch.manual_seed(seed)
    np.random.seed(seed)

    data = graph["data"]
    tr, ev = graph["train"], graph["eval"]
    n_nom_features = tr["x"].shape[1]

    model = GNNFraudModel(hidden_dim=hidden_dim, emb_dim=emb_dim, n_nom_features=n_nom_features)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    y_tr = torch.tensor(np.asarray(y_train), dtype=torch.float32)
    n_pos = float(y_tr.sum())
    n_neg = float(len(y_tr) - n_pos)
    # Mirrors class_weight='balanced' in the Random Forest.
    pos_weight = torch.tensor([n_neg / n_pos if n_pos > 0 else 1.0])
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    best = {"pr_auc": -1.0, "epoch": -1, "state": None}
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        opt.zero_grad()
        logits = model(data, tr["triples"], tr["x"])
        loss = loss_fn(logits, y_tr)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            ev_logits = model(data, ev["triples"], ev["x"]).numpy()
        ev_pr = pr_auc(y_eval, ev_logits)
        history.append({"epoch": epoch, "loss": loss.item(), "eval_pr_auc": ev_pr})

        if not np.isnan(ev_pr) and ev_pr > best["pr_auc"]:
            best = {
                "pr_auc": ev_pr,
                "epoch": epoch,
                "state": {k: v.detach().clone() for k, v in model.state_dict().items()},
            }
        if epoch - best["epoch"] >= patience:
            logger.info("Early stop at epoch %d (best epoch %d).", epoch, best["epoch"])
            break
        if log_every and epoch % log_every == 0:
            # .detach() before float(): torch warns about converting a tensor
            # that still carries requires_grad to a Python scalar.
            logger.info("epoch %3d  loss %.4f  eval PR-AUC %.4f",
                        epoch, float(loss.detach()), ev_pr)

    if best["state"] is not None:
        model.load_state_dict(best["state"])

    model.eval()
    with torch.no_grad():
        z = model.embed_users(data)
        tr_logits = model.score(z, tr["triples"], tr["x"]).numpy()
        ev_logits = model.score(z, ev["triples"], ev["x"]).numpy()

    base_rate = float(np.mean(y_eval)) if len(y_eval) else float("nan")
    metrics = {
        "best_epoch":     best["epoch"],
        "epochs_run":     len(history),
        "train_pr_auc":   pr_auc(y_train, tr_logits),
        "train_roc_auc":  roc_auc(y_train, tr_logits),
        "eval_pr_auc":    pr_auc(y_eval, ev_logits),
        "eval_roc_auc":   roc_auc(y_eval, ev_logits),
        "eval_base_rate": base_rate,
        "eval_lift":      (pr_auc(y_eval, ev_logits) / base_rate) if base_rate else float("nan"),
        "n_train":        int(len(y_train)),
        "n_eval":         int(len(y_eval)),
        "n_train_pos":    int(np.sum(y_train)),
        "n_eval_pos":     int(np.sum(y_eval)),
        "history":        history,
    }
    return model, metrics
