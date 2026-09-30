"""Offline CPU latency benchmark; never connects to SQL/Azure or changes data.

Random initialized weights measure compute, not accuracy. Separates graph
assembly, encoder, decoder and cached SQL-embedding-equivalent baseline.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrity-engine-core" / "src"))
import numpy as np
import torch
from integrity_engine.gnn.encoder import HeteroEncoder
from integrity_engine.gnn.features import USER_FEATURE_COLUMNS, NOMINATION_FEATURE_COLUMNS, build_nomination_features
from integrity_engine.gnn.live_graph import build_live_graph_inputs, target_causal_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nominations", type=int, nargs="+", default=[2000, 7500, 15000])
    parser.add_argument("--users", type=int, default=400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if min(args.users, args.repeats, args.threads, *args.nominations) < 1 or args.users < 2:
        parser.error("Counts must be positive and users at least two")
    torch.set_num_threads(args.threads)
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    users = [{"UserId": i, "TenantId": 5} for i in range(1, args.users + 1)]
    prep = {"user_feature_columns": USER_FEATURE_COLUMNS,
            "nomination_feature_columns": NOMINATION_FEATURE_COLUMNS,
            "user_scaler_mean": [0.] * 3, "user_scaler_std": [1.] * 3,
            "nomination_scaler_mean": [0.] * len(NOMINATION_FEATURE_COLUMNS),
            "nomination_scaler_std": [1.] * len(NOMINATION_FEATURE_COLUMNS),
            "category_amount_stats": {"global": {"median": 500., "scale": 200.}},
            "causal_context_window_days": 365}
    report = {"torch": torch.__version__, "threads": args.threads,
              "note": "Local compute only; excludes SQL, blob downloads, process startup and Azure contention",
              "results": []}
    for count in args.nominations:
        rng = random.Random(42)
        rows = []
        for i in range(count):
            source = rng.randrange(1, args.users + 1)
            # Stable working relationships rather than a uniformly dense graph.
            destination = ((source - 1 + rng.randrange(1, min(9, args.users))) % args.users) + 1
            rows.append(dict(NominationId=i + 1, NominatorId=source, BeneficiaryId=destination,
                             CategoryId=1 + i % 5, Amount=rng.randrange(100, 1000), Status="Paid",
                             CreatedAt=now - timedelta(seconds=rng.randrange(1, 365 * 86400))))
        target = dict(NominationId=count + 1, NominatorId=1, BeneficiaryId=2, CreatedAt=now,
                      Amount=500., CategoryId=1, Status="Pending")
        start = time.perf_counter()
        inputs = build_live_graph_inputs(users, rows, target, prep, num_layers=2)
        cold_build = (time.perf_counter() - start) * 1000
        for architecture in ("graphsage", "gcn", "gatv2"):
            torch.manual_seed(42)
            encoder = HeteroEncoder(architecture=architecture).eval()
            decoder = torch.nn.Sequential(torch.nn.Linear(128 + len(NOMINATION_FEATURE_COLUMNS), 64),
                                          torch.nn.ReLU(), torch.nn.Linear(64, 32), torch.nn.ReLU(),
                                          torch.nn.Linear(32, 1)).eval()
            features = torch.zeros((1, len(NOMINATION_FEATURE_COLUMNS)))
            with torch.inference_mode():
                start = time.perf_counter()
                z = encoder(inputs.x_dict, inputs.edge_index_dict)["user"]
                cold_encoder = (time.perf_counter() - start) * 1000
                timings = []
                for _ in range(args.repeats):
                    start = time.perf_counter()
                    inputs = build_live_graph_inputs(users, rows, target, prep, num_layers=2)
                    built = time.perf_counter()
                    z = encoder(inputs.x_dict, inputs.edge_index_dict)["user"]
                    encoded = time.perf_counter()
                    features = torch.from_numpy(build_nomination_features(
                        [target], prep["category_amount_stats"], now.date(), historical=False,
                        context_rows=target_causal_history(rows, target, 365), causal_window_days=365,
                    ))
                    featured = time.perf_counter()
                    decoder(torch.cat((z[0:1], z[1:2], features), dim=1))
                    finished = time.perf_counter()
                    timings.append([(built-start)*1000, (encoded-built)*1000, (finished-featured)*1000,
                                    (finished-start)*1000, (featured-encoded)*1000])
                baseline = []
                for _ in range(100):
                    start = time.perf_counter()
                    decoder(torch.cat((z[0:1], z[1:2], features), dim=1))
                    baseline.append((time.perf_counter()-start)*1000)
            median = np.median(timings, axis=0)
            report["results"].append({"architecture": architecture, "users": args.users,
                "nominations": count, "first_graph_build_ms": round(cold_build, 2),
                "message_passing_nominations": len(inputs.nomination_ids),
                "first_encoder_forward_ms": round(cold_encoder, 2),
                "warm_graph_build_ms": round(float(median[0]), 2),
                "warm_encoder_ms": round(float(median[1]), 2), "warm_decoder_ms": round(float(median[2]), 2),
                "warm_target_features_ms": round(float(median[4]), 2),
                "warm_total_median_ms": round(float(median[3]), 2),
                "observed_warm_total_p95_ms": round(float(np.percentile(np.asarray(timings)[:, 3], 95)), 2),
                "decoder_only_median_ms": round(float(np.median(baseline)), 3)})
        print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
