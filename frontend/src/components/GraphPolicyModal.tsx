import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle, RefreshCw, Save, Send, X } from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface PatternPolicy {
  pattern_type: string;
  display_order: number;
  enabled: boolean;
  enabled_for_routing: boolean;
  applicable_roles: string[];
  base_score: number;
  minimum_score: number;
  maximum_score: number;
  parameters: Record<string, number>;
}

interface GraphPolicy {
  policy_id: number;
  policy_version: number;
  status: 'DRAFT' | 'ACTIVE' | 'RETIRED';
  scoring_strategy: string;
  thresholds: { low: number; medium: number; high: number; critical: number };
  detection_window_days: number;
  snapshot_max_age_days: number;
  patterns: PatternPolicy[];
  published_at: string | null;
  published_by: string | null;
}

interface ChangeRequest {
  request_id: number;
  pattern_type: string | null;
  request_text: string;
  status: string;
  requested_at: string;
  requested_by: string;
  admin_response: string | null;
  suggested_parameters: Record<string, unknown> | null;
  supporting_nomination_ids: number[];
}

interface PolicyBundle {
  active_policy: GraphPolicy | null;
  draft_policy: GraphPolicy | null;
  history: GraphPolicy[];
  requests: ChangeRequest[];
  can_edit: boolean;
  can_request: boolean;
}

interface Props {
  impersonatedUPN?: string;
  onClose: () => void;
}

interface SignalFormula {
  key: string;
  name: string;
  expression: (pattern: PatternPolicy) => string;
}

interface DetectorFormula {
  detectionCondition: (pattern: PatternPolicy) => string;
  signals: SignalFormula[];
}

interface CalculatorInput {
  key: string;
  name: string;
  minimum: number;
  maximum?: number;
  step: number;
  defaultValue: (pattern: PatternPolicy) => number;
}

interface CalculatedSignal {
  rawEvidence: string;
  normalized: number;
}

interface DetectorCalculator {
  inputs: CalculatorInput[];
  calculate: (
    pattern: PatternPolicy,
    values: Record<string, number>,
  ) => Record<string, CalculatedSignal>;
}

const label = (value: string) =>
  value.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());

const formatValue = (value: number) => Number.isInteger(value)
  ? value.toLocaleString()
  : value.toLocaleString(undefined, { maximumFractionDigits: 4 });

const parameter = (pattern: PatternPolicy, key: string, fallback: number) =>
  Number(pattern.parameters[key] ?? fallback);

const clamp = (value: number, minimum = 0, maximum = 1) =>
  Math.max(minimum, Math.min(maximum, value));

const inputValue = (
  values: Record<string, number>,
  input: CalculatorInput,
  pattern: PatternPolicy,
) => Number(values[input.key] ?? input.defaultValue(pattern));

