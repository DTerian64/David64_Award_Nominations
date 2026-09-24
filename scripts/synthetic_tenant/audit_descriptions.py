"""Read-only embedding audit of the generated corpus's Copy-Paste clusters.

This optional check uses the same sentence-transformer as Graph Analytics.
It requires the model to be cached locally; it never queries SQL or downloads
model weights. Run after the fast seeder dry run and before resetting a tenant.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json

import numpy as np
from sentence_transformers import SentenceTransformer

from .scenarios import DIRECTORY_SEED, generate_nominations, generate_users
from .seed_synthetics_inc import DEFAULT_AS_OF, DEFAULT_SEED


def audit(
    seed: int, as_of: date, threshold: float = 0.92, sample_step: int = 15,
) -> dict:
    if sample_step < 1:
        raise ValueError("sample_step must be positive")
    nominations = generate_nominations(generate_users(DIRECTORY_SEED), seed, as_of)
    full_counts = Counter(row.description for row in nominations)
    nominations = nominations[::sample_step]
    texts = [row.description for row in nominations]
    model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
    embeddings = np.asarray(model.encode(
        texts, normalize_embeddings=True, batch_size=64,
        show_progress_bar=False,
    ), dtype=np.float32)
    parent = list(range(len(texts)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    exact_pairs = 0
    nonexact_pairs = 0
    cross_category_pairs = 0
    for start in range(0, len(texts), 256):
        similarities = embeddings[start:start + 256] @ embeddings.T
        for local_index in range(similarities.shape[0]):
            left = start + local_index
            hits = np.flatnonzero(similarities[local_index, left + 1:] >= threshold)
            for offset in hits:
                right = left + 1 + int(offset)
                parent[find(left)] = find(right)
                exact_pairs += texts[left] == texts[right]
                nonexact_pairs += texts[left] != texts[right]
                cross_category_pairs += (
                    nominations[left].category_name
                    != nominations[right].category_name
                )
    sizes = Counter(find(index) for index in range(len(texts)))
    clusters = [size for size in sizes.values() if size >= 3]
    return {
        "full_nomination_count": sum(full_counts.values()),
        "full_unique_description_count": len(full_counts),
        "full_exact_reuse_rows": sum(
            count for count in full_counts.values() if count > 1
        ),
        "embedding_sample_step": sample_step,
        "embedding_sample_count": len(texts),
        "similarity_threshold": threshold,
        "exact_pairs_above_threshold": exact_pairs,
        "nonexact_pairs_above_threshold": nonexact_pairs,
        "cross_category_pairs_above_threshold": cross_category_pairs,
        "clusters_at_least_three": len(clusters),
        "largest_cluster": max(clusters, default=0),
        "largest_ten_clusters": sorted(clusters, reverse=True)[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    parser.add_argument("--threshold", type=float, default=0.92)
    parser.add_argument(
        "--sample-step", type=int, default=15,
        help="Embed every Nth nomination; 1 audits the full corpus and is slow on CPU.",
    )
    args = parser.parse_args()
    print(json.dumps(audit(
        args.seed, args.as_of, args.threshold, args.sample_step
    ), indent=2))


if __name__ == "__main__":
    main()
