# Graph candidate-edge Ring pruning strategy

**Status:** Implemented; pending deployment and refreshed Graph snapshots  
**Applies to:** `integrity-engine-core`, `fraud-analytics-job`, `integrity-check`, and the future ELCE worker  
**Last updated:** 2026-09-07

## Purpose

At nomination time, Graph Analytics evaluates whether the candidate nomination
edge closes a directed Ring. For a nomination `A -> B`, the evaluator searches
the historical graph for a return path `B -> ... -> A`. A path containing two
through seven historical edges creates a Ring of three through eight users when
the candidate edge is added.

Dense tenant graphs can contain a combinatorial number of simple paths. The
current best-first search can exceed 100,000 generated states and return Graph
Analytics as unavailable with `EVALUATION_LIMIT`. This document defines a
deterministic pruning contract that preserves concrete evidence, bounds
production work, and exposes evaluation completeness for future ELCE use.

## Terminology

- **Candidate edge:** The nomination currently being evaluated.
- **Historical return path:** A directed path from the candidate beneficiary
  back to the candidate nominator.
- **Candidate Ring:** The historical return path plus the candidate edge.
- **Graph Finding Score:** The continuous 0–100 score of one detector finding.
- **Graph Nomination Score:** The highest relevant Graph Finding Score for the
  current nomination.
- **Complete search:** Every feasible candidate Ring was either evaluated or
  eliminated by an exact pruning rule.
- **Bounded search:** The configured work budget was reached after at least one
  concrete candidate Ring was proven.

## Evidence invariants

Pruning must not change the underlying evidence contract:

1. Use only nominations from the same tenant snapshot.
2. Include only `Pending`, `Approved`, and `Paid` nominations.
3. Include only nominations created before the candidate evaluation time.
4. Exclude the candidate nomination itself from the historical graph.
5. Search only for the historical return path from beneficiary to nominator.
6. Require a simple path: a user cannot occur twice in the historical path.
7. Permit candidate Rings containing three through eight users.
8. Every returned Ring must include its actual user path and supporting
   nomination IDs. A score bound is never presented as observed evidence.

## Policy ownership

The search budget is a versioned Graph Analytics policy property, not an
integrity-check container setting. The Ring detector policy will contain:

```json
{
  "pattern_type": "Ring",
  "candidate_evaluation": {
    "max_states": 100000,
    "max_ring_size": 8,
    "limit_strategy": "BEST_EVIDENCE"
  }
}
```

`candidate_evaluation` is separate from the detector's scoring `parameters`:

- `parameters` defines the score formula, including weights and reference
  values.
- `candidate_evaluation` defines how the engine searches for evidence.

The active policy version is embedded in the Graph inference snapshot. Both
production inference and ELCE therefore evaluate against the same versioned
search and scoring contract. `GRAPH_RING_MAX_STATES` will not be a Terraform or
container environment variable.

Policy validation must require:

- `max_states` to be a positive whole number.
- `max_ring_size` to be between 3 and the engine hard safety maximum of 8.
- `limit_strategy` to be a supported enum value.

Publishing any of these changes creates a new policy version and requires a new
Graph snapshot before that policy can serve inference.

## Ordered pruning rules

The following order is part of the deterministic evaluation contract.

### 1. Aggregate eligible directed edges

Apply the evidence invariants and collapse parallel nominations into one
directed edge carrying:

- Supporting nomination IDs.
- Total amount.
- Nomination count.

This preserves scoring inputs without repeatedly traversing parallel edges.

### 2. Build the candidate-specific feasible corridor

Calculate bounded hop distances:

- Forward distance from the beneficiary.
- Reverse distance to the nominator.

A node is retained only when it is reachable from the beneficiary, can reach
the nominator, and satisfies:

```text
forward_distance + reverse_distance <= maximum historical edges
```

All other nodes and edges are removed before path enumeration. This is an exact
pruning rule: removed nodes cannot participate in a valid candidate Ring.

If the nominator is unreachable within the allowed historical-edge count, the
result is an exact no-Ring result and no optimization search is needed.

### 3. Seed the search with concrete evidence

Before optimizing the score:

1. Evaluate all three-person Rings using the intersection of the beneficiary's
   outgoing neighbours and the nominator's incoming neighbours.
2. Use deterministic breadth-first traversal to retain a shortest valid Ring
   when no three-person Ring exists.

This guarantees that once Ring existence is established, the engine already
has one real path with full lineage. Later work-budget exhaustion cannot erase
that evidence or turn it into an unavailable result.

### 4. Explore in deterministic best-first order

Order partial paths by:

1. Highest admissible score bound.
2. Fewer users.
3. Greater accumulated exposure.
4. Greater accumulated nomination count.
5. Lexicographic user-ID path.

No random ordering, sampling, or database row order may affect the result.

### 5. Use node- and depth-specific admissible bounds

For every partial path, calculate the best score it could still achieve for
each feasible completion length. The bound accounts for:

- The current node.
- Remaining hops to the nominator.
- Maximum attainable additional amount.
- Maximum attainable additional nomination count.
- The best compactness possible at that completion length.
- Score signal caps and the detector's configured maximum score.

The bound may relax the simple-path constraint to remain inexpensive, but it
must never underestimate a feasible completion. A branch can be removed only
when its maximum possible score cannot improve the current winner.

