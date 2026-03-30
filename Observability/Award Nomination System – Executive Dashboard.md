# Award Nomination System – Executive Dashboard
**Environment:** Sandbox  
**Platform:** Azure Monitor Dashboards with Grafana (preview)  
**Data sources:** `appi-award-api-sandbox`, `appi-award-frontend-sandbox`  
**Default time range:** Last 24 hours  
**Auto-refresh:** Every 5 minutes

---

## Overview

This dashboard provides a real-time executive view of the Award Nomination System, covering API health, frontend engagement, and business activity. It is organized into three zones, each serving a distinct purpose for executive consumption.

---

## Dashboard Layout

### Zone 1 · Health at a glance
Six stat tiles across the full width (4 columns each, 24 columns total). Designed for an instant system pulse — color-coded green/yellow/red so executives see system health without reading numbers.

### Zone 2 · Traffic shape
Four charts side by side (6 columns each, 24 columns total). Shows how the system behaves over time — volume, latency, failures, and business activity across the last 24 hours.

### Zone 3 · Failures & action
One full-width table (24 columns). Surfaces the specific endpoints with failures, ranked by severity. Actionable for engineering teams and transparent for executives.

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

## Known issues & sandbox caveats

### Database cold start spikes
**What it looks like:** A sudden 20–40% failure rate spike lasting one or two hourly buckets, affecting `POST /api/nominations`.  
**Cause:** Azure SQL Basic tier aggressively suspends after inactivity. The first requests during wake-up receive connection timeout errors (HTTP 500, 15–30 second performance bucket) before the database resumes.  
**Production impact:** None — production will run on a dedicated SKU with no sleep behavior and connection pooling.  
**Exec talking point:** *"This spike reflects our sandbox database resuming from sleep mode. This behavior does not occur in production environments."*

### P95 latency — analytics endpoint exclusion
**What it looks like:** P95 latency tile shows high values (>2s) without exclusion.  
**Cause:** `POST /api/admin/analytics/ask` calls an AI/LLM backend with inherently high response times (1.3–3.4 seconds). This is expected behavior for that endpoint.  
**Fix applied:** This endpoint is excluded from all P95 calculations. Its latency is monitored separately if needed.

### Nomination approval endpoint design
**Note:** The system uses a single `POST /api/nominations/approve` endpoint with a request body parameter (`approved` or `denied`) rather than separate approve/reject endpoints. Rejection metrics therefore cannot be isolated at the endpoint level without custom event tracking in Application Insights.

---

## Operational notes

### Time range
All panels use the dashboard global time range (Last 24 hours by default). The global time picker in the top toolbar controls all panels simultaneously. Executives should not need to adjust this.

### Auto-refresh
Set to 5 minutes. Panels re-query App Insights automatically while the dashboard is open in a browser tab.

### Exporting
**PDF:** Use browser print (`Ctrl+P` / `Cmd+P`) in full screen mode, save as PDF, landscape orientation, 60–70% scale.  
**Screenshot:** Use browser full-page capture extension for a single-image export suitable for email.  
**Live link:** Share the dashboard URL with users who have Azure AD access to the same tenant — they will see live data without needing a static export.

### Backup
Export the dashboard JSON periodically via **Share → Export → Save to file**. This JSON can be re-imported to restore the full dashboard if panels are accidentally deleted. The preview version has no built-in version history.

---

## Dashboard summary

| Zone | Panels | Purpose |
|------|--------|---------|
| Health at a glance | 6 stat tiles | Instant system pulse — color coded for exec scan |
| Traffic shape | 4 charts | 24h trends — volume, latency, failures, business activity |
| Failures & action | 1 table | Actionable endpoint failure detail |

**Total panels:** 11  
**API insight panels:** 9  
**Frontend insight panels:** 2  
**Business metric panels:** 2 (Nominations stat + Nominations & approvals chart)