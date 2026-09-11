"""
model.py — encoder / decoder for the GNN fraud model
=====================================================
Stage 3 of the fraud-analytics-job pipeline.

The model is trained as one network and deployed as two pieces:

    encoder   HeteroGNN over the user/nomination graph -> per-user embeddings.
              Runs weekly in fraud-analytics-job. Its OUTPUT (the embeddings)
              is persisted to dbo.GNN_UserEmbeddings; the encoder itself goes to
              blob for audit and retraining and is never downloaded by inference.

    decoder   MLP over [z_nominator | z_beneficiary | x_nomination].
              ~11k parameters. This is the only artifact integrity-check reads,
              which is what keeps PyTorch Geometric out of the inference image.

GraphSAGE rather than GCN because its aggregation can support inductive serving.
The current decoder-only live path still requires a user embedding from the
latest compatible weekly snapshot, so new users remain an explicit cold start.
TGN is reserved for the later architecture experiment defined in the v2 plan.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATv2Conv, GraphConv, HeteroConv, SAGEConv

logger = logging.getLogger(__name__)

# Message-passing relations, including the reverse edges added by graph.py.
_RELATIONS = [
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("nomination", "rev_nominates", "user"),
    ("user", "rev_benefits", "nomination"),
    ("nomination", "belongs_to", "category"),
    ("category", "rev_belongs_to", "nomination"),
]


GRAPH_ENCODERS = ("graphsage", "gcn", "gatv2")
CANDIDATE_ARCHITECTURES = ("mlp", *GRAPH_ENCODERS)


def _message_passing_layer(architecture: str, out_dim: int) -> nn.Module:
    if architecture == "graphsage":
        return SAGEConv((-1, -1), out_dim)
    if architecture == "gcn":
        # GraphConv supports bipartite heterogeneous relations. PyG's GCNConv
        # does not, so GraphConv is the correct GCN-family implementation here.
        return GraphConv((-1, -1), out_dim, aggr="mean")
    if architecture == "gatv2":
        return GATv2Conv(
            (-1, -1), out_dim, heads=1, concat=False, add_self_loops=False
        )
    raise ValueError(
        f"Unknown graph encoder {architecture!r}; expected one of {GRAPH_ENCODERS}"
    )


class HeteroEncoder(nn.Module):
    """Two-layer heterogeneous graph encoder producing per-user embeddings."""

    def __init__(
        self,
        hidden_dim: int = 64,
        out_dim: int = 64,
        num_layers: int = 2,
        architecture: str = "graphsage",
    ):
        super().__init__()
        if architecture not in GRAPH_ENCODERS:
            raise ValueError(
                f"Unknown graph encoder {architecture!r}; expected one of {GRAPH_ENCODERS}"
            )
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            dim = out_dim if i == num_layers - 1 else hidden_dim
            self.convs.append(
                HeteroConv(
                    {
                        rel: _message_passing_layer(architecture, dim)
                        for rel in _RELATIONS
                    },
                    aggr="mean",
                )
            )
        self.out_dim = out_dim
        self.architecture = architecture

    def forward(self, x_dict, edge_index_dict):
        for i, conv in enumerate(self.convs):
            x_dict = conv(x_dict, edge_index_dict)
            if i < len(self.convs) - 1:
                x_dict = {k: F.relu(v) for k, v in x_dict.items()}
        return x_dict


class EdgeDecoder(nn.Module):
    """
    Scores one nomination from the P2P user embeddings plus its own features.

    Deployed standalone to integrity-check. Keep this class free of any
    torch_geometric import — the inference image has torch but not PyG.
    """

    def __init__(self, emb_dim: int, n_nom_features: int, hidden: tuple[int, int] = (64, 32)):
        super().__init__()
        in_dim = 2 * emb_dim + n_nom_features
        h1, h2 = hidden
        self.net = nn.Sequential(
            nn.Linear(in_dim, h1), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(h1, h2),     nn.ReLU(),
            nn.Linear(h2, 1),
        )
        self.emb_dim = emb_dim
        self.n_nom_features = n_nom_features

    def forward(self, z_nom, z_ben, x_nom):
        return self.net(torch.cat([z_nom, z_ben, x_nom], dim=-1)).squeeze(-1)


class GNNFraudModel(nn.Module):
    """Encoder + decoder, trained end to end."""

    def __init__(
        self,
        hidden_dim: int = 64,
        emb_dim: int = 64,
        n_nom_features: int = 6,
        architecture: str = "graphsage",
    ):
        super().__init__()
        self.encoder = HeteroEncoder(
            hidden_dim=hidden_dim,
            out_dim=emb_dim,
            architecture=architecture,
        )
        self.decoder = EdgeDecoder(emb_dim=emb_dim, n_nom_features=n_nom_features)
        self.emb_dim = emb_dim
        self.architecture = architecture

    def embed_users(self, data) -> torch.Tensor:
        return self.encoder(data.x_dict, data.edge_index_dict)["user"]

    def score(self, z_users: torch.Tensor, pairs: torch.Tensor, x_nom: torch.Tensor):
        nom_i, ben_i = pairs[:, 0], pairs[:, 1]
        z_nom = z_users[nom_i]
        z_ben = z_users[ben_i]
        return self.decoder(z_nom, z_ben, x_nom)

    def forward(self, data, pairs, x_nom):
        return self.score(self.embed_users(data), pairs, x_nom)


class NoGraphFraudModel(nn.Module):
    """MLP baseline over endpoint attributes and nomination attributes only."""

    def __init__(self, user_feature_dim: int, n_nom_features: int):
        super().__init__()
        self.decoder = EdgeDecoder(
            emb_dim=user_feature_dim,
            n_nom_features=n_nom_features,
        )
        self.emb_dim = user_feature_dim
        self.architecture = "mlp"

    @staticmethod
    def embed_users(data) -> torch.Tensor:
        return data["user"].x

    def score(self, z_users: torch.Tensor, pairs: torch.Tensor, x_nom: torch.Tensor):
        nom_i, ben_i = pairs[:, 0], pairs[:, 1]
        return self.decoder(z_users[nom_i], z_users[ben_i], x_nom)

    def forward(self, data, pairs, x_nom):
        return self.score(self.embed_users(data), pairs, x_nom)


def build_candidate(
    architecture: str,
    graph: dict,
    hidden_dim: int = 64,
    emb_dim: int = 64,
) -> nn.Module:
    """Construct one candidate under the common rolling-fold interface."""
    if architecture not in CANDIDATE_ARCHITECTURES:
        raise ValueError(
            f"Unknown candidate {architecture!r}; expected one of "
            f"{CANDIDATE_ARCHITECTURES}"
        )
    n_nom_features = int(graph["train"]["x"].shape[1])
    if architecture == "mlp":
        return NoGraphFraudModel(
            user_feature_dim=int(graph["data"]["user"].x.shape[1]),
            n_nom_features=n_nom_features,
        )
    return GNNFraudModel(
        hidden_dim=hidden_dim,
        emb_dim=emb_dim,
        n_nom_features=n_nom_features,
        architecture=architecture,
    )


# ── Metrics ───────────────────────────────────────────────────────────────────

def pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """
    Average precision. Implemented here rather than pulled from sklearn so this
    module has no sklearn dependency; train_rf_model.py already owns that.
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

    graph is the dict returned by graph.build_hetero_data().

    Note on early stopping: selecting the epoch by evaluation PR-AUC does let the
    evaluation window influence model selection, so the returned eval metric is
    mildly optimistic. It is retained because with the label volumes involved a
    third split would leave too little to select on. Model value is judged on
    live outcomes against human-confirmed labels, not on this number alone.
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
        logits = model(data, tr["pairs"], tr["x"])
        loss = loss_fn(logits, y_tr)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            ev_logits = model(data, ev["pairs"], ev["x"]).numpy()
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
        tr_logits = model.score(z, tr["pairs"], tr["x"]).numpy()
        ev_logits = model.score(z, ev["pairs"], ev["x"]).numpy()

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


