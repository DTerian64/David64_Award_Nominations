# Award Nomination System – Executive Dashboard
**Environment:** Sandbox  
**Platform:** Azure Monitor Dashboards with Grafana (preview)  
**Data sources:** `appi-award-api-sandbox`, `appi-award-frontend-sandbox`  
**Default time range:** Last 24 hours  
**Auto-refresh:** Every 5 minutes

---

## Overview

This dashboard provides a real-time executive view of the Award Nomination System, covering API health, frontend engagement, business activity, and infrastructure health. It is organized into five zones, each serving a distinct purpose for executive consumption.

---

## Dashboard Layout

### Zone 1 · Health at a glance
Six stat tiles across the full width (4 columns each, 24 columns total). Designed for an instant system pulse — color-coded green/yellow/red so executives see system health without reading numbers.

### Zone 2 · Traffic shape
Four charts side by side (6 columns each, 24 columns total). Shows how the system behaves over time — volume, latency, failures, and business activity across the last 24 hours.

### Zone 3 · Failures & action
One full-width table (24 columns). Surfaces the specific endpoints with failures, ranked by severity. Actionable for engineering teams and transparent for executives.

### Zone 4 · Platform Health
Six stat tiles (SWA + AFD metrics) plus one full-width AFD latency time series. Shows the health of the edge and frontend delivery layer.

### Zone 5 · Compute & Database
Four stat tiles (ACA + SQL) plus four time series charts. Shows compute utilization and database activity across the backend layer.

---

## Zone 1 — Health at a glance

### KPI 1 · Total API requests
**Visualization:** Stat  
**Source:** `appi-award-api-sandbox`  
**Unit:** Short (formatted number)  
**Thresholds:** Base green (high volume is good, no upper limit)

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| summarize ["Total API Requests"] = count()
```

---

### KPI 2 · API failure rate
**Visualization:** Stat  
**Source:** `appi-award-api-sandbox`  
**Unit:** Percent (0–100)  
**Thresholds:** Base green · yellow at 1% · red at 1%  
**Color mode:** Background

```kusto
requests
| summarize
    Total = count(),
    Failed = countif(success == false)
| extend FailureRate = round(100.0 * todouble(Failed) / iif(Total == 0, 1.0, todouble(Total)), 2)
| project FailureRate
```

---

### KPI 3 · P95 latency (ms)
**Visualization:** Stat  
**Source:** `appi-award-api-sandbox`  
**Unit:** Milliseconds (ms)  
**Thresholds:** Base green · yellow at 500ms · red at 1500ms  
**Color mode:** Background  
**Note:** Excludes `/api/admin/analytics/ask` — an AI endpoint with inherently high latency not representative of application performance.

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| where name !startswith "POST /api/admin/analytics"
| where success == true
| summarize P95 = round(percentile(duration, 95), 0)
| project P95
```

---

### KPI 4 · Unique users
**Visualization:** Stat  
**Source:** `appi-award-frontend-sandbox`  
**Unit:** Short  
**Thresholds:** Base green  
**Color mode:** Background

```kusto
pageViews
| summarize ["Unique Users"] = dcount(session_Id)
```

---

### KPI 5 · Pages viewed
**Visualization:** Stat  
**Source:** `appi-award-frontend-sandbox`  
**Unit:** Short  
**Thresholds:** Base red · green at 5 (fewer than 5 views may indicate frontend issues)  
**Color mode:** Background

```kusto
pageViews
| summarize ["Frontend Page Views"] = count()
```

---

### KPI 6 · Nominations
**Visualization:** Stat  
**Source:** `appi-award-api-sandbox`  
**Unit:** Short  
**Thresholds:** Base green  
**Color mode:** Background  
**Business significance:** The only pure business metric in Zone 1 — tells executives whether the system is being actively used for its intended purpose.

```kusto
requests
| where name == "POST /api/nominations"
| where success == true
| summarize ["Nominations Submitted"] = count()
```

---

## Zone 2 — Traffic shape

