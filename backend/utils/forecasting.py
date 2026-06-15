"""
utils/forecasting.py
====================
Predictive forecasting for the Award Nomination System.

This module is **purely numerical** — it takes plain Python/NumPy inputs and
returns plain dicts. It performs no database or network I/O, which keeps it
unit-testable in isolation (see the synthetic-data harness) and cheap enough to
run on-demand inside an HTTP request.

Two products are built on the same daily time series of nomination activity:

  1. HRBP review-queue load forecast  (primary — staffing / capacity planning)
       projected nominations/week
         × historical flag/review rate          → projected HRBP reviews/week
         ↳ translated to expected queue depth via Little's Law using the
           average days-to-approval SLA.

  2. Recognition-budget pacing  (secondary — same data, cheap to add)
       projected cumulative award spend vs. the tenant's annual budget,
       reporting the date the budget is expected to be exhausted.

Modelling choices (and their honesty constraints)
-------------------------------------------------
* Model: **Holt's linear trend** (a.k.a. double exponential smoothing,
  ETS(A,A,N)). Level + trend, no seasonal term. We deliberately do NOT fit a
  seasonal component: reliable weekly/monthly seasonality needs many cycles of
  history, and faking it would produce dishonest precision. With limited
  history we fall back to a flat/linear projection and let the prediction
  intervals widen honestly.
* Smoothing parameters (alpha, beta) are fit by a coarse grid search that
  minimises one-step sum-of-squared-error — no SciPy optimiser dependency.
* Prediction intervals use the standard closed-form ETS additive-trend
  variance, so they widen with the forecast horizon as uncertainty compounds.
* We resample to a **weekly** series before modelling. Weekly buckets are the
  natural unit for staffing decisions and smooth out day-of-week noise without
  pretending to model that noise as seasonality.

Nothing here is tenant- or schema-specific; the router layer is responsible for
pulling the daily series and the scalar inputs (review rate, SLA, budget).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np

# ── Constants ──────────────────────────────────────────────────────────────────

# Minimum weekly observations before we trust a fitted trend. Below this we
# degrade to a flat projection (last level, zero trend) with wide intervals.
_MIN_WEEKS_FOR_TREND = 4

# Grid resolution for the alpha/beta search. 0.05 steps over (0, 1) is plenty
# given the weekly granularity, and keeps the fit well under a millisecond.
_GRID = np.round(np.arange(0.05, 1.0, 0.05), 2)


# ── z-score for a two-sided prediction interval ────────────────────────────────

def _z_for_confidence(confidence: float) -> float:
    """
    Inverse standard-normal CDF for a symmetric two-sided interval, via a
    rational approximation (Acklam). Avoids a SciPy dependency.

    confidence=0.80 → ~1.2816, 0.90 → ~1.6449, 0.95 → ~1.9600.
    """
    confidence = min(max(confidence, 0.50), 0.999)
    p = 1.0 - (1.0 - confidence) / 2.0  # upper tail prob for two-sided

    # Acklam's inverse normal CDF approximation.
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ── Daily → contiguous → weekly resampling ─────────────────────────────────────

def build_contiguous_daily(
    points: list[tuple[date, float]],
    end: Optional[date] = None,
) -> tuple[list[date], np.ndarray]:
    """
    Turn a sparse list of (date, value) rows — which omit zero-activity days —
    into a dense day-by-day array, filling missing days with 0.0.

    Returns (dates, values) sorted ascending, spanning min(date)..end inclusive.
    """
    if not points:
        return [], np.array([], dtype=float)

    by_day: dict[date, float] = {}
    for d, v in points:
        if isinstance(d, datetime):
            d = d.date()
        by_day[d] = by_day.get(d, 0.0) + float(v or 0.0)

    start = min(by_day)
    last = end or max(by_day)
    if last < start:
        last = start

    dates: list[date] = []
    vals: list[float] = []
    cur = start
    while cur <= last:
        dates.append(cur)
        vals.append(by_day.get(cur, 0.0))
        cur += timedelta(days=1)
    return dates, np.asarray(vals, dtype=float)


def _week_start(d: date) -> date:
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def resample_weekly(
    dates: list[date],
    values: np.ndarray,
    drop_partial_last: bool = True,
) -> tuple[list[date], np.ndarray]:
    """
    Sum a daily series into Monday-anchored weekly buckets.

    The final bucket is dropped if it is partial (fewer than 7 observed days),
    so a half-finished current week doesn't read as a volume collapse.
    """
    if not dates:
        return [], np.array([], dtype=float)

    buckets: dict[date, float] = {}
    counts: dict[date, int] = {}
    for d, v in zip(dates, values):
        ws = _week_start(d)
        buckets[ws] = buckets.get(ws, 0.0) + float(v)
        counts[ws] = counts.get(ws, 0) + 1

    weeks = sorted(buckets)
    if drop_partial_last and len(weeks) > 1 and counts[weeks[-1]] < 7:
        weeks = weeks[:-1]

    return weeks, np.asarray([buckets[w] for w in weeks], dtype=float)


# ── Holt's linear trend ────────────────────────────────────────────────────────

def _holt_run(y: np.ndarray, alpha: float, beta: float) -> tuple[float, float, np.ndarray]:
    """
    Run Holt's linear recursion over y. Returns (final_level, final_trend,
    one_step_residuals). Residuals are y_t - (l_{t-1} + b_{t-1}) for t >= 2.
    """
    n = len(y)
    level = y[0]
    trend = y[1] - y[0] if n > 1 else 0.0
    resid = []
    for t in range(1, n):
        fitted = level + trend          # one-step-ahead forecast for time t
        err = y[t] - fitted
        resid.append(err)
        new_level = alpha * y[t] + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        level, trend = new_level, new_trend
    return level, trend, np.asarray(resid, dtype=float)


def _fit_holt(y: np.ndarray) -> tuple[float, float]:
    """Grid-search alpha, beta minimising one-step SSE."""
    best = (0.3, 0.1)
    best_sse = math.inf
    for a in _GRID:
        for b in _GRID:
            _, _, resid = _holt_run(y, a, b)
            if resid.size == 0:
                continue
            sse = float(np.sum(resid ** 2))
            if sse < best_sse:
                best_sse, best = sse, (float(a), float(b))
    return best


def holt_forecast(
    y: np.ndarray,
    horizon: int,
    confidence: float = 0.80,
    floor_at_zero: bool = True,
) -> dict:
    """
    Fit Holt's linear trend to weekly series y and project `horizon` steps.

    Returns a dict with point forecasts and lower/upper prediction-interval
    arrays (length == horizon), plus the fitted params and per-step variance.

    Prediction-interval variance for ETS(A,A,N):
        Var(h) = sigma^2 * ( 1 + sum_{j=1}^{h-1} (alpha + j*alpha*beta)^2 )
    where sigma^2 is the one-step residual variance. This grows with h, so the
    bands fan out honestly as the horizon extends.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    z = _z_for_confidence(confidence)

    if n == 0:
        zeros = np.zeros(horizon)
        return {
            "point": zeros, "lower": zeros, "upper": zeros,
            "alpha": 0.0, "beta": 0.0, "sigma": 0.0,
            "variance": zeros, "degraded": True,
        }

    degraded = n < _MIN_WEEKS_FOR_TREND
    if degraded:
        # Too little history to trust a slope: project the mean of the last few
        # observations flat, with intervals from the observed spread.
        level = float(np.mean(y[-min(n, 4):]))
        trend = 0.0
        alpha, beta = 0.0, 0.0
        sigma = float(np.std(y)) if n > 1 else max(level * 0.5, 1.0)
        resid_var = sigma ** 2
        point = np.full(horizon, level, dtype=float)
        # Flat model: variance grows linearly with horizon (random-walk-ish).
        variance = resid_var * np.arange(1, horizon + 1, dtype=float)
    else:
        alpha, beta = _fit_holt(y)
        level, trend, resid = _holt_run(y, alpha, beta)
        # Unbiased-ish residual variance (guard tiny samples).
        dof = max(len(resid) - 2, 1)
        resid_var = float(np.sum(resid ** 2) / dof) if resid.size else 0.0
        sigma = math.sqrt(resid_var)
        h = np.arange(1, horizon + 1, dtype=float)
        point = level + h * trend
        # ETS(A,A,N) variance multiplier.
        mult = np.ones(horizon, dtype=float)
        for i, hh in enumerate(range(1, horizon + 1)):
            s = 1.0
            for j in range(1, hh):
                s += (alpha + j * alpha * beta) ** 2
            mult[i] = s
        variance = resid_var * mult

    sd = np.sqrt(variance)
    lower = point - z * sd
    upper = point + z * sd
    if floor_at_zero:
        point = np.maximum(point, 0.0)
        lower = np.maximum(lower, 0.0)
        upper = np.maximum(upper, 0.0)

    return {
        "point": point, "lower": lower, "upper": upper,
        "alpha": alpha, "beta": beta, "sigma": sigma,
        "variance": variance, "degraded": degraded,
    }