const DETECTOR_FORMULAS: Record<string, DetectorFormula> = {
  Ring: {
    detectionCondition: () => 'A directed nomination cycle containing at least 3 people is found.',
    signals: [
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
      {
        key: 'repeat', name: 'Repeat activity',
        expression: () => 'clamp(nominations in the cycle ÷ (people in the cycle × 3), 0, 1)',
      },
      {
        key: 'compactness', name: 'Compactness',
        expression: () => 'clamp(1 − ((people in the cycle − 3) ÷ 5), 0, 1)',
      },
    ],
  },
  BipartiteDenseBlock: {
    detectionCondition: pattern => `Candidate participants share at least ${formatValue(parameter(pattern, 'minimum_shared_neighbors', 2))} neighbors with pairwise Jaccard overlap of at least ${formatValue(parameter(pattern, 'overlap_threshold', 0.6))}. The resulting core must have at least ${formatValue(parameter(pattern, 'minimum_side_size', 2))} people on each side, at least ${formatValue(parameter(pattern, 'minimum_large_side_size', 3))} on one side, ${formatValue(parameter(pattern, 'minimum_edges', 6))} distinct edges, and density of at least ${formatValue(parameter(pattern, 'minimum_density', 0.65))}.`,
    signals: [
      {
        key: 'density', name: 'Density above threshold',
        expression: pattern => {
          const threshold = formatValue(parameter(pattern, 'minimum_density', 0.65));
          return `clamp((actual density − ${threshold}) ÷ max(1 − ${threshold}, 0.001), 0, 1)`;
        },
      },
      {
        key: 'overlap', name: 'Neighbor overlap',
        expression: () => 'clamp(max average pairwise Jaccard overlap, 0, 1)',
      },
      {
        key: 'exclusivity', name: 'Block exclusivity',
        expression: () => 'clamp(mean share of each participant’s edges contained in the block, 0, 1)',
      },
      {
        key: 'repeat', name: 'Repeat edges',
        expression: pattern => `clamp(((nominations ÷ distinct edges) − 1) ÷ ${formatValue(parameter(pattern, 'repeat_reference', 2))}, 0, 1)`,
      },
      {
        key: 'compactness', name: 'Temporal compactness',
        expression: pattern => `clamp(1 − ((activity span days − 1) ÷ ${formatValue(parameter(pattern, 'compactness_reference_days', 14))}), 0, 1)`,
      },
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
    ],
  },
  TemporalBurst: {
    detectionCondition: pattern => `A ${formatValue(parameter(pattern, 'burst_window_days', 3))}-day window contains at least T nominations, where T = max(${formatValue(parameter(pattern, 'minimum_nominations', 8))}, rolling median + ${formatValue(parameter(pattern, 'standard_deviations', 3))} × robust deviation), with at least ${formatValue(parameter(pattern, 'minimum_baseline_days', 21))} days of history. Robust deviation = max(1.4826 × median absolute deviation, square root of the rolling median).`,
    signals: [
      {
        key: 'excess', name: 'Excess above threshold',
        expression: () => 'clamp((observed nominations ÷ max(T, 1)) − 1, 0, 1)',
      },
      {
        key: 'volume', name: 'Burst volume',
        expression: pattern => `clamp(observed nominations ÷ ${formatValue(parameter(pattern, 'count_reference', 20))}, 0, 1)`,
      },
      {
        key: 'participant_concentration', name: 'Participant concentration',
        expression: () => 'clamp(largest nomination count involving one participant ÷ observed nominations, 0, 1)',
      },
      {
        key: 'temporal_compactness', name: 'Temporal compactness',
        expression: () => 'clamp(largest single-day count ÷ observed nominations, 0, 1)',
      },
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
    ],
  },
  SuperNominator: {
    detectionCondition: pattern => `Nomination count is at least T, where T = max(tenant mean + ${formatValue(parameter(pattern, 'standard_deviations', 2))} × σ, ${formatValue(parameter(pattern, 'median_multiplier', 3))} × tenant median, ${formatValue(parameter(pattern, 'minimum_count', 5))}).`,
    signals: [
      {
        key: 'excess', name: 'Excess above threshold',
        expression: () => 'clamp((nomination count ÷ max(T, 1)) − 1, 0, 1)',
      },
      {
        key: 'volume', name: 'Volume',
        expression: () => 'clamp(nomination count ÷ max(2 × T, 1), 0, 1)',
      },
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
    ],
  },
  SuperBeneficiary: {
    detectionCondition: pattern => `Nominations received are at least T, where T = max(tenant mean + ${formatValue(parameter(pattern, 'standard_deviations', 2))} × σ, ${formatValue(parameter(pattern, 'median_multiplier', 3))} × tenant median, ${formatValue(parameter(pattern, 'minimum_count', 5))}), from at least ${formatValue(parameter(pattern, 'minimum_unique_nominators', 4))} distinct nominators.`,
    signals: [
      {
        key: 'excess', name: 'Excess above threshold',
        expression: () => 'clamp((nominations received ÷ max(T, 1)) − 1, 0, 1)',
      },
      {
        key: 'breadth', name: 'Nominator breadth',
        expression: pattern => `clamp(distinct nominators ÷ ${formatValue(parameter(pattern, 'unique_reference', 10))}, 0, 1)`,
      },
      {
        key: 'repeat_concentration', name: 'Repeat concentration',
        expression: () => 'clamp((dominant nominator share − 1 ÷ distinct nominators) ÷ max(1 − 1 ÷ distinct nominators, 0.001), 0, 1)',
      },
      {
        key: 'compactness', name: 'Temporal compactness',
        expression: pattern => `clamp(1 − ((activity span days − 1) ÷ ${formatValue(parameter(pattern, 'compactness_reference_days', 14))}), 0, 1)`,
      },
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
    ],
  },
  Desert: {
    detectionCondition: pattern => `Every member of a manager's team has zero nomination activity, and the team has at least ${formatValue(parameter(pattern, 'minimum_team_size', 3))} members.`,
    signals: [
      {
        key: 'team_size', name: 'Team size',
        expression: pattern => `clamp(team members ÷ ${formatValue(parameter(pattern, 'team_size_reference', 10))}, 0, 1)`,
      },
    ],
  },
  CopyPaste: {
    detectionCondition: pattern => `A cluster contains at least ${formatValue(parameter(pattern, 'minimum_cluster_size', 3))} nominations whose cosine similarity is at least ${formatValue(parameter(pattern, 'similarity_threshold', 0.92))}.`,
    signals: [
      {
        key: 'similarity', name: 'Similarity above threshold',
        expression: pattern => {
          const threshold = formatValue(parameter(pattern, 'similarity_threshold', 0.92));
          return `clamp((average similarity − ${threshold}) ÷ max(1 − ${threshold}, 0.001), 0, 1)`;
        },
      },
      {
        key: 'cluster_size', name: 'Cluster size',
        expression: pattern => `clamp(nominations in cluster ÷ ${formatValue(parameter(pattern, 'cluster_size_reference', 8))}, 0, 1)`,
      },
      {
        key: 'exposure', name: 'Exposure',
        expression: pattern => `clamp(total approved/paid amount ÷ ${formatValue(parameter(pattern, 'amount_reference', 10_000))}, 0, 1)`,
      },
    ],
  },
  HiddenCandidate: {
    detectionCondition: pattern => `An active user's name appears at least ${formatValue(parameter(pattern, 'minimum_mentions', 5))} times in descriptions, while the user never appears as a formal beneficiary.`,
    signals: [
      {
        key: 'mention', name: 'Name mentions',
        expression: pattern => `clamp(name mentions ÷ ${formatValue(parameter(pattern, 'mention_reference', 15))}, 0, 1)`,
      },
    ],
  },
};

