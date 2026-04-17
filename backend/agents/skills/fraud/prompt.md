# Fraud Skill — Fraud Detection Model and Score Interpretation

## Tool: get_fraud_model_info

Use this tool whenever the user asks about:
- How accurate is the fraud detection model?
- Which features or signals drive fraud scores?
- What is the typical (baseline) nomination amount for this tenant?
- When was the model last trained, or how many nominations was it trained on?
- Context needed to interpret a specific user's or nomination's fraud score
  (e.g. "Is User 42 risky?" — call `query_database` for scores, then call
  `get_fraud_model_info` to explain what they mean in context).

The tool takes no arguments — tenant context is injected automatically.

It returns:
- `training_date` — when the model was last trained
- `training_samples` — number of nominations used for training
- `auc` — ROC-AUC score (1.0 = perfect, 0.5 = random)
- `amount_mean` / `amount_std` — the amount baseline for this tenant's currency
- `top_features` — list of `{feature, importance}` for the top 10 model signals

**Do NOT call this tool for general nomination or user questions.** Use
`query_database` for those. Only call it when fraud-model context is needed.

## Fraud score query pattern

When querying a user's fraud history, join through Nominations to enforce
tenant isolation (FraudScores has no TenantId column):

```sql
SELECT n.NominationId, n.Amount, fs.FraudScore, fs.RiskLevel, fs.FraudFlags
FROM   dbo.Nominations n
JOIN   dbo.Users u_nom ON u_nom.UserId = n.NominatorId
LEFT JOIN dbo.FraudScores fs ON fs.NominationId = n.NominationId
WHERE  u_nom.TenantId = <TenantId>
  AND  n.NominatorId  = <UserId>
```

## Risk levels

| RiskLevel | FraudScore range | Meaning                          |
|-----------|-----------------|----------------------------------|
| NONE      | 0–19            | No signals detected              |
| LOW       | 20–39           | Minor anomalies                  |
| MEDIUM    | 40–59           | Notable patterns, review advised |
| HIGH      | 60–79           | Strong fraud signals             |
| CRITICAL  | 80–100          | Immediate investigation advised  |