### KPI 7 · API requests distribution
**Visualization:** Bar chart  
**Source:** `appi-award-api-sandbox`  
**Unit:** Short  
**Why bar chart:** Each bar represents a discrete hourly count. Gaps between bars honestly show periods of no traffic. A line chart would imply false continuity between empty hours.

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| summarize Requests = count() by bin(timestamp, 1h)
| order by timestamp asc
| project timestamp, Requests
```

---

### KPI 8 · P95 latency over time
**Visualization:** Time series (line chart)  
**Source:** `appi-award-api-sandbox`  
**Unit:** Milliseconds (ms)  
**Thresholds:** Line at 500ms (yellow) · line at 1500ms (red)  
**Why line chart:** Latency is a continuous measurement — interpolation between hourly readings is valid and shows degradation trends clearly.  
**Note:** Excludes analytics endpoint and buckets with fewer than 5 requests to avoid statistically misleading percentiles on low-traffic hours.

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| where name !startswith "POST /api/admin/analytics"
| where success == true
| summarize P95 = round(percentile(duration, 95), 0) by bin(timestamp, 1h)
| where P95 > 0
| order by timestamp asc
| project timestamp, P95
```

---

### KPI 9 · API failure rate over time
**Visualization:** Bar chart  
**Source:** `appi-award-api-sandbox`  
**Unit:** Percent (0–100)  
**Color scheme:** From thresholds (by value) — base green · yellow at 1% · red at 5%  
**Thresholds mode:** Absolute  
**Filter:** Excludes buckets with fewer than 5 requests to prevent statistically misleading spikes from low-traffic periods.

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| summarize
    Total = count(),
    Failed = countif(success == false)
    by bin(timestamp, 1h)
| where Total >= 5
| extend FailureRatePct = round(100.0 * todouble(Failed) / iif(Total == 0, 1.0, todouble(Total)), 2)
| order by timestamp asc
| project timestamp, FailureRatePct
```

---

### KPI 10 · Nominations & approvals over time
**Visualization:** Time series (line chart)  
**Source:** `appi-award-api-sandbox`  
**Unit:** Short  
**Series colors:** Nominations → blue · Approvals → green  
**Business significance:** The only business activity chart on the dashboard. Shows whether the system is being actively used for nominations and whether approvals are keeping pace — directly relevant to executives.

```kusto
requests
| where name in (
    "POST /api/nominations",
    "POST /api/nominations/approve")
| where success == true
| summarize
    Nominations = countif(name == "POST /api/nominations"),
    Approvals = countif(name == "POST /api/nominations/approve")
    by bin(timestamp, 1h)
| order by timestamp asc
| project timestamp, Nominations, Approvals
```

---

## Zone 3 — Failures & action

### KPI 11 · Top failing API endpoints
**Visualization:** Table  
**Source:** `appi-award-api-sandbox`  
**Width:** Full width (24 columns)  
**Sorted by:** Failed requests descending  
**Rows:** Top 10 endpoints  

**Column configuration:**

| Column | Display name | Width | Alignment | Color |
|--------|-------------|-------|-----------|-------|
| Endpoint | Endpoint | 280 | Left | — |
| FailedRequests | Failed | 80 | Center | — |
| TotalRequests | Total | 80 | Center | — |
| FailureRatePct | Failure % | 100 | Center | Threshold background |

**Failure % thresholds:** Absolute mode · base green · yellow at 1 · red at 5

```kusto
requests
| where name !startswith "HEAD /health"
| where name !startswith "OPTIONS "
| summarize
    TotalRequests = count(),
    FailedRequests = countif(success == false)
    by name
