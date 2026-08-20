"""
validate_gnn_synthetic.py — proof that the GNN stage trains, plus an ablation
=============================================================================
Operational harness, not a unit test: it prints a report rather than asserting,
which is why it lives here alongside inspect_fraud_model.py. The pytest suite
for the same modules is in fraud-analytics-job/tests/.

It exists to answer one question before any of this touches real data:

    does message passing actually contribute, or would a plain MLP over the
    same flat per-user features do just as well?

That is the empirical core of ADR-0002. If the ablation matches the full model,
the GNN is an expensive reimplementation of features the Random Forest already
has, and the ADR's premise is wrong.

Run:  python scripts/validate_gnn_synthetic.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

# ── path setup ────────────────────────────────────────────────────────────────
# Same convention as inspect_fraud_model.py. The fraud-analytics-job stages are
# flat top-level modules (see run_job.py), so the job directory goes on sys.path
# directly rather than being imported as a package.
_repo_root = Path(__file__).resolve().parent.parent
_job_dir = _repo_root / "fraud-analytics-job"
sys.path.insert(0, str(_job_dir))
sys.path.insert(0, str(_job_dir / "tests"))

import gnn_graph as G
from gnn_model import EdgeDecoder, GNNFraudModel, pr_auc, roc_auc, train_gnn
from synthetic import make_tenant

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("validate_gnn")


class NoMessagePassing(nn.Module):
    """
    Ablation control: identical decoder, but user 'embeddings' are the raw
    behavioural features with no graph convolution at all.

    This is the honest baseline — roughly what the Random Forest already sees,
    since UserGraphFlags aside its user-level signal is these same aggregates.
    """

    def __init__(self, n_user_features: int, n_nom_features: int):
        super().__init__()
        self.decoder = EdgeDecoder(emb_dim=n_user_features, n_nom_features=n_nom_features)

    def embed_users(self, data):
        return data["user"].x

    def score(self, z, triples, x_nom):
        nom_i, ben_i, appr_i = triples[:, 0], triples[:, 1], triples[:, 2]
        z_nom, z_ben = z[nom_i], z[ben_i]
        has = appr_i != -1
        z_appr = torch.zeros_like(z_nom)
        if has.any():
            z_appr[has] = z[appr_i[has]]
        return self.decoder(z_nom, z_ben, z_appr, x_nom)

    def forward(self, data, triples, x_nom):
        return self.score(self.embed_users(data), triples, x_nom)


def train_ablation(graph, y_train, y_eval, epochs=300, lr=0.01, patience=40, seed=42):
    torch.manual_seed(seed)
    data, tr, ev = graph["data"], graph["train"], graph["eval"]
    model = NoMessagePassing(data["user"].x.shape[1], tr["x"].shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=5e-4)
    y_tr = torch.tensor(y_train, dtype=torch.float32)
    n_pos = float(y_tr.sum()); n_neg = float(len(y_tr) - n_pos)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([n_neg / max(n_pos, 1.0)]))

    best = {"pr": -1.0, "epoch": -1, "state": None}
    for epoch in range(1, epochs + 1):
        model.train(); opt.zero_grad()
        loss = loss_fn(model(data, tr["triples"], tr["x"]), y_tr)
        loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            p = pr_auc(y_eval, model(data, ev["triples"], ev["x"]).numpy())
        if not np.isnan(p) and p > best["pr"]:
            best = {"pr": p, "epoch": epoch,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        if epoch - best["epoch"] >= patience:
            break
    model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        ev_logits = model(data, ev["triples"], ev["x"]).numpy()
    return {
        "eval_pr_auc":  pr_auc(y_eval, ev_logits),
        "eval_roc_auc": roc_auc(y_eval, ev_logits),
        "best_epoch":   best["epoch"],
    }


def main() -> int:
    log.info("=" * 74)
    log.info("GNN STAGE — synthetic end-to-end validation")
    log.info("=" * 74)

    users, noms, labels = make_tenant(1)
    log.info("Fixture: %d users, %d nominations, %.1f%% planted collusion",
             len(users), len(noms), 100 * np.mean(labels))

    graph = G.build_hetero_data(users, noms)
    d = graph["data"]
    by_id = {n["NominationId"]: l for n, l in zip(noms, labels)}
    y_train = np.array([by_id[i] for i in graph["train"]["nom_ids"]])
    y_eval  = np.array([by_id[i] for i in graph["eval"]["nom_ids"]])

    log.info("")
    log.info("Graph      : %d user nodes, %d nomination nodes",
             d["user"].num_nodes, d["nomination"].num_nodes)
    for rel in d.edge_types:
        log.info("  %-42s %d edges", str(rel), d[rel].edge_index.shape[1])
    log.info("Split      : t_graph=%s  t_cut=%s", graph["t_graph"], graph["t_cut"])
    log.info("Targets    : train %d (%d pos)  eval %d (%d pos)",
             len(y_train), y_train.sum(), len(y_eval), y_eval.sum())

    log.info("")
    log.info("── Training full model (encoder + decoder) " + "─" * 30)
    model, m = train_gnn(graph, y_train, y_eval, epochs=300, log_every=50)

    log.info("")
    log.info("── Ablation: same decoder, NO message passing " + "─" * 27)
    abl = train_ablation(graph, y_train, y_eval)

    base = m["eval_base_rate"]
    log.info("")
    log.info("=" * 74)
    log.info("RESULTS  (evaluation window — strictly out-of-graph, post-t_cut)")
    log.info("=" * 74)
    log.info("  base rate (random)          %.4f", base)
    log.info("  no message passing  PR-AUC  %.4f   (%.2fx base)", abl["eval_pr_auc"], abl["eval_pr_auc"] / base)
    log.info("  full GNN            PR-AUC  %.4f   (%.2fx base)", m["eval_pr_auc"], m["eval_lift"])
    log.info("  full GNN            ROC-AUC %.4f", m["eval_roc_auc"])
    log.info("  message-passing gain        %+.4f PR-AUC (%+.1f%%)",
             m["eval_pr_auc"] - abl["eval_pr_auc"],
             100 * (m["eval_pr_auc"] - abl["eval_pr_auc"]) / abl["eval_pr_auc"])
    log.info("  best epoch %d / %d run", m["best_epoch"], m["epochs_run"])

    # Embedding publication path: exactly what train_gnn_model.py will persist.
    with torch.no_grad():
        z = model.embed_users(graph["data"]).numpy().astype(np.float32)
    blob = z[0].tobytes()
    assert np.array_equal(np.frombuffer(blob, dtype=np.float32), z[0])
    log.info("")
    log.info("  embeddings  shape %s  -> %d bytes/user for GNN_UserEmbeddings.Embedding",
             z.shape, len(blob))

    # Decoder-only artifact: the sole file integrity-check downloads.
    dec_params = sum(p.numel() for p in model.decoder.parameters())
    enc_params = sum(p.numel() for p in model.encoder.parameters())
    log.info("  decoder %s params (ships to inference), encoder %s params (stays in the job)",
             f"{dec_params:,}", f"{enc_params:,}")

    ok = m["eval_pr_auc"] > 2 * base and m["eval_pr_auc"] > abl["eval_pr_auc"]
    log.info("")
    log.info("VERDICT: %s", "PASS" if ok else "INVESTIGATE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
