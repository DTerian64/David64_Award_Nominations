"""
routers/analytics_router.py
============================
Admin analytics, integrity findings, and AI ask/investigate endpoints.
All routes require the AWard_Nomination_Admin role.

Routes
------
GET  /api/admin/analytics/overview
GET  /api/admin/analytics/spending-trends
GET  /api/admin/analytics/department-spending
GET  /api/admin/analytics/top-recipients
GET  /api/admin/analytics/top-nominators
GET  /api/admin/analytics/fraud-alerts
GET  /api/admin/analytics/approval-metrics
GET  /api/admin/analytics/diversity-metrics
GET  /api/admin/analytics/category-breakdown

GET  /api/admin/analytics/gnn/shadow-comparison

GET  /api/admin/analytics/integrity/runs
GET  /api/admin/analytics/integrity/findings
GET  /api/admin/analytics/integrity/findings/{finding_id}/export

GET    /api/admin/analytics/conversations
GET    /api/admin/analytics/conversations/{conversation_id}/messages
DELETE /api/admin/analytics/conversations/{conversation_id}
PATCH  /api/admin/analytics/conversations/{conversation_id}
POST   /api/admin/analytics/ask
POST   /api/admin/analytics/investigate
"""

import logging
import uuid
import json as _json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import utils.sqlhelper2 as sqlhelper
import utils.forecasting as forecasting
from auth import get_current_user_with_impersonation, require_role
from routers.schemas import User
from utils.export_utils import build_finding_workbook
from agents import AskAgent, AskResult
from agents.orchestrator import AgentsOrchestrator, OrchestratorResult

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

# Shared, stateless agent singletons
_ask_agent           = AskAgent()
_agents_orchestrator = AgentsOrchestrator()
_MAX_HISTORY_TURNS   = 10


# ── Models ────────────────────────────────────────────────────────────────────

class AnalyticsQuestion(BaseModel):
    question:        str
    conversation_id: str | None = None


class ConversationRename(BaseModel):
    title: str


# ── Standard analytics ────────────────────────────────────────────────────────