# ── Product 1: HRBP review-queue load ──────────────────────────────────────────

def forecast_review_load(
    daily_counts: list[tuple[date, float]],
    horizon_weeks: int,
    review_rate: float,
    avg_days_to_approval: float,
    confidence: float = 0.80,
    today: Optional[date] = None,
) -> dict:
    """
    Project HRBP review-queue load for the next `horizon_weeks`.

    Pipeline:
      daily nomination counts → contiguous → weekly volume
        → Holt forecast of weekly volume (with PIs)
        → × review_rate                       = projected reviews/week
        → × (avg_days_to_approval / 7)        = expected queue depth (Little's Law)

    Little's Law (L = λ·W): with arrivals λ reviews per week and an average
    time-in-system W = avg_days_to_approval weeks, the expected number of
    reviews concurrently in the queue is L = λ · (avg_days/7). Intervals scale
    linearly through the review_rate and SLA multipliers.
    """
    today = today or date.today()
    review_rate = max(float(review_rate), 0.0)
    sla_weeks = max(float(avg_days_to_approval), 0.0) / 7.0

    dates, daily = build_contiguous_daily(daily_counts, end=today)
    weeks, weekly = resample_weekly(dates, daily)

    fc = holt_forecast(weekly, horizon_weeks, confidence=confidence)

    # Observed weekly history (for the chart's "actuals" portion).
    history = [
        {"weekStart": w.isoformat(),
         "nominations": float(v),
         "reviews": round(float(v) * review_rate, 2)}
        for w, v in zip(weeks, weekly)
    ]

    last_week = weeks[-1] if weeks else _week_start(today)
    forecast = []
    for i in range(horizon_weeks):
        ws = last_week + timedelta(weeks=i + 1)
        vol, vlo, vup = fc["point"][i], fc["lower"][i], fc["upper"][i]
        rev, rlo, rup = vol * review_rate, vlo * review_rate, vup * review_rate
        forecast.append({
            "weekStart": ws.isoformat(),
            "weekIndex": i + 1,
            "projectedNominations": round(float(vol), 2),
            "projectedNominationsLower": round(float(vlo), 2),
            "projectedNominationsUpper": round(float(vup), 2),
            "projectedReviews": round(float(rev), 2),
            "projectedReviewsLower": round(float(rlo), 2),
            "projectedReviewsUpper": round(float(rup), 2),
            "projectedQueueDepth": round(float(rev * sla_weeks), 2),
            "projectedQueueDepthLower": round(float(rlo * sla_weeks), 2),
            "projectedQueueDepthUpper": round(float(rup * sla_weeks), 2),
        })

    return {
        "history": history,
        "forecast": forecast,
        "model": {
            "name": "holt_linear",
            "alpha": round(fc["alpha"], 3),
            "beta": round(fc["beta"], 3),
            "residualSigma": round(fc["sigma"], 3),
            "weeklyObservations": len(weeks),
            "degradedToFlat": fc["degraded"],
        },
    }


