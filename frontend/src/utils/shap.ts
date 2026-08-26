export interface ShapContribution {
  feature: string;
  raw_value: number;
  contribution: number;
}

export const SHAP_FEATURE_LABELS: Record<string, string> = {
  PairNominationCount:          'Same nominator → beneficiary pair count',
  AmountZScore:                 'Amount deviation from tenant average (σ)',
  NominatorConcentrationRatio:  'Nominator concentration ratio',
  HasReciprocalNomination:      'Reciprocal nomination exists',
  NominatorTotalNominations:    'Nominator total nominations',
  IsHighAmount:                 'Amount statistically high',
  BeneficiaryTotalReceived:     'Beneficiary total awards received',
  BeneficiaryAvgAmountReceived: 'Beneficiary avg award amount',
  DescriptionCosineSim:         'Description similarity to prior nominations',
  DescriptionEmbDistance:       'Description semantic distance',
  CategoryFraudRate:            'Category historical fraud rate',
  NominatorAvgAmount:           'Nominator avg award amount',
  NominatorStdAmount:           'Nominator amount variability',
  NominatorUniqueBeneficiaries: 'Nominator unique beneficiaries',
};

export function parseShapContributions(value: unknown): ShapContribution[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is ShapContribution => {
    if (item === null || typeof item !== 'object') return false;
    const candidate = item as Record<string, unknown>;
    return typeof candidate.feature === 'string'
      && typeof candidate.raw_value === 'number'
      && typeof candidate.contribution === 'number';
  });
}
