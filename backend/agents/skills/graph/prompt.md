# Graph Skill — Nomination Network and Integrity Findings

The nomination data is modelled as a **directed graph** in SQL Server graph
tables. Use the `graph_*` tools for any question involving relationships,
connections, paths, network structure, or fraud pattern findings.

## Graph tables

| Table                      | Type  | Columns                                                       |
|----------------------------|-------|---------------------------------------------------------------|
| `dbo.NomGraph_Person`      | NODE  | UserId, FullName, TenantId                                    |
| `dbo.NomGraph_Nominated`   | EDGE  | NominationId, Amount, Status, NomDate                         |
| `dbo.GraphPatternFindings` | Table | FindingId, PatternType, Severity, AffectedUsers (JSON array), NominationIds (JSON array), TotalAmount, Detail, DetectedAt, RunId |

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
| SuperNominator        | User whose nomination count is a statistical outlier (mean + 2σ)    |
| Desert                | Entire team under one manager with zero nomination activity          |
| ApproverAffinity      | Nominator/approver pair with approval rate ≥ 2× tenant baseline     |
| CopyPaste             | Cluster of near-identical nomination descriptions (cosine ≥ 0.92)   |
| TransactionalLanguage | Description contains quid-pro-quo or personal-benefit phrasing      |
| HiddenCandidate       | Name appears frequently in descriptions but never as a BeneficiaryId |

## Severity scale (financial exposure)

Severity is based on the total Approved/Paid nomination amount involved:

| Severity | TotalAmount    |
|----------|----------------|
| Critical | ≥ $10,000      |
| High     | ≥ $5,000       |
| Medium   | ≥ $1,000       |
| Low      | < $1,000       |
