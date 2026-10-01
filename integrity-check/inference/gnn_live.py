"""Normal live encoder path. Never substitutes weekly embeddings on failure."""
import hashlib
import io
import json
import time
import torch

from integrity_engine.artifact_paths import gnn_bundle_prefix
from integrity_engine.gnn.encoder import HeteroEncoder
from integrity_engine.gnn.live_graph import LIVE_ENCODING_CONTRACT, build_live_graph_inputs


def load_encoder(head, tenant_id, read_blob):
    """Load and verify once per cached immutable decoder bundle."""
    if head.get("inference_contract") != LIVE_ENCODING_CONTRACT:
        raise ValueError("Unsupported live GNN inference contract")
    if "_live_encoder" in head:
        return head["_live_encoder"], False
    prefix = gnn_bundle_prefix(
        tenant_id, head["model_version"], system="awards"
    )
    manifest = json.loads(read_blob(f"{prefix}/manifest.json"))
    if (manifest.get("tenant_id") != tenant_id or
        manifest.get("model_version") != head["model_version"] or
        manifest.get("inference_contract") != LIVE_ENCODING_CONTRACT or
        manifest.get("graph_snapshot_id") != head["graph_snapshot_id"]):
        raise ValueError("Live GNN manifest identity mismatch")
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    if len(artifacts) != len(manifest["artifacts"]):
        raise ValueError("Duplicate live GNN artifact roles")
    descriptor = artifacts["serving_encoder"]
    if descriptor["relative_path"] != "serving/encoder.pt":
        raise ValueError("Invalid live GNN encoder path")
    decoder = artifacts["serving_decoder"]
    if (decoder["relative_path"] != "serving/decoder.pt" or
        decoder["sha256"] != head["_artifact_sha256"] or
        int(decoder["size_bytes"]) != head["_artifact_size_bytes"]):
        raise ValueError("Live GNN decoder does not match its manifest")
    raw = read_blob(f"{prefix}/serving/encoder.pt")
    if (len(raw) != int(descriptor["size_bytes"]) or
        hashlib.sha256(raw).hexdigest() != descriptor["sha256"]):
        raise ValueError("Live GNN encoder size/hash mismatch")
    artifact = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    for key in ("model_version", "architecture", "graph_snapshot_id", "feature_schema_version", "emb_dim"):
        if artifact.get(key) != head.get(key):
            raise ValueError(f"Live GNN encoder identity mismatch: {key}")
    if artifact.get("inference_contract") != LIVE_ENCODING_CONTRACT:
        raise ValueError("Live GNN encoder contract mismatch")
    if artifact.get("tenant_id") != tenant_id:
        raise ValueError("Live GNN encoder tenant mismatch")
    encoder = HeteroEncoder(hidden_dim=int(artifact["hidden_dim"]),
                           out_dim=int(artifact["emb_dim"]),
                           num_layers=int(artifact["num_layers"]),
                           architecture=artifact["architecture"])
    encoder.load_state_dict(artifact["encoder_state_dict"], strict=True)
    encoder.eval()
    encoder.requires_grad_(False)
    head["_live_encoder"] = encoder
    return encoder, True


def refresh_embeddings(head, users, history, target, encoder):
    started = time.perf_counter()
    inputs = build_live_graph_inputs(users, history, target, head, num_layers=len(encoder.convs))
    build_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    with torch.inference_mode():
        z = encoder(inputs.x_dict, inputs.edge_index_dict)["user"]
    encoder_ms = (time.perf_counter() - started) * 1000
    endpoints = {int(target[key]): z[inputs.user_index[int(target[key])]].numpy().copy()
                 for key in ("NominatorId", "BeneficiaryId")}
    return endpoints, {
        "contract": LIVE_ENCODING_CONTRACT,
        "history_nomination_count": inputs.history_nomination_count,
        "message_passing_nomination_count": len(inputs.nomination_ids),
        "user_count": len(inputs.user_index),
        "graph_build_ms": round(build_ms, 3),
        "encoder_ms": round(encoder_ms, 3),
    }