# ── Product 2: recognition-budget pacing ───────────────────────────────────────

def forecast_budget_pacing(
    daily_amounts: list[tuple[date, float]],
    annual_budget: float,
    horizon_weeks: int,
    confidence: float = 0.80,
    fiscal_year_start: Optional[date] = None,
    today: Optional[date] = None,
) -> Optional[dict]:
    """
    Project cumulative award spend against an annual budget and estimate the
    exhaustion date.

    Returns None if annual_budget is not supplied (the feature is opt-in: the
    admin enters their budget in the UI).

    Cumulative-variance note: the variance of a sum of forecast steps is taken
    as the sum of per-step variances (a mild independence approximation), so the
    cumulative band also widens with horizon.
    """
    if not annual_budget or annual_budget <= 0:
        return None

    today = today or date.today()
    fy_start = fiscal_year_start or date(today.year, 1, 1)

    dates, daily = build_contiguous_daily(daily_amounts, end=today)
    weeks, weekly = resample_weekly(dates, daily)

    # Spend booked so far this fiscal year (sum of weekly buckets >= fy_start).
    spent_to_date = float(sum(
        v for w, v in zip(weeks, weekly) if w >= fy_start
    ))

    fc = holt_forecast(weekly, horizon_weeks, confidence=confidence)
    z = _z_for_confidence(confidence)

    last_week = weeks[-1] if weeks else _week_start(today)
    cumulative = []

    # Actuals portion (cumulative within FY).
    run = 0.0
    for w, v in zip(weeks, weekly):
        if w >= fy_start:
            run += float(v)
            cumulative.append({
                "weekStart": w.isoformat(),
                "actual": round(run, 2),
                "projected": None, "lower": None, "upper": None,
            })

    # Forecast portion: cumulative point + cumulative-variance band.
    cum_point = spent_to_date
    cum_var = 0.0
    exhaustion = {"date": None, "lower": None, "upper": None}
    prev_point = spent_to_date
    prev_lo = spent_to_date
    prev_up = spent_to_date
    for i in range(horizon_weeks):
        ws = last_week + timedelta(weeks=i + 1)
        cum_point += float(fc["point"][i])
        cum_var += float(fc["variance"][i])
        sd = math.sqrt(cum_var)
        lo = cum_point - z * sd
        up = cum_point + z * sd
        cumulative.append({
            "weekStart": ws.isoformat(),
            "actual": None,
            "projected": round(cum_point, 2),
            "lower": round(max(lo, 0.0), 2),
            "upper": round(up, 2),
        })

        # First crossing of the budget for point / upper / lower tracks.
        def _cross(prev, cur, prev_ws):
            if prev < annual_budget <= cur and cur != prev:
                frac = (annual_budget - prev) / (cur - prev)
                return (prev_ws + timedelta(days=int(round(frac * 7)))).isoformat()
            return None

        if exhaustion["date"] is None:
            exhaustion["date"] = _cross(prev_point, cum_point, ws - timedelta(weeks=1))
        if exhaustion["upper"] is None:
            # Upper spend track exhausts soonest.
            exhaustion["upper"] = _cross(prev_up, up, ws - timedelta(weeks=1))
        if exhaustion["lower"] is None:
            exhaustion["lower"] = _cross(prev_lo, lo, ws - timedelta(weeks=1))
        prev_point, prev_lo, prev_up = cum_point, lo, up

    return {
        "annualBudget": round(float(annual_budget), 2),
        "fiscalYearStart": fy_start.isoformat(),
        "spentToDate": round(spent_to_date, 2),
        "projectedHorizonSpend": round(cum_point, 2),
        "projectedHorizonLower": round(max(cum_point - z * math.sqrt(cum_var), 0.0), 2),
        "projectedHorizonUpper": round(cum_point + z * math.sqrt(cum_var), 2),
        "budgetUtilizationAtHorizon": round(cum_point / annual_budget, 4) if annual_budget else None,
        "exhaustionDate": exhaustion["date"],
        "exhaustionDateEarliest": exhaustion["upper"],   # upper spend → earliest exhaustion
        "exhaustionDateLatest": exhaustion["lower"],     # lower spend → latest exhaustion
        "cumulative": cumulative,
    }


