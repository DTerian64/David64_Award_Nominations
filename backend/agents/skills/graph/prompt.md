# Graph Skill — Nomination Network and Integrity Findings

The nomination data is modelled as a **directed graph** in SQL Server graph
tables. Use the `graph_*` tools for any question involving relationships,
connections, paths, network structure, or fraud pattern findings.

## Graph tables

| Table                      | Type  | Columns                                                       |
|----------------------------|-------|---------------------------------------------------------------|
| `dbo.NomGraph_Person`      | NODE  | UserId, FullName, TenantId                                    |
| `dbo.NomGraph_Nominated`   | EDGE  | NominationId, Amount, Status, NomDate                         |
| `dbo.GraphPatternFindings` | Table | FindingId, PatternType, Severity, FindingScore, ScoringPolicyVersion, ScoreComponentsJson, AffectedUsers (JSON array), NominationIds (JSON array), TotalAmount, Detail, DetectedAt, RunId |

An edge `p1 → p2` means *p1 nominated p2*. Only Approved/Paid nominations
are loaded into the graph — edges represent committed financial exposure.

## When to use graph tools vs query_database

| Question                                      | Tool                                             |
|-----------------------------------------------|--------------------------------------------------|
| Who has User X nominated?                     | `graph_get_nominations_sent`                     |
| Who nominated User X?                         | `graph_get_nominations_received`                 |
| Are Users X and Y connected?                  | `graph_find_path`                                |
| Who are the most active nominators?           | `graph_get_degree_leaders(direction="out")`      |
| Who receives the most nominations?            | `graph_get_degree_leaders(direction="in")`       |
| Show X's extended nomination network          | `graph_get_network(depth=2)`                     |
| What fraud patterns have been detected?       | `graph_get_integrity_findings`                   |
| Look up finding #12345                        | `graph_get_integrity_findings(finding_id=12345)` |
| Is User X involved in any findings?           | `graph_get_integrity_findings(user_id=X)`        |
| Count / aggregate / date-filter nominations   | `query_database`                                 |

## Critical routing rule — fraud findings vs nomination edges

Questions that mention fraud, integrity findings, copy-paste, rings, or any
PatternType **always** start with `graph_get_integrity_findings`, regardless
of phrasing. The words "issued by", "sent by", "from", or "by User X" do
**not** mean use `graph_get_nominations_sent` when the topic is fraud findings.

| Misleading phrasing                                          | Correct tool                                         |
|--------------------------------------------------------------|------------------------------------------------------|
| "copy-paste nominations issued by User X"                    | `graph_get_integrity_findings(user_id=X, pattern_type="CopyPaste")` |
| "fraud findings sent by / involving / attributed to User X" | `graph_get_integrity_findings(user_id=X)`            |
| "what rings has User X been part of?"                        | `graph_get_integrity_findings(user_id=X, pattern_type="Ring")` |

`AffectedUsers` in a current finding lists **all implicated nominators and
beneficiaries** — not just the person who sent the nominations. Historical
`ApproverAffinity` audit rows can still contain approver IDs, but that detector
is no longer run or used for nomination routing.
A user can appear in a CopyPaste finding as a nominator whose descriptions
matched others, OR as a nominee who received near-identical descriptions.
`graph_get_integrity_findings(user_id=X)` captures both roles correctly.

Only use `graph_get_nominations_sent` / `graph_get_nominations_received` when
the question is purely about the **edge structure** of the graph (who nominated
whom), not about whether those nominations were flagged as fraudulent.

## Workflow for name-based questions

1. Call `graph_search_user(name_fragment)` to resolve the name to a UserId.
2. Call the appropriate graph tool with the resolved UserId.
3. **Never guess a UserId** — always resolve via `graph_search_user` first.

## Anonymity in findings

`AffectedUsers` and `NominationIds` in GraphPatternFindings are JSON arrays
of integer IDs. When discussing findings, refer to users by **UserId number
only** — never speculate about their real identity. Investigators with a
FindingId can look up full details directly in the database.

## Pattern type reference

| PatternType           | Description                                                          |
|-----------------------|----------------------------------------------------------------------|
| Ring                  | Directed cycle: A → B → C → A (mutual nomination loop)              |
| BipartiteDenseBlock   | Dense many-to-few or few-to-many nomination campaign                 |
| TemporalBurst         | Abnormal nomination volume compressed into a short rolling window    |
| SuperNominator        | User whose nomination count is a statistical outlier (mean + 2σ)    |
| SuperBeneficiary      | Beneficiary with outlier volume and broad nominator support          |
| Desert                | Entire team under one manager with zero nomination activity          |
| ApproverAffinity      | Legacy historical finding retained for audit; no longer produced    |
| CopyPaste             | Cluster of near-identical nomination descriptions (cosine ≥ 0.92)   |
| HiddenCandidate       | Name appears frequently in descriptions but never as a BeneficiaryId |

## Finding scores and derived severity

Each detector produces a continuous `FindingScore` from its versioned base,
signal weights, and limits. `ScoreComponentsJson` records the inputs used to
calculate that score. The active tenant policy converts the numeric score into
Low, Medium, High, or Critical using its configured thresholds; the bands are
not fixed dollar amounts.

For nomination routing, the graph score is the highest relevant routing-enabled
finding score involving the nomination's nominator or beneficiary. Findings
configured as analytics-only remain visible evidence but do not raise the
nomination's routing severity. Always cite `ScoringPolicyVersion` when explaining
how a score was produced.
