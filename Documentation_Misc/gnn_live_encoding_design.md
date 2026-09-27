# Normal live GNN encoding

Implemented contract: `gnn-live-causal-encoder-v1`. New shared multi-head v4
bundles use this as their normal scoring path, not a configurable second mode.
The trained encoder, decoder, scalers and temporal-validation calibration are
frozen; their inputs are refreshed. No retraining happens during nomination
processing. Graph Analytics and its windows remain independent.

## Causal scoring

Read the tenant roster and eligible raw nominations within the serving model's
`causal_context_window_days`, strictly before `(NominationDate, NominationId)`.
Do not use the Graph Analytics snapshot or findings. Preserve the existing
HRBP-confirmed rejected-fraud eligibility contract. SQL filters history and the
shared builder independently excludes the target, future and expired rows.

Run the exact dependency neighborhood for the encoder's recorded depth. No
neighbors are sampled, no count limit is introduced, and behavioral aggregates
and historical nomination features still use the full causal history. Tests
compare endpoint embeddings with full-tenant encoding for GraphSAGE, GraphConv
and GATv2, at depths one through three. The normal v4 encoder has two layers.

The decoder uses refreshed endpoint embeddings and the current target feature
row. Overall and all active pattern heads retain their separate calibration and
existing routing semantics. A missing, incompatible or corrupt live encoder
returns GNN unavailable; it never silently scores with cached weekly embeddings.

## Fitting, temporal evaluation and publication

Weights are fitted on historical fold graphs. Architecture validation,
calibration and final testing now replay each nomination using causal graphs at
its own timestamp, through the same feature builders and encoder as inference.
This measures the encoder's inductive use on newly available historical edges;
it does not insert the target edge into its own graph or update weights using
evaluation labels. Raw-feature MLP and engineered-graph MLP remain independent
non-serving controls. The engineered baseline receives refreshed behavioral
aggregates without learned message passing.

Offline replay reuses causal historical node features where the lower window
boundary is unchanged. It retains one feature matrix, not a full graph for each
target. Tests verify cached replay and direct live reconstruction agree,
including expiration boundaries.

Publish the selected model with its **training-fitted** scalers and category
amount statistics. Do not fit replacement preprocessing on the publication
graph. The existing publication graph and SQL embeddings remain useful audit
artifacts; the new scoring path does not depend on those SQL embeddings.

The manifest, encoder and decoder carry the live inference contract. The worker
verifies tenant/model/snapshot identities, feature schema, encoder/decoder
descriptors and hashes, then caches the frozen encoder with the versioned head.
Historical artifacts without this contract retain their original inference
semantics for rollback; they are not relabelled as live-validated models.

## Latency and observability

Every successful live result stores `live_encoding` and `inference_timings` in
`GnnResultJson`, including cold encoder load, SQL history read, graph preparation,
encoder, decoder and total assessment time. History count and message-passing
nomination count are reported separately. The decoder time includes active
pattern heads. Engine windows and scoring thresholds are unchanged.

Temporal evaluation records per-nomination compute p95 in `inference_ms`, with
`inference_latency_basis`, median and complete replay time alongside it. The
existing inference budget is applied to that per-nomination latency, not the
elapsed time for thousands of final-test nominations. These offline times exclude
SQL, blob downloads and worker startup.

Run the repeatable, read-only benchmark:

```powershell
python scripts/benchmark_gnn_live_inference.py --nominations 7500 15000 --repeats 5 --threads 1
```

It uses random initialized weights for timing only, not model-quality evaluation.
The generated graph has stable working relationships; other densities can cost
more. Local compute is not an Azure SLA. Measure warm/cold p50/p95 from deployed
timings before changing worker resources. In particular, artifact download,
process/model imports, SQL and competition for CPU are not estimated by the
local graph benchmark.

### Local measurement, 2026-09-27

400 users, two message-passing layers, 64 embedding dimensions, one CPU thread,
12 warm repetitions per architecture (PyTorch 2.12.0 CPU). These are synthetic
timing graphs, not measured tenant SQL data or model-quality results.

| Architecture | 7,500 history rows, warm median | 15,000 history rows, warm median | 15,000 rows, observed p95 | Encoder alone, 15,000 rows |
| --- | ---: | ---: | ---: | ---: |
| GraphSAGE | 159 ms | 389 ms | 422 ms | 4 ms |
| GCN (GraphConv) | 175 ms | 352 ms | 463 ms | 4 ms |
| GATv2 | 265 ms | 456 ms | 684 ms | 12 ms |

Totals include graph preparation, target features and the overall decoder.
Active pattern decoders are included in production telemetry but not in this
benchmark. The old decoder-only compute baseline is below 0.2 ms; it excludes
the old SQL and causal-feature work, so subtracting it does **not** give an exact
end-to-end overhead. The practical local estimate is roughly 0.35–0.46 seconds
of warm compute at 15,000 history rows, plus unmeasured SQL and cold-load costs.
The small-sample observed p95 is descriptive, not a service guarantee.

The worker image now defaults OpenMP/MKL to one thread, matching its existing
one-vCPU allocation. No CPU/memory allocation increase is made. Larger or denser
graphs and Azure CPU contention still require deployed measurement.

## Deployment order

1. Deploy the shared core and live-capable integrity-check worker (now including
   PyTorch Geometric); no SQL schema or tenant policy migration is required.
2. Deploy the updated analytics job and retrain/evaluate the existing corpus.
3. A successful admission atomically publishes a live-contract serving bundle.
   Existing bundles remain unchanged until that succeeds.
4. Check live timings, matching contract/provenance, causal exclusion and
   prediction behavior before the v6 corpus experiment. No automatic reset or
   reseeding is part of this change.

## GNNExplainer consequence

The live graph can differ from the immutable weekly graph. GNNExplainer must
reproduce the **scoring-time graph and refreshed embeddings**, not explain the
weekly snapshot and claim it produced the new score. Scoring-time input
preservation/reproduction is required before enabling attribution execution.
The current unimplemented attribution worker is not enabled by this change.