def fit_candidate_rolling(
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    architecture: str = "graphsage",
    hidden_dim: int = 64,
    emb_dim: int = 64,
    epochs: int = 300,
    lr: float = 0.01,
    weight_decay: float = 5e-4,
    seed: int = 42,
    log_every: int = 50,
) -> tuple[nn.Module, dict]:
    """Fit one candidate across time-valid, disjoint labelled windows.

    This primitive deliberately performs no evaluation. It is used both by the
    candidate bake-off and by the post-selection refit, where the former
    holdout becomes one additional matured-label training interval.
    """
    if not folds:
        raise ValueError("At least one rolling fold is required")
    if len(folds) != len(y_train_by_fold):
        raise ValueError("Each rolling fold must have one training-label array")
    if epochs < 1:
        raise ValueError("epochs must be at least 1")

    training_started = time.perf_counter()
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = build_candidate(
        architecture,
        folds[-1],
        hidden_dim=hidden_dim,
        emb_dim=emb_dim,
    )
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    all_train_labels = np.concatenate(
        [np.asarray(labels, dtype=np.float32) for labels in y_train_by_fold]
    )
    n_pos = float(all_train_labels.sum())
    n_neg = float(len(all_train_labels) - n_pos)
    pos_weight = torch.tensor([n_neg / n_pos if n_pos > 0 else 1.0])
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        opt.zero_grad()
        epoch_loss = 0.0
        contributing_folds = 0
        active_fold_count = sum(len(labels) > 0 for labels in y_train_by_fold)
        for graph, labels in zip(folds, y_train_by_fold):
            if len(labels) == 0:
                continue
            y_train = torch.tensor(np.asarray(labels), dtype=torch.float32)
            logits = model(
                graph["data"], graph["train"]["pairs"], graph["train"]["x"]
            )
            loss = loss_fn(logits, y_train)
            (loss / active_fold_count).backward()
            epoch_loss += float(loss.detach())
            contributing_folds += 1
        if contributing_folds == 0:
            raise ValueError("Rolling folds contain no labelled training targets")
        opt.step()
        mean_loss = epoch_loss / contributing_folds
        history.append({"epoch": epoch, "loss": mean_loss})
        if log_every and epoch % log_every == 0:
            logger.info(
                "%s rolling epoch %3d  loss %.4f",
                architecture,
                epoch,
                mean_loss,
            )

    model.eval()
    return model, {
        "architecture": architecture,
        "epochs_run": epochs,
        "training_duration_seconds": time.perf_counter() - training_started,
        "parameter_count": int(
            sum(parameter.numel() for parameter in model.parameters())
        ),
        "n_train": int(len(all_train_labels)),
        "n_train_pos": int(np.sum(all_train_labels)),
        "history": history,
    }


