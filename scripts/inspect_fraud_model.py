"""
inspect_fraud_model.py
======================
Peek inside a random_forest_tenant_<N>.pkl without running the full
training job.  Prints all metadata and a feature importance table.

Usage:
    # Direct path to any pkl file:
    python scripts/inspect_fraud_model.py my_pickle.pkl

    # By tenant ID (looks in fraud-analytics-job/Output/):
    python scripts/inspect_fraud_model.py --tenant 1

    # Download fresh from blob first, then inspect:
    python scripts/inspect_fraud_model.py --tenant 1 --from-blob

Environment variables (only needed for --from-blob):
    AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY, MODEL_CONTAINER (default: ml-models)
"""

import argparse
import os
import pickle
import sys
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "backend"))

from dotenv import load_dotenv  # noqa: E402 - backend path is established above
load_dotenv(_repo_root / "backend" / ".env")


def load_from_file(tenant_id: int) -> dict:
    candidates = [
        _repo_root / "fraud-analytics-job" / "Output" /
            f"random_forest_tenant_{tenant_id}.pkl",
        Path(f"random_forest_tenant_{tenant_id}.pkl"),
    ]
    for path in candidates:
        if path.exists():
            print(f"Loading from file: {path}\n")
            with open(path, "rb") as f:
                return pickle.load(f)
    raise FileNotFoundError(
        f"No local pkl found for tenant {tenant_id}. "
        f"Tried: {[str(p) for p in candidates]}\n"
        f"Run with --from-blob to download from Azure Storage."
    )


def load_from_blob(tenant_id: int) -> dict:
    from azure.storage.blob import BlobServiceClient

    account   = os.environ["AZURE_STORAGE_ACCOUNT"]
    key       = os.getenv("AZURE_STORAGE_KEY")
    container = os.getenv("MODEL_CONTAINER", "ml-models")
    blob_name = f"random_forest_tenant_{tenant_id}.pkl"

    if key:
        client = BlobServiceClient(
            f"https://{account}.blob.core.windows.net", credential=key
        )
    else:
        from azure.identity import DefaultAzureCredential
        client = BlobServiceClient(
            f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )

    print(f"Downloading blob: {container}/{blob_name} ...")
    data = client.get_blob_client(container=container, blob=blob_name) \
                 .download_blob().readall()
    print(f"Downloaded {len(data):,} bytes\n")
    return pickle.loads(data)


def _print_model_section(label: str, rf, scaler, cols: list) -> None:
    print("=" * 60)
    print(f"{label} — RANDOM FOREST")
    print("=" * 60)
    if rf is None:
        print("  (no model trained yet — insufficient labelled data)")
        print()
        return
    print(f"  n_estimators   : {rf.n_estimators}")
    print(f"  max_depth      : {rf.max_depth}")
    print(f"  n_features_in_ : {rf.n_features_in_}")
    print(f"  classes_       : {rf.classes_}")
    print()

    print("=" * 60)
    print(f"{label} — STANDARD SCALER  (mean / std per feature)")
    print("=" * 60)
    for col, m, s in zip(cols, scaler.mean_, scaler.scale_):
        print(f"  {col:<38}  mean={m:>10.4f}   std={s:>10.4f}")
    print()

    print("=" * 60)
    print(f"{label} — FEATURE IMPORTANCES  (sorted)")
    print("=" * 60)
    pairs = sorted(zip(cols, rf.feature_importances_), key=lambda x: x[1], reverse=True)
    for col, imp in pairs:
        bar = "█" * int(imp * 80)
        print(f"  {col:<38}  {imp:.6f}  {bar}")
    print()


def inspect(model_data: dict) -> None:
    mean = model_data.get("amount_mean", "n/a")
    std  = model_data.get("amount_std",  "n/a")
    cfr  = model_data.get("category_fraud_rate", {})
    gfr  = model_data.get("global_fraud_rate", "n/a")

    print("=" * 60)
    print("PKL METADATA")
    print("=" * 60)
    print(f"  amount_mean          : {mean:.4f}" if isinstance(mean, float) else f"  amount_mean : {mean}")
    print(f"  amount_std           : {std:.4f}"  if isinstance(std,  float) else f"  amount_std  : {std}")
    print(f"  global_fraud_rate    : {gfr:.4f}"  if isinstance(gfr,  float) else f"  global_fraud_rate : {gfr}")
    print(f"  category_fraud_rate  : {cfr}")
    print()

    _print_model_section(
        "P2P",
        model_data["p2p_model"],
        model_data["p2p_scaler"],
        model_data["p2p_feature_columns"],
    )
    _print_model_section(
        "APPROVER",
        model_data["appr_model"],
        model_data["appr_scaler"],
        model_data["appr_feature_columns"],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pkl", nargs="?", help="Direct path to a .pkl file")
    parser.add_argument("--tenant",    type=int, default=1)
    parser.add_argument("--from-blob", action="store_true",
                        help="Download fresh from Azure Blob Storage before inspecting")
    args = parser.parse_args()

    if args.pkl:
        path = Path(args.pkl)
        if not path.exists():
            print(f"Error: file not found: {path}")
            sys.exit(1)
        print(f"Loading from file: {path}\n")
        with open(path, "rb") as f:
            model_data = pickle.load(f)
    elif args.from_blob:
        model_data = load_from_blob(args.tenant)
    else:
        model_data = load_from_file(args.tenant)

    inspect(model_data)


if __name__ == "__main__":
    main()