# ── Assemble UI shapes from stored weekly forecast rows ──────────────────────────
# These let the endpoint serve the weekly job's chosen-model forecast through the
# exact same reviewLoad / budgetPacing shapes the live Holt path produces, so the
# frontend contract is unchanged whether the source is the stored run or the
# live fallback.

def review_load_from_weekly(
    daily_counts: list[tuple[date, float]],
    weekly_rows: list[dict],
    review_rate: float,
    avg_days_to_approval: float,
    today: Optional[date] = None,
) -> dict:
    """Build the reviewLoad block from stored weekly nomination forecast rows.

    weekly_rows: ordered dicts with keys point/lower/upper/targetDate/model.
    """
    today = today or date.today()
    review_rate = max(float(review_rate), 0.0)
    sla_weeks = max(float(avg_days_to_approval), 0.0) / 7.0

    dates, daily = build_contiguous_daily(daily_counts, end=today)
    weeks, weekly = resample_weekly(dates, daily)
    history = [
        {"weekStart": w.isoformat(), "nominations": float(v),
         "reviews": round(float(v) * review_rate, 2)}
        for w, v in zip(weeks, weekly)
    ]

    forecast = []
    for i, row in enumerate(weekly_rows):
        vol = float(row["point"]); vlo = float(row["lower"]); vup = float(row["upper"])
        rev, rlo, rup = vol * review_rate, vlo * review_rate, vup * review_rate
        forecast.append({
            "weekStart": row["targetDate"],
            "weekIndex": i + 1,
            "projectedNominations": round(vol, 2),
            "projectedNominationsLower": round(vlo, 2),
            "projectedNominationsUpper": round(vup, 2),
            "projectedReviews": round(rev, 2),
            "projectedReviewsLower": round(rlo, 2),
            "projectedReviewsUpper": round(rup, 2),
            "projectedQueueDepth": round(rev * sla_weeks, 2),
            "projectedQueueDepthLower": round(rlo * sla_weeks, 2),
            "projectedQueueDepthUpper": round(rup * sla_weeks, 2),
        })

    model_name = weekly_rows[0]["model"] if weekly_rows else "n/a"
    return {
        "history": history,
        "forecast": forecast,
        "model": {"name": model_name, "weeklyObservations": len(weeks),
                  "degradedToFlat": False, "source": "stored_run"},
    }