def train_candidate_rolling(
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    y_holdout: np.ndarray,
    architecture: str = "graphsage",
    hidden_dim: int = 64,
    emb_dim: int = 64,
    epochs: int = 300,
    lr: float = 0.01,
    weight_decay: float = 5e-4,
    seed: int = 42,
    log_every: int = 50,
) -> tuple[nn.Module, dict]:
    """Train on disjoint rolling windows and score the final holdout once.

    Epoch count and all hyperparameters are fixed before the final interval is
    evaluated. This deliberately has no early stopping: using the final
    interval to choose an epoch would turn the holdout into validation data.
    """
    model, fit_metrics = fit_candidate_rolling(
        folds,
        y_train_by_fold,
        architecture,
        hidden_dim=hidden_dim,
        emb_dim=emb_dim,
        epochs=epochs,
        lr=lr,
        weight_decay=weight_decay,
        seed=seed,
        log_every=log_every,
    )
    all_train_labels = np.concatenate(
        [np.asarray(labels, dtype=np.float32) for labels in y_train_by_fold]
    )
    model.eval()
    train_logits = []
    inference_started = time.perf_counter()
    with torch.no_grad():
        for graph, labels in zip(folds, y_train_by_fold):
            if len(labels) == 0:
                continue
            logits = model(
                graph["data"], graph["train"]["pairs"], graph["train"]["x"]
            )
            train_logits.append(logits.numpy())
        holdout = folds[-1]
        holdout_logits = model(
            holdout["data"], holdout["eval"]["pairs"], holdout["eval"]["x"]
        ).numpy()
    holdout_inference_ms = (time.perf_counter() - inference_started) * 1000.0

    train_scores = np.concatenate(train_logits)
    holdout_labels = np.asarray(y_holdout)
    holdout_base_rate = (
        float(np.mean(holdout_labels)) if len(holdout_labels) else float("nan")
    )
    holdout_pr_auc = pr_auc(holdout_labels, holdout_logits)
    holdout_probabilities = 1.0 / (
        1.0 + np.exp(-np.clip(holdout_logits, -60.0, 60.0))
    )
    metrics = {
        "architecture": architecture,
        "selection_policy": "fixed_epochs_final_holdout_untouched",
        "epochs_run": epochs,
        "rolling_fold_count": len(folds),
        "train_pr_auc": pr_auc(all_train_labels, train_scores),
        "train_roc_auc": roc_auc(all_train_labels, train_scores),
        "eval_pr_auc": holdout_pr_auc,
        "eval_roc_auc": roc_auc(holdout_labels, holdout_logits),
        "eval_base_rate": holdout_base_rate,
        "eval_lift": (
            holdout_pr_auc / holdout_base_rate
            if holdout_base_rate and not np.isnan(holdout_pr_auc)
            else float("nan")
        ),
        "eval_brier_score": float(
            np.mean(np.square(holdout_probabilities - holdout_labels))
        ),
        "training_duration_seconds": fit_metrics["training_duration_seconds"],
        "holdout_inference_ms": holdout_inference_ms,
        "parameter_count": fit_metrics["parameter_count"],
        "n_train": int(len(all_train_labels)),
        "n_eval": int(len(holdout_labels)),
        "n_train_pos": int(np.sum(all_train_labels)),
        "n_eval_pos": int(np.sum(holdout_labels)),
        "folds": [
            {
                "fold_index": int(graph.get("fold_index", index + 1)),
                "graph_cutoff": graph["t_graph"].isoformat(),
                "train_end": graph["t_cut"].isoformat(),
                "eval_end": graph["eval_end"].isoformat(),
                "n_train": int(len(labels)),
                "n_train_pos": int(np.sum(labels)),
            }
            for index, (graph, labels) in enumerate(zip(folds, y_train_by_fold))
        ],
        "history": fit_metrics["history"],
    }
    return model, metrics


def train_gnn_rolling(
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    y_holdout: np.ndarray,
    **kwargs,
) -> tuple[GNNFraudModel, dict]:
    """Production GraphSAGE wrapper around the candidate training contract."""
    model, metrics = train_candidate_rolling(
        folds,
        y_train_by_fold,
        y_holdout,
        architecture="graphsage",
        **kwargs,
    )
    return model, metrics