const DETECTOR_CALCULATORS: Record<string, DetectorCalculator> = {
  Ring: {
    inputs: [
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
      { key: 'people_in_cycle', name: 'People in cycle', minimum: 3, maximum: 8, step: 1, defaultValue: () => 3 },
      { key: 'nominations_in_cycle', name: 'Nominations in cycle', minimum: 3, step: 1, defaultValue: () => 3 },
    ],
    calculate: (pattern, values) => {
      const amount = Number(values.total_amount);
      const people = Number(values.people_in_cycle);
      const nominations = Number(values.nominations_in_cycle);
      return {
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
        repeat: {
          rawEvidence: `${formatValue(nominations)} nominations ÷ (${formatValue(people)} people × 3)`,
          normalized: clamp(nominations / Math.max(people * 3, 1)),
        },
        compactness: {
          rawEvidence: `${formatValue(people)} people`,
          normalized: clamp(1 - ((people - 3) / 5)),
        },
      };
    },
  },
  BipartiteDenseBlock: {
    inputs: [
      { key: 'nominators', name: 'Nominators in block', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_large_side_size', 3) },
      { key: 'beneficiaries', name: 'Beneficiaries in block', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_side_size', 2) },
      { key: 'distinct_edges', name: 'Distinct nomination edges', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_edges', 6) },
      { key: 'nominations', name: 'Nominations in block', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_edges', 6) },
      { key: 'neighbor_overlap', name: 'Average neighbor overlap (0–1)', minimum: 0, maximum: 1, step: 0.01, defaultValue: pattern => parameter(pattern, 'overlap_threshold', 0.6) },
      { key: 'exclusivity', name: 'Block exclusivity (0–1)', minimum: 0, maximum: 1, step: 0.01, defaultValue: () => 0.75 },
      { key: 'activity_span_days', name: 'Activity span (days)', minimum: 1, step: 1, defaultValue: () => 3 },
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
    ],
    calculate: (pattern, values) => {
      const nominators = Number(values.nominators);
      const beneficiaries = Number(values.beneficiaries);
      const edges = Number(values.distinct_edges);
      const nominations = Number(values.nominations);
      const overlap = Number(values.neighbor_overlap);
      const exclusivity = Number(values.exclusivity);
      const span = Number(values.activity_span_days);
      const amount = Number(values.total_amount);
      const density = edges / Math.max(nominators * beneficiaries, 1);
      const densityThreshold = parameter(pattern, 'minimum_density', 0.65);
      return {
        density: {
          rawEvidence: `${formatValue(edges)} of ${formatValue(nominators * beneficiaries)} possible edges; density ${formatValue(density)}`,
          normalized: clamp(
            (density - densityThreshold) / Math.max(1 - densityThreshold, 0.001),
          ),
        },
        overlap: {
          rawEvidence: `${formatValue(overlap)} average Jaccard overlap`,
          normalized: clamp(overlap),
        },
        exclusivity: {
          rawEvidence: `${formatValue(exclusivity)} mean internal-edge share`,
          normalized: clamp(exclusivity),
        },
        repeat: {
          rawEvidence: `${formatValue(nominations)} nominations on ${formatValue(edges)} edges`,
          normalized: clamp(
            ((nominations / Math.max(edges, 1)) - 1)
            / Math.max(parameter(pattern, 'repeat_reference', 2), 1),
          ),
        },
        compactness: {
          rawEvidence: `${formatValue(span)} day(s)`,
          normalized: clamp(
            1 - ((span - 1) / Math.max(
              parameter(pattern, 'compactness_reference_days', 14), 1,
            )),
          ),
        },
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
      };
    },
  },
  TemporalBurst: {
    inputs: [
      { key: 'observed_nominations', name: 'Nominations in burst window', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_nominations', 8) },
      { key: 'rolling_median', name: 'Historical rolling median', minimum: 0, step: 0.1, defaultValue: () => 3 },
      { key: 'robust_deviation', name: 'Historical robust deviation', minimum: 0, step: 0.1, defaultValue: () => 1.7 },
      { key: 'largest_participant_count', name: 'Most nominations involving one participant', minimum: 0, step: 1, defaultValue: () => 3 },
      { key: 'largest_day_count', name: 'Largest single-day count', minimum: 0, step: 1, defaultValue: () => 5 },
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
    ],
    calculate: (pattern, values) => {
      const observed = Number(values.observed_nominations);
      const expected = Number(values.rolling_median);
      const deviation = Number(values.robust_deviation);
      const participantPeak = Number(values.largest_participant_count);
      const dailyPeak = Number(values.largest_day_count);
      const amount = Number(values.total_amount);
      const threshold = Math.max(
        parameter(pattern, 'minimum_nominations', 8),
        expected + parameter(pattern, 'standard_deviations', 3) * deviation,
      );
      return {
        excess: {
          rawEvidence: `${formatValue(observed)} nominations; T = ${formatValue(threshold)}`,
          normalized: clamp((observed / Math.max(threshold, 1)) - 1),
        },
        volume: {
          rawEvidence: `${formatValue(observed)} nominations`,
          normalized: clamp(observed / Math.max(parameter(pattern, 'count_reference', 20), 1)),
        },
        participant_concentration: {
          rawEvidence: `${formatValue(participantPeak)} of ${formatValue(observed)} nominations`,
          normalized: clamp(participantPeak / Math.max(observed, 1)),
        },
        temporal_compactness: {
          rawEvidence: `${formatValue(dailyPeak)} of ${formatValue(observed)} nominations on busiest day`,
          normalized: clamp(dailyPeak / Math.max(observed, 1)),
        },
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
      };
    },
  },
  SuperNominator: {
    inputs: [
      { key: 'nomination_count', name: 'Nominations sent', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_count', 5) },
      { key: 'tenant_mean', name: 'Tenant mean', minimum: 0, step: 0.1, defaultValue: () => 2 },
      { key: 'tenant_standard_deviation', name: 'Tenant standard deviation (σ)', minimum: 0, step: 0.1, defaultValue: () => 1 },
      { key: 'tenant_median', name: 'Tenant median', minimum: 0, step: 0.1, defaultValue: () => 2 },
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
    ],
    calculate: (pattern, values) => {
      const count = Number(values.nomination_count);
      const mean = Number(values.tenant_mean);
      const deviation = Number(values.tenant_standard_deviation);
      const median = Number(values.tenant_median);
      const amount = Number(values.total_amount);
      const threshold = Math.max(
        mean + parameter(pattern, 'standard_deviations', 2) * deviation,
        parameter(pattern, 'median_multiplier', 3) * median,
        parameter(pattern, 'minimum_count', 5),
      );
      return {
        excess: {
          rawEvidence: `${formatValue(count)} nominations; T = ${formatValue(threshold)}`,
          normalized: clamp((count / Math.max(threshold, 1)) - 1),
        },
        volume: {
          rawEvidence: `${formatValue(count)} nominations; T = ${formatValue(threshold)}`,
          normalized: clamp(count / Math.max(threshold * 2, 1)),
        },
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
      };
    },
  },
  SuperBeneficiary: {
    inputs: [
      { key: 'nomination_count', name: 'Nominations received', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_count', 5) },
      { key: 'tenant_mean', name: 'Tenant mean', minimum: 0, step: 0.1, defaultValue: () => 2 },
      { key: 'tenant_standard_deviation', name: 'Tenant standard deviation (σ)', minimum: 0, step: 0.1, defaultValue: () => 1 },
      { key: 'tenant_median', name: 'Tenant median', minimum: 0, step: 0.1, defaultValue: () => 2 },
      { key: 'unique_nominators', name: 'Distinct nominators', minimum: 1, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_unique_nominators', 4) },
      { key: 'dominant_nominator_count', name: 'Most nominations from one nominator', minimum: 1, step: 1, defaultValue: () => 1 },
      { key: 'activity_span_days', name: 'Activity span (days)', minimum: 1, step: 1, defaultValue: () => 3 },
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
    ],
    calculate: (pattern, values) => {
      const count = Number(values.nomination_count);
      const mean = Number(values.tenant_mean);
      const deviation = Number(values.tenant_standard_deviation);
      const median = Number(values.tenant_median);
      const unique = Math.max(Number(values.unique_nominators), 1);
      const dominant = Number(values.dominant_nominator_count);
      const span = Number(values.activity_span_days);
      const amount = Number(values.total_amount);
      const threshold = Math.max(
        mean + parameter(pattern, 'standard_deviations', 2) * deviation,
        parameter(pattern, 'median_multiplier', 3) * median,
        parameter(pattern, 'minimum_count', 5),
      );
      const concentrationFloor = 1 / unique;
      const dominantShare = dominant / Math.max(count, 1);
      return {
        excess: {
          rawEvidence: `${formatValue(count)} nominations; T = ${formatValue(threshold)}`,
          normalized: clamp((count / Math.max(threshold, 1)) - 1),
        },
        breadth: {
          rawEvidence: `${formatValue(unique)} distinct nominators`,
          normalized: clamp(unique / Math.max(parameter(pattern, 'unique_reference', 10), 1)),
        },
        repeat_concentration: {
          rawEvidence: `${formatValue(dominant)} of ${formatValue(count)} nominations from the dominant nominator`,
          normalized: clamp(
            (dominantShare - concentrationFloor)
            / Math.max(1 - concentrationFloor, 0.001),
          ),
        },
        compactness: {
          rawEvidence: `${formatValue(span)} day(s)`,
          normalized: clamp(
            1 - ((span - 1) / Math.max(
              parameter(pattern, 'compactness_reference_days', 14), 1,
            )),
          ),
        },
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
      };
    },
  },
  Desert: {
    inputs: [
      { key: 'team_members', name: 'People in inactive team', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_team_size', 3) },
    ],
    calculate: (pattern, values) => {
      const members = Number(values.team_members);
      return {
        team_size: {
          rawEvidence: `${formatValue(members)} people`,
          normalized: clamp(members / Math.max(parameter(pattern, 'team_size_reference', 10), 1)),
        },
      };
    },
  },
  CopyPaste: {
    inputs: [
      { key: 'average_similarity', name: 'Average cosine similarity', minimum: 0, maximum: 1, step: 0.01, defaultValue: pattern => parameter(pattern, 'similarity_threshold', 0.92) },
      { key: 'cluster_size', name: 'Nominations in cluster', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_cluster_size', 3) },
      { key: 'total_amount', name: 'Total approved/paid amount ($)', minimum: 0, step: 100, defaultValue: () => 0 },
    ],
    calculate: (pattern, values) => {
      const similarity = Number(values.average_similarity);
      const size = Number(values.cluster_size);
      const amount = Number(values.total_amount);
      const threshold = parameter(pattern, 'similarity_threshold', 0.92);
      return {
        similarity: {
          rawEvidence: `${formatValue(similarity)} average; ${formatValue(threshold)} threshold`,
          normalized: clamp(
            (similarity - threshold) / Math.max(1 - threshold, 0.001),
          ),
        },
        cluster_size: {
          rawEvidence: `${formatValue(size)} nominations`,
          normalized: clamp(size / Math.max(parameter(pattern, 'cluster_size_reference', 8), 1)),
        },
        exposure: {
          rawEvidence: `$${formatValue(amount)}`,
          normalized: clamp(amount / Math.max(parameter(pattern, 'amount_reference', 10_000), 1)),
        },
      };
    },
  },
  HiddenCandidate: {
    inputs: [
      { key: 'name_mentions', name: 'Name mentions', minimum: 0, step: 1, defaultValue: pattern => parameter(pattern, 'minimum_mentions', 5) },
    ],
    calculate: (pattern, values) => {
      const mentions = Number(values.name_mentions);
      return {
        mention: {
          rawEvidence: `${formatValue(mentions)} mentions`,
          normalized: clamp(mentions / Math.max(parameter(pattern, 'mention_reference', 15), 1)),
        },
      };
    },
  },
};

const detectorFormula = (pattern: PatternPolicy): DetectorFormula => {
  const configured = DETECTOR_FORMULAS[pattern.pattern_type];
  if (configured) return configured;
  return {
    detectionCondition: () => 'The detector-specific eligibility condition is met.',
    signals: Object.keys(pattern.parameters)
      .filter(key => key.endsWith('_weight'))
      .map(key => {
        const signal = key.slice(0, -'_weight'.length);
        return {
          key: signal,
          name: label(signal),
          expression: () => 'clamp(detector evidence, 0, 1)',
        };
      }),
  };
};

const detectorCalculator = (pattern: PatternPolicy): DetectorCalculator => {
  const configured = DETECTOR_CALCULATORS[pattern.pattern_type];
  if (configured) return configured;
  const formula = detectorFormula(pattern);
  return {
    inputs: formula.signals.map(signal => ({
      key: signal.key,
      name: `${signal.name} normalized value`,
      minimum: 0,
      maximum: 1,
      step: 0.01,
      defaultValue: () => 0,
    })),
    calculate: (_pattern, values) => Object.fromEntries(
      formula.signals.map(signal => [signal.key, {
        rawEvidence: formatValue(Number(values[signal.key])),
        normalized: clamp(Number(values[signal.key])),
      }]),
    ),
  };
};

const configuredScoreFormula = (
  pattern: PatternPolicy,
  formula: DetectorFormula,
) => {
  const weightedSignals = formula.signals.map(signal =>
    `${signal.name} × ${formatValue(parameter(pattern, `${signal.key}_weight`, 0))}`);
  const terms = [formatValue(pattern.base_score), ...weightedSignals].join(' + ');
  return `clamp(${terms}, ${formatValue(pattern.minimum_score)}, ${formatValue(pattern.maximum_score)})`;
};

const requestHeaders = async (impersonatedUPN?: string) => {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
  if (impersonatedUPN) headers['X-Impersonate-User'] = impersonatedUPN;
  return headers;
};

const NumberInput: React.FC<{
  labelText: string;
  value: number;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}> = ({ labelText, value, disabled, min = 0, max, step = 1, onChange }) => (
  <label className="block text-xs text-gray-500">
    {labelText}
    <input
      type="number"
      value={value}
      disabled={disabled}
      min={min}
      max={max}
      step={step}
      onChange={event => onChange(Number(event.target.value))}
      className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-2 text-sm text-gray-800 disabled:bg-gray-50 disabled:text-gray-500"
    />
  </label>
);

export const GraphPolicyModal: React.FC<Props> = ({ impersonatedUPN, onClose }) => {
  const [bundle, setBundle] = useState<PolicyBundle | null>(null);
  const [draft, setDraft] = useState<GraphPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [requestPattern, setRequestPattern] = useState('');
  const [requestText, setRequestText] = useState('');
  const [requestNominations, setRequestNominations] = useState('');
  const [requestProposal, setRequestProposal] = useState('');
  const [simPattern, setSimPattern] = useState('');
  const [simInputs, setSimInputs] = useState<Record<string, number>>({});
  const [reviewResponses, setReviewResponses] = useState<Record<number, string>>({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/model-analysis/setup/graph-policy`,
        { headers: await requestHeaders(impersonatedUPN) },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail || `HTTP ${response.status}`);
      }
      const next = await response.json() as PolicyBundle;
      setBundle(next);
      setDraft(next.draft_policy ? structuredClone(next.draft_policy) : null);
      if (!simPattern && next.active_policy?.patterns[0]) {
        setSimPattern(next.active_policy.patterns[0].pattern_type);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load the scoring policy');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const policy = draft || bundle?.active_policy || null;
  const orderedPatterns = useMemo(
    () => [...(policy?.patterns || [])].sort(
      (left, right) => left.display_order - right.display_order,
    ),
    [policy],
  );
  const simulationPattern = useMemo(
    () => policy?.patterns.find(item => item.pattern_type === simPattern),
    [policy, simPattern],
  );
  const simulationFormula = useMemo(
    () => simulationPattern ? detectorFormula(simulationPattern) : null,
    [simulationPattern],
  );
  const simulationCalculator = useMemo(
    () => simulationPattern ? detectorCalculator(simulationPattern) : null,
    [simulationPattern],
  );
  const resolvedSimulatorInputs = useMemo(() => {
    if (!simulationPattern || !simulationCalculator) return {};
    return Object.fromEntries(simulationCalculator.inputs.map(input => [
      input.key,
      inputValue(simInputs, input, simulationPattern),
    ]));
  }, [simulationPattern, simulationCalculator, simInputs]);
  const simulatedSignals = useMemo(() => {
    if (!simulationPattern || !simulationCalculator) return {};
    return simulationCalculator.calculate(simulationPattern, resolvedSimulatorInputs);
  }, [simulationPattern, simulationCalculator, resolvedSimulatorInputs]);
  const simulatedContributions = useMemo(() => {
    if (!simulationPattern || !simulationFormula) return [];
    return simulationFormula.signals.map(signal => {
      const normalized = simulatedSignals[signal.key]?.normalized ?? 0;
      const weight = parameter(simulationPattern, `${signal.key}_weight`, 0);
      return {
        ...signal,
        rawEvidence: simulatedSignals[signal.key]?.rawEvidence ?? '—',
        normalized,
        weight,
        contribution: normalized * weight,
      };
    });
  }, [simulationPattern, simulationFormula, simulatedSignals]);
  const simulatedUnclampedScore = useMemo(() => {
    if (!simulationPattern) return 0;
    return simulationPattern.base_score + simulatedContributions.reduce(
      (sum, signal) => sum + signal.contribution,
      0,
    );
  }, [simulationPattern, simulatedContributions]);
  const simulatedScore = useMemo(() => {
    if (!simulationPattern) return 0;
    return Math.max(
      simulationPattern.minimum_score,
      Math.min(simulationPattern.maximum_score, simulatedUnclampedScore),
    );
  }, [simulationPattern, simulatedUnclampedScore]);

  const mutate = async (path: string, method: string, body?: unknown) => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers: await requestHeaders(impersonatedUPN),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({})) as { detail?: string; message?: string };
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      setMessage(result.message || 'Saved');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The change could not be saved');
    } finally {
      setSaving(false);
    }
  };

  const createDraft = () => mutate('/api/admin/setup/graph-policy/draft', 'POST');
  const saveDraft = () => draft && mutate('/api/admin/setup/graph-policy/draft', 'PUT', {
    thresholds: draft.thresholds,
    detection_window_days: draft.detection_window_days,
    snapshot_max_age_days: draft.snapshot_max_age_days,
    patterns: draft.patterns,
  });
  const publishDraft = () => {
    if (window.confirm('Publish this policy version? It will be used by the next weekly Graph Analytics run.')) {
      void mutate('/api/admin/setup/graph-policy/draft/publish', 'POST');
    }
  };

  const updateThreshold = (key: keyof GraphPolicy['thresholds'], value: number) => {
    setDraft(current => current ? {
      ...current, thresholds: { ...current.thresholds, [key]: value },
    } : current);
  };

  const updatePattern = (index: number, update: Partial<PatternPolicy>) => {
    setDraft(current => current ? {
      ...current,
      patterns: current.patterns.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item),
    } : current);
  };

  const submitRequest = async () => {
    const nominations = requestNominations.split(',')
      .map(value => Number(value.trim())).filter(value => Number.isInteger(value) && value > 0);
    await mutate('/api/model-analysis/setup/graph-policy/requests', 'POST', {
      pattern_type: requestPattern || null,
      request_text: requestText,
      supporting_nomination_ids: nominations,
      suggested_parameters: requestProposal.trim()
        ? { proposal: requestProposal.trim() }
        : null,
    });
    setRequestText('');
    setRequestNominations('');
    setRequestProposal('');
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="Graph Analytics scoring policy">
      <div className="mx-auto max-w-7xl overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Graph Analytics scoring policy</h3>
            <p className="mt-0.5 text-xs text-gray-500">Continuous finding scores and the Maximum relevant finding strategy</p>
          </div>
          <button onClick={onClose} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close scoring policy" aria-label="Close scoring policy"><X className="h-5 w-5" /></button>
        </header>

        <div className="space-y-5 p-4 sm:p-6">
          {loading && <div className="flex items-center justify-center gap-2 py-20 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading scoring policy…</div>}
          {error && <div className="flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
          {message && <div className="flex items-start gap-2 rounded-lg bg-green-50 p-3 text-sm text-green-700"><CheckCircle className="mt-0.5 h-4 w-4 shrink-0" />{message}</div>}
          {!loading && !error && !policy && <div className="rounded-lg border border-dashed border-gray-200 px-6 py-12 text-center text-sm text-gray-500">No Graph Analytics scoring policy is available for this organization.</div>}

          {!loading && policy && (
            <>
              <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h4 className="font-semibold text-gray-800">Version {policy.policy_version} · {policy.status === 'DRAFT' ? 'Draft' : 'Active'}</h4>
                    <p className="mt-1 text-sm text-gray-600"><strong>Maximum relevant finding:</strong> the nomination receives the highest applicable routing-enabled finding score.</p>
                  </div>
                  {bundle?.can_edit && !draft && <button onClick={createDraft} disabled={saving} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Create draft</button>}
                  {bundle?.can_edit && draft && (
                    <div className="flex gap-2">
                      <button onClick={saveDraft} disabled={saving} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"><Save className="h-4 w-4" />Save draft</button>
                      <button onClick={publishDraft} disabled={saving} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Publish</button>
                    </div>
                  )}
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
                  {(['low', 'medium', 'high', 'critical'] as const).map(key => (
                    <NumberInput key={key} labelText={label(`${key} threshold`)} value={policy.thresholds[key]} disabled={!draft} max={100} step={0.01} onChange={value => updateThreshold(key, value)} />
                  ))}
                  <NumberInput labelText="Detection window (days)" value={policy.detection_window_days} disabled={!draft} min={1} onChange={value => setDraft(current => current ? { ...current, detection_window_days: value } : current)} />
                  <NumberInput labelText="Maximum snapshot age (days)" value={policy.snapshot_max_age_days} disabled={!draft} min={1} onChange={value => setDraft(current => current ? { ...current, snapshot_max_age_days: value } : current)} />
                </div>
              </section>

              <section>
                <h4 className="mb-2 text-sm font-semibold text-gray-700">Detector scoring</h4>
                <div className="space-y-3">
                  {orderedPatterns.map(pattern => {
                    const index = policy.patterns.findIndex(
                      item => item.pattern_type === pattern.pattern_type,
                    );
                    const formula = detectorFormula(pattern);
                    return (
                    <details key={pattern.pattern_type} className="rounded-lg border border-gray-200 p-4">
                      <summary className="cursor-pointer list-none">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <strong className="text-sm text-gray-800">{label(pattern.pattern_type)}</strong>
                          <span className={`rounded-full px-2 py-0.5 text-xs ${pattern.enabled_for_routing ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-600'}`}>{pattern.enabled_for_routing ? 'Used for routing' : 'Analytics only'}</span>
                        </div>
                        <div className="mt-2 overflow-x-auto rounded-md bg-indigo-50 px-3 py-2 text-xs text-indigo-900">
                          <span className="mr-2 font-semibold">Finding score</span>
                          <code className="whitespace-nowrap font-mono">{configuredScoreFormula(pattern, formula)}</code>
                        </div>
                      </summary>
                      <div className="mt-4 space-y-4">
                        <div className="rounded-md border border-indigo-100 bg-indigo-50/40 p-3 text-xs text-gray-700">
                          <div>
                            <span className="font-semibold text-gray-800">Detection condition: </span>
                            {formula.detectionCondition(pattern)}
                          </div>
                          <div className="mt-3 font-semibold text-gray-800">Normalized signals</div>
                          <div className="mt-1 divide-y divide-indigo-100">
                            {formula.signals.map(signal => (
                              <div key={signal.key} className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr_6rem] sm:items-center">
                                <span className="font-medium">{signal.name}</span>
                                <code className="overflow-x-auto whitespace-nowrap font-mono text-[11px] text-indigo-800">{signal.expression(pattern)}</code>
                                <span className="text-gray-500 sm:text-right">× {formatValue(parameter(pattern, `${signal.key}_weight`, 0))}</span>
                              </div>
                            ))}
                          </div>
                          <p className="mt-2 text-[11px] text-gray-500">
                            Each signal is limited to 0–1 before weighting. The final result is limited to the detector's configured minimum and maximum, which must remain within 0–100.
                          </p>
                        </div>
                        <div className="flex flex-wrap gap-4 text-sm">
                          <label className="flex items-center gap-2"><input type="checkbox" checked={pattern.enabled} disabled={!draft} onChange={event => updatePattern(index, { enabled: event.target.checked, enabled_for_routing: event.target.checked ? pattern.enabled_for_routing : false })} />Detection enabled</label>
                          <label className="flex items-center gap-2"><input type="checkbox" checked={pattern.enabled_for_routing} disabled={!draft || !pattern.enabled} onChange={event => updatePattern(index, { enabled_for_routing: event.target.checked })} />Use for nomination routing</label>
                          {(['nominator', 'beneficiary'] as const).map(role => (
                            <label key={role} className="flex items-center gap-2"><input type="checkbox" checked={pattern.applicable_roles.includes(role)} disabled={!draft} onChange={event => updatePattern(index, { applicable_roles: event.target.checked ? [...pattern.applicable_roles, role] : pattern.applicable_roles.filter(value => value !== role) })} />{label(role)}</label>
                          ))}
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                          <NumberInput labelText="Base score" value={pattern.base_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { base_score: value })} />
                          <NumberInput labelText="Minimum score" value={pattern.minimum_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { minimum_score: value })} />
                          <NumberInput labelText="Maximum score" value={pattern.maximum_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { maximum_score: value })} />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                          {Object.entries(pattern.parameters).map(([key, value]) => (
                            <NumberInput key={key} labelText={label(key)} value={value} disabled={!draft} step={key.includes('threshold') || key.includes('deviation') ? 0.01 : 1} onChange={next => updatePattern(index, { parameters: { ...pattern.parameters, [key]: next } })} />
                          ))}
                        </div>
                      </div>
                    </details>
                    );
                  })}
                </div>
              </section>

              <section className="rounded-lg border border-blue-100 bg-blue-50/40 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-semibold text-gray-700">Finding score calculator</h4>
                    <p className="mt-1 text-xs text-gray-500">
                      Enter raw detector evidence. The calculator applies the same normalization and weighting as the weekly Graph Analytics job.
                    </p>
                  </div>
                  <select
                    value={simPattern}
                    onChange={event => { setSimPattern(event.target.value); setSimInputs({}); }}
                    className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm"
                  >
                    {orderedPatterns.map(item => <option key={item.pattern_type} value={item.pattern_type}>{label(item.pattern_type)}</option>)}
                  </select>
                </div>

                {simulationPattern && simulationCalculator && (
                  <div className="mt-4 space-y-4">
                    <div className="rounded-md border border-blue-100 bg-white/70 p-3">
                      <p className="text-xs text-gray-600">
                        <strong>Scoring assumption:</strong> the detector's condition has already been met and a finding exists. This calculator determines that finding's numeric score.
                      </p>
                      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
                        {simulationCalculator.inputs.map(input => (
                          <label key={input.key} className="block text-xs text-gray-500">
                            {input.name}
                            <input
                              type="number"
                              value={resolvedSimulatorInputs[input.key] ?? ''}
                              min={input.minimum}
                              max={input.maximum}
                              step={input.step}
                              onChange={event => setSimInputs(current => ({
                                ...current,
                                [input.key]: Number(event.target.value),
                              }))}
                              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-2 text-sm text-gray-800"
                            />
                          </label>
                        ))}
                      </div>
                    </div>

                    <div className="overflow-x-auto rounded-md border border-blue-100 bg-white">
                      <table className="min-w-full text-left text-xs">
                        <thead className="bg-blue-50 text-gray-600">
                          <tr>
                            <th className="px-3 py-2 font-semibold">Signal</th>
                            <th className="px-3 py-2 font-semibold">Raw evidence</th>
                            <th className="px-3 py-2 text-right font-semibold">Normalized 0–1</th>
                            <th className="px-3 py-2 text-right font-semibold">Weight</th>
                            <th className="px-3 py-2 text-right font-semibold">Contribution</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-blue-50">
                          {simulatedContributions.map(signal => (
                            <tr key={signal.key}>
                              <td className="px-3 py-2 font-medium text-gray-700">{signal.name}</td>
                              <td className="px-3 py-2 text-gray-600">{signal.rawEvidence}</td>
                              <td className="px-3 py-2 text-right font-mono text-gray-700">{signal.normalized.toFixed(4)}</td>
                              <td className="px-3 py-2 text-right font-mono text-gray-700">{formatValue(signal.weight)}</td>
                              <td className="px-3 py-2 text-right font-mono font-semibold text-indigo-700">{signal.contribution.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="grid gap-3 rounded-md bg-indigo-950 p-4 text-white md:grid-cols-[1fr_auto] md:items-center">
                      <div>
                        <div className="text-xs text-indigo-200">Calculation</div>
                        <div className="mt-1 overflow-x-auto whitespace-nowrap font-mono text-xs">
                          {formatValue(simulationPattern.base_score)} base
                          {simulatedContributions.map(signal => ` + ${signal.contribution.toFixed(2)}`).join('')}
                          {' = '}{simulatedUnclampedScore.toFixed(2)}, limited to [{formatValue(simulationPattern.minimum_score)}, {formatValue(simulationPattern.maximum_score)}]
                        </div>
                      </div>
                      <div className="md:text-right">
                        <div className="text-xs text-indigo-200">Finding score</div>
                        <div className="text-3xl font-bold">{simulatedScore.toFixed(2)}</div>
                      </div>
                    </div>
                  </div>
                )}
              </section>

              {bundle?.can_request && <section className="rounded-lg border border-gray-200 p-4">
                <h4 className="text-sm font-semibold text-gray-700">Request fine-tuning</h4>
                <div className="mt-3 grid gap-3 lg:grid-cols-4">
                  <select value={requestPattern} onChange={event => setRequestPattern(event.target.value)} className="rounded-md border border-gray-300 px-3 py-2 text-sm"><option value="">Entire policy</option>{orderedPatterns.map(item => <option key={item.pattern_type} value={item.pattern_type}>{label(item.pattern_type)}</option>)}</select>
                  <input value={requestNominations} onChange={event => setRequestNominations(event.target.value)} placeholder="Nomination numbers (optional)" className="rounded-md border border-gray-300 px-3 py-2 text-sm" />
                  <textarea value={requestText} onChange={event => setRequestText(event.target.value)} placeholder="Describe the observed issue and desired outcome" className="min-h-20 rounded-md border border-gray-300 px-3 py-2 text-sm lg:col-span-2" />
                  <textarea value={requestProposal} onChange={event => setRequestProposal(event.target.value)} placeholder="Suggested parameter changes (optional)" className="min-h-16 rounded-md border border-gray-300 px-3 py-2 text-sm lg:col-span-4" />
                </div>
                <button onClick={() => void submitRequest()} disabled={saving || !requestText.trim()} className="mt-3 inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"><Send className="h-4 w-4" />Submit request</button>
              </section>}

              <section className="grid gap-4 lg:grid-cols-2">
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-gray-700">Fine-tuning requests</h4>
                  <div className="max-h-64 space-y-2 overflow-y-auto">
                    {(bundle?.requests || []).map(item => (
                      <div key={item.request_id} className="rounded-lg border border-gray-200 p-3 text-xs">
                        <div className="flex justify-between gap-2"><strong>#{item.request_id} · {item.pattern_type ? label(item.pattern_type) : 'Entire policy'}</strong><span>{label(item.status)}</span></div>
                        <p className="mt-1 text-gray-600">{item.request_text}</p>
                        {item.supporting_nomination_ids.length > 0 && <p className="mt-1 text-gray-500">Examples: {item.supporting_nomination_ids.map(value => `#${value}`).join(', ')}</p>}
                        {item.suggested_parameters?.proposal != null && <p className="mt-1 text-gray-500">Suggestion: {String(item.suggested_parameters.proposal)}</p>}
                        {item.admin_response && <p className="mt-1 rounded bg-gray-50 p-2 text-gray-600"><strong>Admin:</strong> {item.admin_response}</p>}
                        <p className="mt-1 text-gray-400">{item.requested_by} · {new Date(item.requested_at).toLocaleString()}</p>
                        {bundle?.can_edit && item.status !== 'PUBLISHED' && item.status !== 'REJECTED' && (
                          <div className="mt-2 space-y-2">
                            <input value={reviewResponses[item.request_id] || ''} onChange={event => setReviewResponses(current => ({ ...current, [item.request_id]: event.target.value }))} placeholder="Admin response (optional)" className="w-full rounded border border-gray-200 px-2 py-1.5 text-xs" />
                            <div className="flex gap-2">
                              {(item.status === 'REQUESTED'
                                ? ['UNDER_REVIEW', 'APPROVED', 'REJECTED']
                                : item.status === 'UNDER_REVIEW'
                                  ? ['APPROVED', 'REJECTED']
                                  : ['PUBLISHED', 'REJECTED']
                              ).map(status => <button key={status} onClick={() => void mutate(`/api/admin/setup/graph-policy/requests/${item.request_id}`, 'PATCH', { status, admin_response: reviewResponses[item.request_id] || null })} className="text-indigo-600 hover:underline">{label(status)}</button>)}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                    {(bundle?.requests || []).length === 0 && <p className="text-xs text-gray-400">No requests submitted.</p>}
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-gray-700">Version history</h4>
                  <div className="space-y-2">
                    {(bundle?.history || []).map(item => <div key={item.policy_id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-xs"><span>Version {item.policy_version}</span><span>{label(item.status)}{item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ''}</span></div>)}
                  </div>
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