### 6. Prune equal-score branches

Discard a branch when:

```text
branch_upper_bound <= best_completed_ring_score
```

Equal-score paths cannot improve the Graph Finding Score or its severity.
Stable traversal order determines which equally scoring Ring supplies the
displayed evidence. We do not enumerate thousands of equivalent Rings solely
to obtain a different tie winner.

### 7. Apply exact state dominance

Two partial paths are comparable for dominance only when they have the same:

- Current user.
- Path length.
- Visited-user set.

A state is dominated when another comparable state has both an equal-or-greater
total amount and an equal-or-greater nomination count. The dominated state
cannot unlock a different continuation or produce a higher score. Stable path
ordering resolves exact ties.

### 8. Stop at the configured maximum score

Return immediately when a completed Ring reaches the detector's configured
`maximum_score`. No unexplored Ring can improve the numeric result. The evidence
remains deterministic because the traversal order is deterministic.

### 9. Enforce the versioned state budget

Count generated and visited states consistently. When
`candidate_evaluation.max_states` is reached:

- If no return path exists, return the already proven exact no-Ring result.
- If a Ring was proven, return the best concrete Ring found under the
  `BEST_EVIDENCE` strategy.
- Do not manufacture a score from an upper bound.
- Do not discard proven evidence and return Graph Analytics as unavailable.

## Result contract

Candidate Ring results must expose search completeness in addition to the
existing path, lineage, score, and severity:

```json
{
  "search_status": "COMPLETE",
  "search_complete": true,
  "score_semantics": "EXACT",
  "configured_max_states": 100000,
  "states_generated": 842,
  "states_visited": 311,
  "paths_considered": 12,
  "pruned_unreachable": 1840,
  "pruned_by_bound": 376,
  "pruned_by_dominance": 24,
  "remaining_score_upper_bound": null
}
```

A budget-limited result uses:

```json
{
  "search_status": "BOUNDED",
  "search_complete": false,
  "score_semantics": "LOWER_BOUND",
  "remaining_score_upper_bound": 100.0
}
```

The returned score of a bounded result belongs to the concrete displayed Ring.
It is a lower bound on the best possible Ring score; the remaining upper bound
is diagnostic and is never substituted for evidence.

## Production and ELCE behavior

### Production inference

Production uses the policy's `limit_strategy`. With `BEST_EVIDENCE`, a bounded
result remains available and participates in nomination scoring using only its
proven score. Nomination logs and HRBP evidence must visibly identify the result
as bounded.

### ELCE

ELCE must require `search_complete=true` before treating a Ring result as an
exact counterfactual. If evaluation is bounded, the ELCE worker increases its
offline work budget or reports that the requested counterfactual could not be
proven exactly. It must not silently use a lower-bound score as an exact result.

Production and ELCE use the same shared evaluator and policy; they differ only
in whether the caller accepts a bounded result.

## Explicitly rejected pruning approaches

The implementation must not use:

- Random sampling.
- An arbitrary top-N neighbour cutoff.
- A beam width that silently discards feasible paths.
- Database-return order as a tie breaker.
- A higher container environment limit that merely postpones the failure.
- An unexplored upper bound as though it were an observed finding score.

These approaches can silently bias results or prevent reproducible ELCE
evaluation.

## Model Setup presentation

The Ring detector's Graph Analytics scoring-policy view will show a separate
**Candidate evaluation** section:

```text
Maximum search states    100,000
Maximum Ring size        8
Limit strategy           Best concrete evidence
```

Administrators can edit these values through a policy draft. Data Scientists
retain the read-only inspection view.

## Acceptance criteria

1. A graph with no feasible return path produces an exact no-Ring result.
2. Existing small-graph tests retain the same winning scores and lineage.
3. Candidate evaluation is identical across repeated runs and input row order.
4. A dense graph that previously exceeded 100,000 states returns either an
   exact result or a marked bounded result, not `UNKNOWN` after proving a Ring.
5. Every returned score is backed by an actual path and nomination lineage.
6. Exact pruning never removes a path capable of beating the current winner.
7. Equal maximum-score paths terminate deterministically without exhaustive tie
   enumeration.
8. Policy inspection exposes candidate-evaluation settings separately from the
   scoring formula.
9. Policy publication, snapshot metadata, nomination logs, and the future ELCE
   result all report the same policy version and evaluation settings.
10. ELCE refuses to represent a bounded Ring score as an exact counterfactual.

## Implemented components

1. The Graph policy contract and validation include Ring candidate-evaluation
   settings.
2. Policy persistence and Model Setup expose those controls separately from
   scoring parameters.
3. The weekly job embeds the controls in each Graph inference snapshot.
4. `integrity-engine-core` applies the feasible corridor, deterministic seed,
   admissible score bounds, exact dominance rule, and maximum-score stop.
5. Production returns marked best-evidence lower bounds instead of making Graph
   unavailable after concrete Ring evidence has been established.
6. Structured candidate-evaluation diagnostics flow into nomination logs and
   the canonical decision contract.
7. ELCE callers can require a complete result and receive an explicit
   evaluation-limit error when exactness cannot be proven within the budget.
8. Regression coverage includes policy validation, bounded production results,
   ELCE completeness, and input-order determinism.

Deployment requires schema migration `0057` followed by a Graph Analytics run
so each new inference snapshot carries the policy-owned controls.
