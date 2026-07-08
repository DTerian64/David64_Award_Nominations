# Observability and Operations Guide

## Observability Summary

The platform uses structured logging, OpenTelemetry, Application Insights, Log Analytics, KQL assets, dashboards, and health endpoints to support operations.

Core goals:

- Trace a nomination across API, Service Bus, workers, email, payroll, and logs.
- Detect failed deployments quickly.
- Monitor queue health and dead-letter behavior.
- Track fraud and integrity workflows.
- Identify performance and reliability regressions.
- Support executive operational dashboards.

## Logging

Services configure structured logging through `logging_config.py`.

Patterns:

- JSON-style application logs.
- `App_Log` prefix for KQL filtering.
- Message ID injected into worker logs.
- Service Bus event type and nomination ID logged on publish/consume.
- OpenTelemetry trace context restored from Service Bus application properties.

Recommended standard log fields:

- `service`
- `environment`
- `tenant_id`
- `user_id`
- `nomination_id`
- `event_type`
- `message_id`
- `correlation_id` or trace ID
- `status`
- `error`

## Metrics

Important metrics:

- Backend request count, latency, error rate.
- Frontend page load and API call failures.
- Service Bus active messages, dead-letter count, delivery count.
- Integrity worker processing count, failures, processing duration.
- Auxiliary worker email success/failure.
- Payroll broker accepted/failed submissions.
- Fraud analytics job execution status and duration.
- HRBP review queue size and age.
- Fraud score distribution and critical fraud count.
- Azure SQL DTU/vCore, connection errors, CPU, storage.
- Azure OpenAI failures and latency.

## Tracing

Trace propagation flow:

1. Frontend sends W3C headers where available.
2. Backend FastAPI instrumentation emits spans.
3. Service Bus publisher writes trace headers into message application properties.
4. Workers extract trace headers and attach parent context.
5. Worker logs and spans join the originating operation.

Operational benefit: a single nomination can be followed from HTTP request through async processing.

## Dashboards and KQL Assets

Relevant repository paths:

- `Observability/award-nomination-dashboard.md`
- `Observability/Award Nomination System - Executive Dashboard.md`
- `Observability/*.pdf`
- `analytics/kusto-queries`
- `.github/workflows/kusto_*`

KQL categories:

- Alerts.
- Email.
- Fraud.
- Monitoring.
- Errors.
- Logs.

## Health Checks

Backend:

- `GET /health`
- `HEAD /health`
- `GET /`

Front Door probes:

- HEAD `/health` every 30 seconds.

Payroll broker:

- `/health` through health router.
- `/` root returns service healthy.

Recommended worker health:

- Monitor Service Bus subscription drain rate.
- Monitor Container App replica count and restarts.
- Alert on dead-letter growth.

## Operational Runbooks

### API Failure Rate Is High

Check:

- Front Door origin health.
- Container App revisions and restarts.
- Application Insights exceptions.
- SQL connectivity errors.
- Recent deployment workflow.
- Key Vault secret resolution failures.

Actions:

- Roll back to prior image if deployment-related.
- Scale Container App temporarily.
- Check SQL availability and firewall/private endpoint status.
- Validate `CLIENT_ID`, CORS origins, and Key Vault references.

### Service Bus Messages Are Stuck

Check:

- Active message count by subscription.
- Worker replica count.
- Worker logs for auth, SQL, or handler errors.
- Dead-letter queue.
- KEDA scale settings and identity permissions.

Actions:

- Restart worker Container App.
- Inspect DLQ messages.
- Use resubmit scripts where available, such as `scripts/resubmit_dlq.py`.
- Fix handler defects and redeploy.

### Nomination Submitted But Manager Not Notified

Check:

- Nomination status.
- Was `nomination.submitted` published?
- Fraud processor logs.
- Did integrity worker publish `nomination.created` or `nomination.fraud-flagged`?
- Email processor logs.
- `ProcessedEvents` entries for message ID/event type.
- SMTP/email failures.

Likely causes:

- Integrity worker error.
- Service Bus publish failure.
- Description rejection or HRBP route instead of manager route.
- Email template missing.
- SMTP credential failure.

### HRBP Review Queue Is Growing

Check:

- HRBP queue count and age.
- SLA Logic App history.
- HRBP notification handler logs.
- HRBP role assignments.
- Fraud thresholds in tenant integrity config.

Actions:

- Notify HRBP reviewers.
- Review threshold tuning.
- Confirm HRBP SLA breach events are publishing.
- Add temporary HRBP reviewers.

### Payroll Submission Failed

Check:

- Payroll broker logs.
- `payroll_submissions` status and reason.
- Provider token validity.
- Provider webhook signature errors.
- `payroll.failed` event delivery to auxiliary worker.
- Support notification emails.

Actions:

- Refresh provider OAuth connection.
- Resubmit provider request if safe.
- Manually reconcile payroll if required.
- Update nomination reason/status trail.

### Fraud Analytics Job Failed

Check:

- Container Apps Job execution logs.
- SQL wake-up attempts.
- Blob upload errors.
- Python dependency/model memory errors.
- Stage summary in job logs.
- API model refresh callback result.

Actions:

- Start job manually after fixing issue.
- Run a single stage with `--only` where supported.
- Keep prior model artifact active until successful retrain.

### Login Failures Increase

Check:

- Entra app registration settings.
- API audience mismatch.
- Tenant registered in `Tenants`.
- User present in `Users`.
- CORS origins.
- Frontend auth config variables.
- Domain mismatch messages.

Actions:

- Correct tenant mapping.
- Re-seed or sync user roster.
- Validate app role assignments.
- Update allowed origins and tenant domains.

## Alert Recommendations

| Alert | Signal |
| --- | --- |
| Backend high error rate | 5xx rate above threshold for 5 minutes. |
| P95 latency high | Backend P95 latency above target. |
| Service Bus DLQ growth | Dead-letter count greater than 0 for critical subscriptions. |
| Fraud analytics job failure | Container Apps Job non-zero exit. |
| HRBP SLA breach | Count of stale `PendingHRBPReview` nominations. |
| Payroll failures | `payroll.failed` event count above 0. |
| Email failure rate | Auxiliary email failures above threshold. |
| Model unavailable | Integrity worker logs `model_available=false` above threshold. |
| Auth failure spike | 401/403 rate above baseline. |