def budget_pacing_from_weekly(
    daily_amounts: list[tuple[date, float]],
    weekly_spend_rows: list[dict],
    annual_budget: Optional[float],
    confidence: float = 0.80,
    fiscal_year_start: Optional[date] = None,
    today: Optional[date] = None,
) -> Optional[dict]:
    """Build the budgetPacing block from stored weekly spend forecast rows."""
    if not annual_budget or annual_budget <= 0 or not weekly_spend_rows:
        return None
    today = today or date.today()
    fy_start = fiscal_year_start or date(today.year, 1, 1)

    dates, daily = build_contiguous_daily(daily_amounts, end=today)
    weeks, weekly = resample_weekly(dates, daily)
    spent_to_date = float(sum(v for w, v in zip(weeks, weekly) if w >= fy_start))

    cumulative = []
    run = 0.0
    for w, v in zip(weeks, weekly):
        if w >= fy_start:
            run += float(v)
            cumulative.append({"weekStart": w.isoformat(), "actual": round(run, 2),
                               "projected": None, "lower": None, "upper": None})

    cum_pt = spent_to_date
    cum_lo = spent_to_date
    cum_up = spent_to_date
    exhaustion = {"date": None, "lower": None, "upper": None}
    prev_pt, prev_lo, prev_up = spent_to_date, spent_to_date, spent_to_date

    def _cross(prev, cur, prev_ws):
        if prev < annual_budget <= cur and cur != prev:
            frac = (annual_budget - prev) / (cur - prev)
            d = date.fromisoformat(prev_ws) + timedelta(days=int(round(frac * 7)))
            return d.isoformat()
        return None

    for row in weekly_spend_rows:
        prev_ws = row["targetDate"]
        cum_pt += float(row["point"]); cum_lo += float(row["lower"]); cum_up += float(row["upper"])
        cumulative.append({"weekStart": row["targetDate"], "actual": None,
                           "projected": round(cum_pt, 2),
                           "lower": round(max(cum_lo, 0.0), 2), "upper": round(cum_up, 2)})
        if exhaustion["date"] is None:
            exhaustion["date"] = _cross(prev_pt, cum_pt, prev_ws)
        if exhaustion["upper"] is None:
            exhaustion["upper"] = _cross(prev_up, cum_up, prev_ws)
        if exhaustion["lower"] is None:
            exhaustion["lower"] = _cross(prev_lo, cum_lo, prev_ws)
        prev_pt, prev_lo, prev_up = cum_pt, cum_lo, cum_up

    return {
        "annualBudget": round(float(annual_budget), 2),
        "fiscalYearStart": fy_start.isoformat(),
        "spentToDate": round(spent_to_date, 2),
        "projectedHorizonSpend": round(cum_pt, 2),
        "projectedHorizonLower": round(max(cum_lo, 0.0), 2),
        "projectedHorizonUpper": round(cum_up, 2),
        "budgetUtilizationAtHorizon": round(cum_pt / annual_budget, 4) if annual_budget else None,
        "exhaustionDate": exhaustion["date"],
        "exhaustionDateEarliest": exhaustion["upper"],
        "exhaustionDateLatest": exhaustion["lower"],
        "cumulative": cumulative,
    }