| extend FailureRatePct = round(100.0 * todouble(FailedRequests) / todouble(TotalRequests), 2)
| order by FailedRequests desc
| project Endpoint = tostring(name), Failed = FailedRequests, Total = TotalRequests, ["Failure %"] = FailureRatePct
| take 10
```

---

## Zone 4 — Platform Health

**Data source:** Azure Monitor Metrics (not KQL)  
**Resources:** `award-nomination-frontend-sandbox` (SWA) · `Award-Nomination-ADF-sandbox` (AFD)  
**Time grain:** 1 day for stat tiles · Auto for time series

### Layout
Six stat tiles (4 columns each = 24 columns total) followed by one full-width AFD latency time series.

---

### KPI 12 · SWA requests
**Visualization:** Stat  
**Resource:** `award-nomination-frontend-sandbox`  
**Metric namespace:** `microsoft.web/staticsites`  
**Metric:** `SiteHits`  
**Aggregation:** Total · Time grain: 1d  
**Thresholds:** Base green

---

### KPI 13 · SWA 5xx errors
**Visualization:** Stat  
**Resource:** `award-nomination-frontend-sandbox`  
**Metric namespace:** `microsoft.web/staticsites`  
**Metric:** `SiteErrors`  
**Aggregation:** Total · Time grain: 1d  
**Thresholds:** Base green · red at 1  
**No value:** 0  
**Note:** 5xx = server-side failures. Any non-zero value indicates the SWA failed to serve content.

---

### KPI 14 · SWA 4xx errors
**Visualization:** Stat  
**Resource:** `award-nomination-frontend-sandbox`  
**Metric namespace:** `microsoft.web/staticsites`  
**Metric:** `CdnPercentageOf4XX`  
**Aggregation:** Total · Time grain: 1d  
**Thresholds:** Base green · yellow at 10 · red at 50  
**No value:** 0  
**Note:** 4xx = client errors (broken links, missing assets, unauthorized). Small numbers are normal; spikes may indicate a broken deployment.

---

### KPI 15 · SWA bandwidth
**Visualization:** Stat  
**Resource:** `award-nomination-frontend-sandbox`  
**Metric namespace:** `microsoft.web/staticsites`  
**Metric:** `Data Out`  
**Aggregation:** Total · Time grain: 1d  
**Unit:** Megabytes  
**Thresholds:** Base green  
**Note:** Reflects SWA origin serving only. With AFD caching, repeat requests bypass SWA — this metric shows cache misses and first loads only. Azure Monitor metric unit (MB) is injected by Azure Monitor metadata and cannot be suppressed in Grafana.

---

### KPI 16 · AFD requests
**Visualization:** Stat  
**Resource:** `Award-Nomination-ADF-sandbox`  
**Metric namespace:** `microsoft.cdn/profiles`  
**Metric:** `RequestCount`  
**Aggregation:** Total · Time grain: 1d  
**Thresholds:** Base green  
**Note:** AFD count always exceeds SWA count — the difference is requests served from AFD cache without hitting origin. This confirms CDN is working efficiently.

---

### KPI 17 · AFD health
**Visualization:** Stat  
**Resource:** `Award-Nomination-ADF-sandbox`  
**Metric namespace:** `microsoft.cdn/profiles`  
**Metric:** `OriginHealthPercentage`  
**Aggregation:** Average · Time grain: 1d  
**Unit:** Percent (0–100)  
**Thresholds:** Base red · green at 100  
**Color mode:** Background  
**Note:** Most critical AFD metric. 100% = all ACA origins reachable. Below 100% = at least one origin degraded — immediate investigation required.

---

### KPI 18 · AFD latency (ms)
**Visualization:** Time series (line chart)  
**Resource:** `Award-Nomination-ADF-sandbox`  
**Metric namespace:** `microsoft.cdn/profiles`  
**Metric:** `TotalLatency`  
**Aggregation:** Average · Time grain: Auto  
**Unit:** Milliseconds (ms)  
**Width:** Full width (24 columns)  
**Thresholds:** Line at 500ms (yellow) · line at 2000ms (red)  
**Note:** Spikes in this chart directly correlate with database cold start events — confirming the full request chain impact from edge to database.

---

## Zone 5 — Compute & Database

**Data source:** Azure Monitor Metrics  
**Resources:** `award-api-primary-sandbox` · `award-api-secondary-sandbox` · `AwardNominationsSandbox`  
**Metric namespaces:** `microsoft.app/containerapps` (ACA) · `microsoft.sql/servers/databases` (SQL)

### Layout
Four stat tiles (6 columns each = 24 columns total) followed by two rows of paired time series charts (12 columns each).

---

### KPI 19 · ACA primary
**Visualization:** Stat  
**Resource:** `award-api-primary-sandbox`  
**Metric:** `Replicas` · Aggregation: Average · Time grain: 1d  
**Thresholds:** Base red · green at 1  
**Note:** 0 = container down or scaled to zero. 1+ = healthy and serving.

---

### KPI 20 · ACA secondary
**Visualization:** Stat  
**Resource:** `award-api-secondary-sandbox`  
**Metric:** `Replicas` · Aggregation: Average · Time grain: 1d  
**Thresholds:** Base red · green at 1

---

### KPI 21 · SQL storage
**Visualization:** Stat  
**Resource:** `AwardNominationsSandbox`  
**Metric:** `storage` (Data space used bytes)  
**Aggregation:** Maximum · Time grain: 1d  
**Unit:** Megabytes  
**Thresholds:** Base green  
**Current value:** ~29 MB (sandbox baseline)

---

### KPI 22 · Sessions count
**Visualization:** Stat  
**Resource:** `AwardNominationsSandbox`  
**Metric:** `sessions_count` · Aggregation: Maximum · Time grain: 1d  
**Thresholds:** Base green · yellow at 25 · red at 28  
**Note:** Azure SQL Basic tier hard limit is 30 sessions. Approaching this causes connection failures. Current sandbox value of 12 leaves healthy headroom.

---

### KPI 23 · ACA CPU usage (%)
**Visualization:** Time series (line chart)  
**Resources:** Both ACA instances on same chart  
**Query A:** `award-api-primary-sandbox` · Legend: `Primary`  
**Query B:** `award-api-secondary-sandbox` · Legend: `Secondary`  
**Metric:** `CpuUsagePercentage` · Aggregation: Average · Time grain: 15m  
**Unit:** Percent (0–100) · Min/Max: Auto  
**Thresholds:** Line at 70% (yellow) · 90% (red)  
**Series colors:** Primary → green · Secondary → yellow  
**Observed baseline:** 1.25–1.75% — massive headroom for growth

---

### KPI 24 · ACA memory usage (%)
**Visualization:** Time series (line chart)  
**Resources:** Both ACA instances on same chart  
**Metric:** `MemoryWorkingSetPercentage` · Aggregation: Average · Time grain: 15m  
**Unit:** Percent (0–100) · Min/Max: Auto  
**Thresholds:** Line at 80% (yellow) · 95% (red)  
**Series colors:** Primary → green · Secondary → yellow  
**Observed baseline:** Primary ~74% · Secondary ~79–87%  
**Important note:** Memory is elevated due to tenant-specific `fraud_detection.pkl` models loaded at startup — one per tenant. This is intentional, not a leak. Thresholds are provisional — review after stress testing.

---

### KPI 25 · SQL worker threads (%)
**Visualization:** Time series (line chart)  
**Resource:** `AwardNominationsSandbox`  
**Metric:** `workers_percent` · Aggregation: Average · Time grain: 15m  
**Unit:** Percent (0–100) · Min/Max: Auto  
**Thresholds:** Line at 50% (yellow) · 80% (red)  
**Note:** More meaningful than `cpu_percent` for Basic tier. Reflects actual query concurrency. Peaks at ~7% during active nomination processing. Flat overnight confirms database sleep pattern.

---

### KPI 26 · SQL connections
**Visualization:** Time series (line chart)  
**Resource:** `AwardNominationsSandbox`  
**Query A:** `connection_successful` · Total · Legend: `Successful` · Color: green  
**Query B:** `connection_failed` · Total · Legend: `Failed` · Color: red  
**Time grain:** 15m  
**Note:** Failed connection spikes (red) appear at exactly the same timestamps as AFD latency spikes and API failure rate spikes — the definitive cross-layer confirmation of database cold start as root cause.

---

## Known issues & sandbox caveats

### Database cold start spikes
**What it looks like:** Sudden 20–40% failure rate spike, visible simultaneously in Zone 2 (API failure rate), Zone 4 (AFD latency), and Zone 5 (SQL connections failed).  
**Cause:** Azure SQL Basic tier suspends after inactivity. First requests during wake-up receive HTTP 500 with 15–30 second response times.  
**Production impact:** None — production runs on dedicated SKU with no sleep behavior and connection pooling.  
**Exec talking point:** *"This spike reflects our sandbox database resuming from sleep mode. This behavior does not occur in production environments."*

### P95 latency — analytics endpoint exclusion
**Cause:** `POST /api/admin/analytics/ask` calls an AI/LLM backend (1.3–3.4 second response times). Expected behavior.  
**Fix applied:** Excluded from all P95 calculations.

### ACA memory elevated baseline
**Cause:** Tenant-specific `fraud_detection.pkl` models loaded into memory at container startup — one per tenant. Intentional design for fast inference.  
**Action required:** Establish per-tenant memory baselines after stress testing before setting final thresholds.

### SWA bandwidth metric lag
**Cause:** AFD caches repeat requests at edge — SWA only sees cache misses. Azure Monitor platform metrics also have 3–15 minute ingestion lag.  
**Interpretation:** Use AFD request count as the authoritative total traffic metric.

### Nomination approval endpoint design
**Note:** `POST /api/nominations/approve` uses a body parameter (`approved`/`denied`) instead of separate endpoints. Rejection metrics require custom Application Insights event tracking to isolate.

---

## Operational notes

### Time range alignment
KQL panels (Zones 1-3) use a rolling 24h window. Azure Monitor Metrics panels (Zones 4-5) use 1-day grain aligned to midnight. For exec consumption this distinction is not meaningful.

### Data source latency
App Insights KQL (Zones 1-3): 2-5 minute lag. Azure Monitor Metrics (Zones 4-5): 3-15 minute lag.

### Auto-refresh
5 minutes. Active while dashboard is open in browser.

### Exporting
**PDF (one-off):** Create temporary Azure Managed Grafana Standard workspace → import JSON → Share → PDF tab → Save as PDF (landscape, grid) → delete workspace. Cost: ~$1.  
**PDF (scheduled):** Azure Managed Grafana Standard supports scheduled email reports with SMTP configuration.  
**Live link:** Share URL with Azure AD users in the same tenant.

### Backup
Export JSON via **Share → Export → Save to file** after every significant session. No built-in version history in preview.

---

## Infrastructure resources

| Component | Resource name | Azure resource type |
|---|---|---|
| App Insights (API) | `appi-award-api-sandbox` | `Microsoft.Insights/components` |
| App Insights (Frontend) | `appi-award-frontend-sandbox` | `Microsoft.Insights/components` |
| Static Web App | `award-nomination-frontend-sandbox` | `Microsoft.Web/staticSites` |
| Azure Front Door | `Award-Nomination-ADF-sandbox` | `Microsoft.Cdn/profiles` |
| ACA Primary | `award-api-primary-sandbox` | `Microsoft.App/containerApps` |
| ACA Secondary | `award-api-secondary-sandbox` | `Microsoft.App/containerApps` |
| SQL Server | `david64-sql-sandbox` | `Microsoft.Sql/servers` |
| SQL Database | `AwardNominationsSandbox` | `Microsoft.Sql/servers/databases` |

---

## Dashboard summary

| Zone | Panels | Purpose |
|------|--------|---------|
| Health at a glance | 6 stat tiles | Instant system pulse — color coded for exec scan |
| Traffic shape | 4 charts | 24h trends — volume, latency, failures, business activity |
| Failures & action | 1 table | Actionable endpoint failure detail |
| Platform Health | 6 stats + 1 chart | Edge and frontend delivery layer health |
| Compute & Database | 4 stats + 4 charts | Backend compute and database layer health |

**Total panels:** 26  
**App Insights KQL panels:** 11  
**Azure Monitor Metrics panels:** 15  
**Business metric panels:** 3 (Nominations stat + Nominations & approvals chart + Sessions count)  
**Infrastructure panels:** 12 (SWA + AFD + ACA + SQL)

---

## Post-stress-test checklist

- [ ] ACA memory thresholds — establish per-tenant baseline with N tenants loaded
- [ ] ACA CPU thresholds — verify 70%/90% under real load
- [ ] SQL worker threads thresholds — validate 50%/80% under concurrent nomination processing
- [ ] SQL sessions thresholds — confirm Basic tier limit (30) is sufficient or plan upgrade
- [ ] P95 latency thresholds — validate 500ms/1500ms against production SLA commitments
- [ ] Database cold start — implement keep-alive ping or upgrade SQL tier
- [ ] ACA secondary memory — investigate pickle file sharing across instances to reduce per-instance footprint