@router.get("/api/admin/analytics/overview")
async def get_analytics_overview(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get high-level analytics overview"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        metrics = sqlhelper.get_analytics_overview(tenant_id)
        return {
            'totalNominationsAllTime': metrics.get('totalNominations', 0),
            'totalAmountSpent':        metrics.get('totalAmount', 0),
            'approvedNominations':     metrics.get('approvedCount', 0),
            'pendingNominations':      metrics.get('pendingCount', 0),
            'averageAwardAmount':      metrics.get('avgAmount', 0),
            'rejectionRate':           metrics.get('rejectionRate', 0),
            'fraudAlertsThisMonth':    len(sqlhelper.get_fraud_alerts(tenant_id, limit=100))
        }
    except Exception as e:
        logger.error(f"Error fetching analytics overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/spending-trends")
async def get_spending_trends(
    days: int = Query(default=90, ge=1, le=365),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get spending trends over time"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        trends = sqlhelper.get_spending_trends(tenant_id, days=days)
        return [
            {
                'date':            row[0].isoformat(),
                'nominationCount': row[1],
                'amount':          row[2]
            }
            for row in trends
        ]
    except Exception as e:
        logger.error(f"Error fetching spending trends: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/department-spending")
async def get_department_spending(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get spending breakdown by department"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        departments = sqlhelper.get_department_spending(tenant_id)
        return [
            {
                'departmentName':  row[0] or 'Unknown',
                'nominationCount': row[1],
                'totalSpent':      row[2],
                'averageAmount':   row[3]
            }
            for row in departments
        ]
    except Exception as e:
        logger.error(f"Error fetching department spending: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/top-recipients")
async def get_top_recipients(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get top award recipients"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        recipients = sqlhelper.get_top_recipients(tenant_id, limit=limit)
        return [
            {
                'UserId':          row[0],
                'FirstName':       row[1],
                'LastName':        row[2],
                'nominationCount': row[3],
                'totalAmount':     row[4]
            }
            for row in recipients
        ]
    except Exception as e:
        logger.error(f"Error fetching top recipients: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/top-nominators")
async def get_top_nominators(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get top nominators"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        nominators = sqlhelper.get_top_nominators(tenant_id, limit=limit)
        return [
            {
                'UserId':          row[0],
                'FirstName':       row[1],
                'LastName':        row[2],
                'nominationCount': row[3],
                'totalAmount':     row[4]
            }
            for row in nominators
        ]
    except Exception as e:
        logger.error(f"Error fetching top nominators: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/fraud-alerts")
async def get_fraud_alerts(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get recent fraud detection alerts"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        alerts = sqlhelper.get_fraud_alerts(tenant_id, limit=limit)
        return [
            {
                'NominationId':    row[0],
                'fraudScore':      row[1],
                'riskLevel':       row[2],
                'flags':           row[3].split(',') if row[3] else [],
                'nominatorName':   f"{row[4]} {row[5]}",
                'beneficiaryName': f"{row[6]} {row[7]}",
                'amount':          row[8],
                'nominationDate':  row[9].isoformat()
            }
            for row in alerts
        ]
    except Exception as e:
        logger.error(f"Error fetching fraud alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/gnn/shadow-comparison")
async def get_gnn_shadow_comparison(
    limit: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """
    Random Forest vs GNN agreement for the tenant's latest GNN model version.

    The GNN is a shadow model (ADR-0002): its scores are persisted weekly but
    never routed on. This endpoint is the evaluation surface for that shadow run.
    The response always carries scoringMode so the client can label it — a GNN
    score displayed without that label reads as an operative decision.

    Returns 200 with {"available": false} rather than 404 when the tenant has no
    GNN scores. A tenant below the sample gate simply never trains, which is a
    designed outcome and not an error worth an alarming status code.
    """
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        data = sqlhelper.get_gnn_shadow_comparison(tenant_id, limit=limit)
        if not data:
            return {
                "available": False,
                "reason": "No GNN scores for this tenant yet. The tenant may be "
                          "below the training sample gate, or the weekly job may "
                          "not have run since the model was enabled.",
            }
        return {"available": True, **data}
    except Exception as e:
        logger.error(f"Error fetching GNN shadow comparison: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/approval-metrics")
async def get_approval_metrics(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get approval and rejection metrics"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        return sqlhelper.get_approval_metrics(tenant_id)
    except Exception as e:
        logger.error(f"Error fetching approval metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/diversity-metrics")
async def get_diversity_metrics(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get award distribution diversity metrics"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        return sqlhelper.get_diversity_metrics(tenant_id)
    except Exception as e:
        logger.error(f"Error fetching diversity metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/category-breakdown")
async def get_category_breakdown(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """
    Return nomination counts and spend broken down by award category.
    Returns an empty list for tenants with no nomination categories configured.
    """
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        rows = sqlhelper.get_category_breakdown(tenant_id)
        return [
            {
                "categoryDescription": row[0],
                "nominationCount":     row[1],
                "totalAmount":         float(row[2]) if row[2] is not None else 0.0,
                "avgAmount":           round(float(row[3]), 2) if row[3] is not None else 0.0,
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Error fetching category breakdown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Forecasting — predictive review-load & budget pacing ─────────────────────

# Fallback flag/review rate when a tenant has no historical HRBP flags to learn
# from (e.g. a fresh tenant). Kept conservative; the response flags when it is used.
_DEFAULT_REVIEW_RATE = 0.15
# Fallback SLA (avg days-to-approval) when approval metrics are unavailable.
_DEFAULT_AVG_DAYS_TO_APPROVAL = 12.6


@router.get("/api/admin/analytics/forecast")
async def get_forecast(
    weeks: int = Query(default=8, ge=1, le=26,
                       description="Forecast horizon in weeks"),
    history_days: int = Query(default=180, ge=28, le=730,
                              description="Days of history to learn from"),
    annual_budget: float | None = Query(default=None, ge=0,
                                        description="Annual recognition budget for pacing (omit to skip)"),
    confidence: float = Query(default=0.80, ge=0.50, le=0.99,
                              description="Prediction-interval confidence level"),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """
    Predictive forecast over the tenant's daily nomination series.

    Primary: HRBP review-queue load — projected nominations/week × historical
    flag/review rate, translated to expected queue depth via Little's Law using
    the average days-to-approval SLA.

    Secondary: recognition-budget pacing — projected cumulative spend vs.
    `annual_budget`, with an estimated exhaustion date. Omitted when no budget
    is supplied.

    Model: Holt's linear trend (no seasonal term) with closed-form prediction
    intervals that widen with the horizon. See utils/forecasting.py.
    """
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        # ── Core daily series (date, count, amount) ──────────────────────────
        trends = sqlhelper.get_spending_trends(tenant_id, days=history_days)
        daily_counts  = [(row[0], float(row[1] or 0)) for row in trends]
        daily_amounts = [(row[0], float(row[2] or 0)) for row in trends]

        # ── Scalar inputs: review rate + SLA ─────────────────────────────────
        rate_info = sqlhelper.get_review_rate(tenant_id, days=history_days)
        review_rate = rate_info["reviewRate"]
        review_rate_is_default = review_rate <= 0.0
        if review_rate_is_default:
            review_rate = _DEFAULT_REVIEW_RATE

        approval = sqlhelper.get_approval_metrics(tenant_id) or {}
        avg_days = approval.get("avgDaysToApproval") or 0
        avg_days_is_default = not avg_days or avg_days <= 0
        if avg_days_is_default:
            avg_days = _DEFAULT_AVG_DAYS_TO_APPROVAL

        # ── Prefer the latest stored run (weekly job); fall back to live Holt ─
        run = sqlhelper.get_latest_forecast_run(tenant_id)
        source = "live_fallback"
        run_id = None
        run_generated_at = None
        model_comparison = None
        forecasts_payload = None

        nom_weekly_rows = []
        if run:
            rows = sqlhelper.get_forecasts(run["runId"])
            nom_weekly_rows  = [r for r in rows if r["series"] == "nominations" and r["level"] == "total" and r["grain"] == "weekly"]
            spend_weekly_rows = [r for r in rows if r["series"] == "spend" and r["level"] == "total" and r["grain"] == "weekly"]
            nom_daily_rows   = [r for r in rows if r["series"] == "nominations" and r["level"] == "total" and r["grain"] == "daily"]
            dept_rows        = [r for r in rows if r["level"] == "department"]

        if run and nom_weekly_rows:
            source = "stored_run"
            run_id = run["runId"]
            run_generated_at = run["generatedAt"]
            model_comparison = run.get("metrics")
            review_load = forecasting.review_load_from_weekly(
                daily_counts, nom_weekly_rows, review_rate, avg_days)
            budget_pacing = forecasting.budget_pacing_from_weekly(
                daily_amounts, spend_weekly_rows, annual_budget, confidence=confidence)

            depts: dict = {}
            for r in dept_rows:
                d = depts.setdefault(r["department"], {
                    "title": r["department"],
                    "nominationsModel": None, "spendModel": None,
                    "nominations": [], "spend": [],
                })
                pt = {"weekStart": r["targetDate"], "point": r["point"],
                      "lower": r["lower"], "upper": r["upper"]}
                if r["series"] == "spend":
                    d["spendModel"] = r["model"]
                    d["spend"].append(pt)
                else:
                    d["nominationsModel"] = r["model"]
                    d["nominations"].append(pt)
            # Weekly observed spend history (for the spend chart's history segment).
            _sdates, _sdaily = forecasting.build_contiguous_daily(
                daily_amounts, end=datetime.utcnow().date())
            _sweeks, _svals = forecasting.resample_weekly(_sdates, _sdaily)
            spend_history = [{"weekStart": w.isoformat(), "amount": float(v)}
                             for w, v in zip(_sweeks, _svals)]

            forecasts_payload = {
                "nominationsWeekly": [{"weekStart": r["targetDate"], "point": r["point"],
                                       "lower": r["lower"], "upper": r["upper"], "model": r["model"]}
                                      for r in nom_weekly_rows],
                "spendWeekly": [{"weekStart": r["targetDate"], "point": r["point"],
                                 "lower": r["lower"], "upper": r["upper"], "model": r["model"]}
                                for r in spend_weekly_rows],
                "spendHistory": spend_history,
                "nominationsDaily": [{"date": r["targetDate"], "point": r["point"],
                                      "lower": r["lower"], "upper": r["upper"]}
                                     for r in nom_daily_rows],
                "departments": list(depts.values()),
            }
            degraded = False
            note = (f"Served from weekly model run {run_id[:8]} "
                    f"(generated {run_generated_at}). Per-series model chosen by backtest MASE.")
        else:
            review_load = forecasting.forecast_review_load(
                daily_counts=daily_counts, horizon_weeks=weeks, review_rate=review_rate,
                avg_days_to_approval=avg_days, confidence=confidence)
            budget_pacing = forecasting.forecast_budget_pacing(
                daily_amounts=daily_amounts, annual_budget=annual_budget,
                horizon_weeks=weeks, confidence=confidence)
            degraded = review_load["model"]["degradedToFlat"]
            note = ("Live fallback (Holt linear) — the weekly model run is not available "
                    "yet. " + ("Limited history; intervals widened honestly."
                               if degraded else "Intervals widen with the horizon."))

        weekly_obs = review_load["model"]["weeklyObservations"]

        return {
            "generatedAt":   run_generated_at or (datetime.utcnow().isoformat() + "Z"),
            "tenantId":      tenant_id,
            "horizonWeeks":  weeks,
            "historyDays":   history_days,
            "confidence":    confidence,
            "source":        source,
            "runId":         run_id,
            "modelComparison": model_comparison,
            "forecasts":     forecasts_payload,
            "inputs": {
                "reviewRate":              round(review_rate, 4),
                "reviewRateIsDefault":     review_rate_is_default,
                "flaggedNominations":      rate_info["flaggedNominations"],
                "totalNominationsWindow":  rate_info["totalNominations"],
                "avgDaysToApproval":       round(float(avg_days), 2),
                "avgDaysToApprovalIsDefault": avg_days_is_default,
                "weeklyObservations":      weekly_obs,
                "seasonalityUsed":         False,
                "note":                    note,
            },
            "reviewLoad":    review_load,
            "budgetPacing":  budget_pacing,
        }
    except Exception as e:
        logger.error(f"Error computing forecast: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Integrity — graph pattern findings ───────────────────────────────────────

@router.get("/api/admin/analytics/integrity/runs")
async def get_integrity_runs(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Return the list of weekly job runs available for the tenant."""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        return sqlhelper.get_integrity_runs(tenant_id)
    except Exception as e:
        logger.error(f"Error fetching integrity runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/integrity/findings")
async def get_integrity_findings(
    run_id: str = Query(..., description="RunId UUID from the integrity runs list"),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Return all graph pattern findings for a specific run."""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        return sqlhelper.get_integrity_findings(tenant_id, run_id)
    except Exception as e:
        logger.error(f"Error fetching integrity findings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/analytics/integrity/findings/{finding_id}/export")
async def export_integrity_finding(
    finding_id: int,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """Stream an Excel workbook for a single integrity finding."""
    actual_user = current_user["actual_user"]
    data = sqlhelper.get_finding_with_nominations(finding_id, actual_user["TenantId"])
    if data is None:
        raise HTTPException(status_code=404, detail="Finding not found")

    buf = build_finding_workbook(data)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="finding_{finding_id}_export.xlsx"'},
    )


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/api/admin/analytics/conversations")
async def list_conversations(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """Return the current user's conversations, newest first."""
    actual_user = current_user["actual_user"]
    return sqlhelper.get_conversations(
        user_id=actual_user["UserId"], tenant_id=actual_user["TenantId"]
    )


@router.get("/api/admin/analytics/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """Return all messages for a conversation (tenant-scoped)."""
    actual_user = current_user["actual_user"]
    messages = sqlhelper.get_messages(
        conversation_id=conversation_id, tenant_id=actual_user["TenantId"]
    )
    if not messages:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages


@router.delete("/api/admin/analytics/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """Delete a conversation and all its messages."""
    actual_user = current_user["actual_user"]
    deleted = sqlhelper.delete_conversation(
        conversation_id=conversation_id,
        user_id=actual_user["UserId"],
        tenant_id=actual_user["TenantId"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


@router.patch("/api/admin/analytics/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    req: ConversationRename,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin")),
):
    """Rename a conversation title."""
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be empty")
    actual_user = current_user["actual_user"]
    updated = sqlhelper.rename_conversation(
        conversation_id=conversation_id,
        user_id=actual_user["UserId"],
        tenant_id=actual_user["TenantId"],
        title=title,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "renamed", "title": title}


# ── AI ask / investigate ──────────────────────────────────────────────────────

@router.post("/api/admin/analytics/ask")
async def ask_analytics_question(
    req: AnalyticsQuestion,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Ask a question, persisting history in SQL Server."""
    actual_user     = current_user["actual_user"]
    tenant_id       = actual_user["TenantId"]
    user_id         = actual_user["UserId"]
    conversation_id = req.conversation_id or str(uuid.uuid4())

    raw = sqlhelper.get_messages(conversation_id, tenant_id)
    if not raw:
        title = req.question[:80] + ("…" if len(req.question) > 80 else "")
        sqlhelper.create_conversation(conversation_id, user_id, tenant_id, title)
        history: list[dict] = []
    else:
        history = [{"role": m["role"], "content": m["content"]} for m in raw]
        history = history[-(_MAX_HISTORY_TURNS * 2):]

    logger.info(
        "ask endpoint: %s (tenant_id=%d, conv=%s, history=%d turns)",
        req.question[:80], tenant_id, conversation_id, len(history) // 2,
    )

    sqlhelper.append_message(conversation_id, "user", req.question)

    result: AskResult = await _ask_agent.ask(
        req.question,
        tenant_id    = tenant_id,
        current_user = actual_user,
        history      = history or None,
    )

    if result.error:
        logger.error("ask endpoint: agent error: %s", result.error)
        raise HTTPException(status_code=500, detail=f"AI Service Error: {result.error}")

    export_payload: dict | None = None
    if result.export_path:
        fmt = (result.export_format or "file").upper()
        filename = result.export_path.split("?")[0].split("/")[-1]
        export_payload = {
            "format":       result.export_format,
            "file_size":    result.export_size,
            "label":        f"Download your {fmt} here",
            "filename":     filename,
            "download_url": result.export_path,
        }

    sqlhelper.append_message(
        conversation_id, "assistant", result.answer,
        export_json=_json.dumps(export_payload) if export_payload else None,
    )

    logger.info(
        "ask endpoint: answered (conv=%s, sql=%s, rows=%d)",
        conversation_id, bool(result.sql), result.rows_fetched,
    )

    return {
        "conversation_id": conversation_id,
        "question":        result.question,
        "answer":          result.answer,
        **({"export": export_payload} if export_payload else {}),
    }


@router.post("/api/admin/analytics/investigate")
async def investigate_analytics_question(
    req: AnalyticsQuestion,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """
    Deep multi-agent investigation using AgentsOrchestrator (Azure OpenAI GPT-4.1).

    Orchestrates three specialised sub-agents:
      • Fraud Analyst  — SQL queries, fraud scores, graph traversal
      • Export Agent   — builds Excel/CSV/PDF files from analyst findings
      • Notification   — sends emails or stubs calendar events
    """
    actual_user     = current_user["actual_user"]
    tenant_id       = actual_user["TenantId"]
    user_id         = actual_user["UserId"]
    conversation_id = req.conversation_id or str(uuid.uuid4())

    raw = sqlhelper.get_messages(conversation_id, tenant_id)
    if not raw:
        title = req.question[:80] + ("…" if len(req.question) > 80 else "")
        sqlhelper.create_conversation(conversation_id, user_id, tenant_id, title)
        history: list[dict] = []
    else:
        history = [{"role": m["role"], "content": m["content"]} for m in raw]
        history = history[-(_MAX_HISTORY_TURNS * 2):]

    logger.info(
        "investigate endpoint: '%s' (tenant_id=%d, conv=%s, history=%d turns)",
        req.question[:80], tenant_id, conversation_id, len(history) // 2,
    )

    sqlhelper.append_message(conversation_id, "user", req.question)

    result: OrchestratorResult = await _agents_orchestrator.investigate(
        req.question,
        tenant_id=tenant_id,
        history=history or None,
    )

    if result.error and not result.answer:
        logger.error("investigate endpoint: orchestrator error: %s", result.error)
        raise HTTPException(status_code=500, detail=f"Investigation Error: {result.error}")

    export_payload: dict | None = None
    if result.export_url:
        exp = result.export
        fmt = (exp.export_format or "file").upper() if exp else "FILE"
        filename = result.export_url.split("?")[0].split("/")[-1]
        export_payload = {
            "format":       exp.export_format if exp else "file",
            "file_size":    exp.export_size   if exp else 0,
            "label":        f"Download your {fmt} here",
            "filename":     filename,
            "download_url": result.export_url,
        }

    sqlhelper.append_message(
        conversation_id, "assistant", result.answer,
        export_json=_json.dumps(export_payload) if export_payload else None,
    )

    logger.info(
        "investigate endpoint: complete (conv=%s, iterations=%d, export=%s)",
        conversation_id, result.iterations, bool(export_payload),
    )

    return {
        "conversation_id": conversation_id,
        "question":        result.question,
        "answer":          result.answer,
        **({"export": export_payload} if export_payload else {}),
    }
