"""
forecast_models.py — Forecasting stage of the weekly analytics job
==================================================================
For each tenant, runs a model bake-off over the nomination time series and
writes the chosen forecast to dbo.ForecastRuns / dbo.Forecasts, which the
analytics API reads (see /api/admin/analytics/forecast).

Models compared (per series, by rolling-origin backtest MASE):
  • Seasonal-Naive   — baseline (repeat last seasonal cycle)
  • ETS / Holt-Winters (statsmodels) — additive trend + annual seasonality
  • LightGBM         — lag + calendar features; quantile models give bands

Series produced:
  total / nominations / weekly      (chosen model, H weeks)
  total / spend       / weekly      (chosen model, H weeks)
  total / nominations / daily       (LightGBM, H*7 days — production granularity)
  department / nominations / weekly (per-dept best of global-LGBM vs ETS)

Stat models run on weekly aggregation (annual seasonality learnable); LightGBM
runs daily for the total. Department series are forecast by a single GLOBAL
LightGBM with Title as a categorical (pools strength across sparse titles),
compared per-department against ETS, best kept.

Invoked by run_job.run_stage("forecast_models"). Per-tenant failures are logged
and skipped so one tenant cannot fail the whole stage.

Env: SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD (same as the other stages).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyodbc
from dotenv import load_dotenv

# Same .env loading as train_fraud_model.py / graph_pattern_detector.py so this
# stage can be run standalone locally. No-op in Container Apps (env injected).
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

warnings.filterwarnings("ignore")
logger = logging.getLogger("forecast_models")

# ── Tuning knobs ──────────────────────────────────────────────────────────────
HORIZON_WEEKS = 8       # how many weeks ahead each forecast projects
BACKTEST_FOLDS = 5      # rolling-origin folds used to score/select models
SEASON_WEEKS = 52       # seasonal period for weekly data (annual cycle)
CONFIDENCE = 0.80       # prediction-interval coverage (→ 10th/90th pctile bands)
Z = 1.2816              # ~80% two-sided normal quantile (for the stat models' bands)
HISTORY_DAYS = 900      # how far back to pull history (~2.5 years)
TOP_DEPARTMENTS = 6     # model the N busiest titles individually; rest → "Other"

# Holidays for the is_holiday calendar feature are loaded per-tenant from
# dbo.Holidays (populated by the sync_holidays stage, keyed by country) into this
# module-level set before each tenant is forecast. An empty set simply means
# is_holiday is always 0 — harmless, never an error.
_HOLIDAY_SET: set = set()


# ── DB ──────────────────────────────────────────────────────────────────────────

def get_db_connection():
    """Open an Azure SQL connection from env vars (same convention as the other
    stages). The DB is already awake by the time this runs — run_job.py calls
    wake_database() before any stage."""
    return pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USER')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )


def get_tenants(conn) -> list:
    """Return [(TenantId, TenantName), ...] — we forecast each tenant separately."""
    df = pd.read_sql("SELECT TenantId, TenantName FROM dbo.Tenants ORDER BY TenantId", conn)
    return list(df.itertuples(index=False, name=None))


def load_nominations(conn, tenant_id: int) -> pd.DataFrame:
    """One row per nomination (ds, amount, title) within the last HISTORY_DAYS.

    Tenant scoping is via the *beneficiary's* Title/TenantId (the recipient's
    department) — matching how Spending Trends groups departments. Returns the
    raw per-record rows; aggregation into daily/weekly series happens upstream.
    """
    q = """
        SELECT n.NominationDate AS ds, n.Amount AS amount, u.Title AS title
        FROM dbo.Nominations n
        JOIN dbo.Users u ON u.UserId = n.BeneficiaryId
        WHERE u.TenantId = ?
          AND n.NominationDate >= DATEADD(DAY, ?, CAST(GETDATE() AS DATE))
    """
    return pd.read_sql(q, conn, params=[tenant_id, -abs(HISTORY_DAYS)])


def _tenant_country(conn, tenant_id: int) -> str | None:
    """Country code from the tenant's Config.locale ('en-US' → 'US'); None if absent."""
    df = pd.read_sql("SELECT Config FROM dbo.Tenants WHERE TenantId = ?", conn, params=[tenant_id])
    if df.empty or not df.iloc[0]["Config"]:
        return None
    try:
        locale = json.loads(df.iloc[0]["Config"]).get("locale", "")
    except Exception:
        return None
    if not locale or "-" not in locale:
        return None
    region = locale.split("-")[-1].strip().upper()
    return region[:2] if len(region) >= 2 else None


def load_holidays(conn, country: str | None) -> set:
    """Holiday dates for a country from dbo.Holidays (empty set if none / no country).

    Populated by the sync_holidays stage. Reading from SQL (not the network) means
    forecasting never depends on a live fetch succeeding.
    """
    if not country:
        return set()
    df = pd.read_sql(
        "SELECT HolidayDate FROM dbo.Holidays WHERE CountryCode = ?", conn, params=[country])
    if df.empty:
        return set()
    return {ts.date() for ts in pd.to_datetime(df["HolidayDate"])}


# ── Aggregation ─────────────────────────────────────────────────────────────────

def daily_frame(ds_series, value_series, start, end) -> pd.DataFrame:
    """Collapse per-nomination rows into a dense daily series [ds, y].

    Sums value_series per calendar day, then reindexes onto a *contiguous*
    day-by-day range start..end so that days with no nominations show up as 0
    (instead of being missing). Pass value_series = ones for a count series, or
    the Amount column for a spend series.
    """
    s = pd.Series(np.asarray(value_series, dtype=float),
                  index=pd.to_datetime(pd.Series(ds_series).dt.date))
    daily = s.groupby(s.index).sum()
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    daily = daily.reindex(idx, fill_value=0.0)   # fill zero-activity days
    return pd.DataFrame({"ds": idx, "y": daily.values})


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample a daily [ds, y] frame into Monday-anchored weekly sums.

    The statistical models run on weekly data (the annual cycle is learnable at
    52 weeks; daily would be mostly day-of-week noise we didn't model). The final
    bucket is dropped if it's a partial week (< 7 observed days) so an in-progress
    current week doesn't read as a sudden volume collapse.
    """
    g = df.set_index("ds")["y"].resample("W-MON", label="left", closed="left")
    s, counts = g.sum(), g.count()
    if len(counts) and counts.iloc[-1] < 7:      # drop a partial trailing week
        s = s.iloc[:-1]
    return pd.DataFrame({"ds": s.index, "y": s.values})


# ── Metrics ─────────────────────────────────────────────────────────────────────
# All three compare a forecast (y_pred) against what actually happened (y_true)
# on a held-out slice of history. They are the scores the rolling backtest uses to
# rank the candidate models; the lowest-MASE model is the one we keep per series.

def mase(y_true, y_pred, y_train, m):
    """Mean Absolute Scaled Error — the primary model-selection metric.

    MASE = (mean absolute error of the forecast)
           ──────────────────────────────────────────────────────────
           (mean absolute error of a naive 'repeat one season ago' forecast)

    The denominator is computed on the TRAINING data, so the metric is
    *scale-free*: a MASE of 1.0 means "no better than the seasonal-naive
    baseline", < 1.0 means "better than naive", > 1.0 means "worse than naive".
    Being unit-independent lets us compare across series with different scales
    (e.g. nomination counts vs. dollar spend) and it is robust to zero-activity
    weeks that would make a percentage error (MAPE) blow up.

    Args:
        y_true:  actual values over the forecast window.
        y_pred:  forecasted values over the same window.
        y_train: the training history (used only to scale the error).
        m:       seasonal period (52 for weekly data with an annual cycle).
    """
    y_true, y_pred, y_train = map(lambda a: np.asarray(a, float), (y_true, y_pred, y_train))
    # Denominator: average week-over-season change in the training data — i.e. the
    # error a "same week last cycle" naive forecast would have made in-sample.
    denom = np.mean(np.abs(y_train[m:] - y_train[:-m])) if len(y_train) > m else 0
    # Fallback when there isn't a full season of history: use the lag-1 naive
    # difference instead (and guard against a divide-by-zero with 1e-9).
    if not denom:
        denom = np.mean(np.abs(np.diff(y_train))) or 1e-9
    return float(np.mean(np.abs(y_true - y_pred)) / denom)


def smape(y_true, y_pred):
    """Symmetric Mean Absolute Percentage Error, as a percentage (0–200%).

    Like MAPE but symmetric: the error is divided by the average of the actual
    and the forecast, so over- and under-prediction are penalised evenly and it
    doesn't explode toward infinity when an actual is near zero. Reported for
    human readability ("~11% off") alongside MASE, which drives selection.
    """
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    # Denominator |actual| + |forecast|; clamp exact zeros to 1e-9 to avoid 0/0.
    d = np.abs(y_true) + np.abs(y_pred); d[d == 0] = 1e-9
    return float(100 * np.mean(2 * np.abs(y_pred - y_true) / d))


def rmse(y_true, y_pred):
    """Root Mean Squared Error — average error in the series' own units.

    Squaring penalises large misses more than small ones, so RMSE is sensitive
    to occasional big errors. Kept for context (it's in the original units, e.g.
    "off by ~22 nominations/week"); it is not used to choose the model.
    """
    return float(np.sqrt(np.mean((np.asarray(y_true, float) - np.asarray(y_pred, float)) ** 2)))


# ── Models ──────────────────────────────────────────────────────────────────────

def seasonal_naive(train, h, m, z=Z):
    """Baseline model: forecast = the value one full season (m steps) ago.

    For weekly data with m=52 this is "same week last year". It's the yardstick
    every other model has to beat (and the basis MASE is scaled against). Returns
    (point, lower, upper); the interval comes from the spread of in-sample
    seasonal differences, widened with the horizon. Falls back to last-value
    naive when there isn't a full season of history yet.
    """
    train = np.asarray(train, float)
    if len(train) >= m:
        base = train[-m:]                                   # last full season
        point = np.array([base[i % m] for i in range(h)])   # tile it forward
        resid = train[m:] - train[:-m]                      # in-sample seasonal errors
    else:
        point = np.full(h, train[-1] if len(train) else 0.0)  # repeat last value
        resid = np.diff(train) if len(train) > 1 else np.array([0.0])
    sigma = np.std(resid) if len(resid) else 0.0
    sd = sigma * np.sqrt(np.arange(1, h + 1) / max(m, 1) + 1)  # band grows with horizon
    return np.maximum(point, 0), np.maximum(point - z * sd, 0), point + z * sd


def ets(train, h, m, z=Z):
    """ETS / Holt-Winters exponential smoothing (the usual winner on this data).

    Models the series as level + trend + (optionally) an additive seasonal cycle,
    where recent observations are weighted more heavily than older ones. Uses an
    additive annual season when there are at least two full cycles of history
    (>= 2*m weeks); otherwise it drops the seasonal term (Holt's linear trend) so
    statsmodels has enough data to fit. Any fitting failure degrades gracefully to
    seasonal_naive so one bad series never crashes the run.

    Returns (point, lower, upper); the interval is built from the residual spread
    and widens as sqrt(horizon).
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    train = np.asarray(train, float)
    try:
        if len(train) >= 2 * m:                 # >= 2 seasonal cycles → fit seasonality
            fit = ExponentialSmoothing(train, trend="add", seasonal="add",
                                       seasonal_periods=m, initialization_method="estimated").fit()
        else:                                    # too little history → trend only (Holt)
            fit = ExponentialSmoothing(train, trend="add",
                                       initialization_method="estimated").fit()
        point = np.asarray(fit.forecast(h), float)
        sigma = np.std(train - fit.fittedvalues)   # one-step residual spread
    except Exception:
        return seasonal_naive(train, h, m, z)      # graceful fallback
    sd = sigma * np.sqrt(np.arange(1, h + 1))
    return np.maximum(point, 0), np.maximum(point - z * sd, 0), point + z * sd


def _calendar(ds: pd.Timestamp) -> dict:
    """Calendar features for a date — these give LightGBM its seasonality signal
    (it has no built-in notion of time; month/quarter/week/holiday encode it)."""
    return {"month": ds.month, "quarter": ds.quarter,
            "weekofyear": int(ds.isocalendar().week), "dayofyear": ds.dayofyear,
            "is_quarter_end_month": int(ds.month in (3, 6, 9, 12)),
            "is_holiday": int(ds.date() in _HOLIDAY_SET)}


def _supervised(df, lags, group_col=None):
    """Turn a time series into a supervised (X, y) table for LightGBM.

    Trees can't read a sequence directly, so we hand them the recent past as
    columns: lagged values (y shifted by each L in `lags`), a 4-period rolling
    mean, and calendar features. With group_col set (department Title), lags are
    computed *within each group* so series don't bleed into each other.
    """
    frames = []
    for g in (df[group_col].unique() if group_col else [None]):
        sub = (df[df[group_col] == g] if group_col else df).sort_values("ds").copy()
        for L in lags:
            sub[f"lag{L}"] = sub["y"].shift(L)               # value L periods ago
        sub["roll4"] = sub["y"].shift(1).rolling(4).mean()   # recent local average
        sub = pd.concat([sub, sub["ds"].apply(_calendar).apply(pd.Series)], axis=1)
        if group_col:
            sub[group_col] = g
        frames.append(sub)
    return pd.concat(frames, ignore_index=True)


def _flat(yvals, h, z=Z):
    """Last-value flat forecast with a spread-based widening band — the fallback
    when a series is too short to train LightGBM (so thin tenants never crash)."""
    yvals = np.asarray(yvals, float)
    last = float(yvals[-1]) if len(yvals) else 0.0
    spread = float(np.std(yvals)) if len(yvals) > 1 else max(abs(last) * 0.5, 1.0)
    sd = spread * np.sqrt(np.arange(1, h + 1))
    pt = np.full(h, max(last, 0.0))
    return pt, np.maximum(pt - z * sd, 0.0), pt + z * sd


def lgbm_forecast(history, h, freq, lags, group_col=None, confidence=CONFIDENCE, seed=42):
    """Gradient-boosted-tree forecaster over lag + calendar features.

    Fits THREE LightGBM models on the same features: a point model (regression)
    plus two quantile models for the lower/upper edges of the prediction band.
    When group_col is given it's a single *global* model across all departments,
    with Title as a categorical — this pools learning so sparse departments
    borrow strength from busy ones. Forecasts are produced *recursively*: each
    predicted step is appended to the history so it can feed the next step's lags.

    Returns (point, lower, upper) arrays — or, for a panel, a dict keyed by group.
    """
    import lightgbm as lgb
    # 80% interval → predict the 10th and 90th percentiles for the band edges.
    lo_q, hi_q = (1 - confidence) / 2, 1 - (1 - confidence) / 2
    feat = [f"lag{L}" for L in lags] + ["roll4", "month", "quarter", "weekofyear",
            "dayofyear", "is_quarter_end_month", "is_holiday"]
    cats = [group_col] if group_col else []
    if group_col:
        feat = feat + [group_col]
    # Build the training table. Require only the SHORTEST lag (the immediate prior
    # value); longer lags may be NaN for short-history tenants and LightGBM handles
    # missing values natively. This stops tenants with < a year of data (where the
    # 364-day lag is always NaN) from producing an empty training set and crashing.
    sup = _supervised(history, lags, group_col).dropna(subset=[f"lag{min(lags)}"])
    if sup.empty:                          # truly too little history → flat fallback
        if group_col:
            return {g: _flat(history[history[group_col] == g].sort_values("ds")["y"].values, h)
                    for g in history[group_col].unique()}
        return _flat(history.sort_values("ds")["y"].values, h)
    if group_col:
        sup[group_col] = sup[group_col].astype("category")
    X, y = sup[feat], sup["y"]
    p = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20,
             subsample=0.9, colsample_bytree=0.9, random_state=seed, verbose=-1)
    mp = lgb.LGBMRegressor(objective="regression", **p).fit(X, y, categorical_feature=cats or "auto")          # point
    ml = lgb.LGBMRegressor(objective="quantile", alpha=lo_q, **p).fit(X, y, categorical_feature=cats or "auto")  # lower band
    mh = lgb.LGBMRegressor(objective="quantile", alpha=hi_q, **p).fit(X, y, categorical_feature=cats or "auto")  # upper band
    step = pd.Timedelta(days=1) if freq == "D" else pd.Timedelta(weeks=1)
    out = {}
    # Forecast each series (one, or one per group) recursively, h steps ahead.
    for g in (history[group_col].unique() if group_col else [None]):
        hist = (history[history[group_col] == g] if group_col else history).sort_values("ds")
        yh = list(hist["y"].values)   # running history; predictions get appended
        last = hist["ds"].max()
        pt, lo, up = [], [], []
        for i in range(h):
            nds = last + step * (i + 1)                      # date of this step
            # Assemble the same feature row used in training, reading lags off the
            # running history (which now includes our own earlier predictions).
            row = {f"lag{L}": (yh[-L] if len(yh) >= L else np.nan) for L in lags}
            row["roll4"] = np.mean(yh[-4:]) if len(yh) >= 4 else np.nan
            row.update(_calendar(nds))
            if group_col:
                row[group_col] = g
            Xr = pd.DataFrame([row])[feat]
            if group_col:
                Xr[group_col] = Xr[group_col].astype("category")
            pv = max(float(mp.predict(Xr)[0]), 0.0)          # point (floored at 0)
            # Keep band edges sane: lower <= point <= upper.
            pt.append(pv)
            lo.append(min(max(float(ml.predict(Xr)[0]), 0.0), pv))
            up.append(max(float(mh.predict(Xr)[0]), pv))
            yh.append(pv)                                     # feed forward
        out[g] = (np.array(pt), np.array(lo), np.array(up))
    return out if group_col else out[None]


def rolling_backtest(y, model_fn, folds, h, m):
    """Rolling-origin (expanding-window) backtest of a single model.

    Walks the origin backward `folds` times. At each fold, train on everything up
    to a cut point, forecast the next h steps, and score those forecasts against
    the actuals that were held out. Averaging across folds estimates real
    out-of-sample accuracy (not in-sample fit). Returns the mean of each metric
    plus how many folds actually ran.

        train ──────────────►| forecast h →|   (fold k)
        train ───────────►| forecast h →|       (fold k-1)  … etc.

    A fold is skipped when there isn't enough history left of the cut (cut <= m)
    for MASE's seasonal denominator, so short series simply yield fewer folds.
    """
    y = np.asarray(y, float); n = len(y)
    # Adaptive seasonal period for scoring: only trust the annual season (m) when
    # there are >= 2 full cycles of history to validate it; otherwise fall back to a
    # non-seasonal MASE (m_eff = 1, scored vs a lag-1 naive). Without this, a tenant
    # with < ~14 months of weeks gets zero folds and an empty bake-off (every model
    # null → default to ETS). The models themselves still adapt internally.
    m_eff = m if n >= 2 * m else 1
    mae, sm, rm, ma, cov = [], [], [], [], []
    for k in range(folds, 0, -1):
        cut = n - k * h                 # origin for this fold
        if cut <= m_eff:                # need enough training behind the cut for the MASE scaler
            continue
        train, test = y[:cut], y[cut:cut + h]
        if len(test) < h:               # not a full horizon left to score
            continue
        pt, lo, up = model_fn(train, h); pt = pt[:len(test)]
        mae.append(float(np.mean(np.abs(test - pt)))); sm.append(smape(test, pt))
        rm.append(rmse(test, pt)); ma.append(mase(test, pt, train, m_eff))
        # coverage = fraction of actuals that fell inside the prediction band
        # (a calibrated 80% interval should land near 0.80).
        cov.append(float(np.mean((test >= lo[:len(test)]) & (test <= up[:len(test)]))))
    agg = lambda a: round(float(np.mean(a)), 4) if a else None
    return {"MASE": agg(ma), "sMAPE": agg(sm), "RMSE": agg(rm),
            "coverage": agg(cov), "folds": len(ma)}


# ── Per-tenant pipeline ─────────────────────────────────────────────────────────

def _model_fns(series_for_lgbm_end):
    """Build the three competing models as uniform ``fn(train, h) -> (pt, lo, up)``
    callables so the backtest can treat them interchangeably.

    The backtest passes only a bare numpy training array, but LightGBM needs a
    dated DataFrame to derive calendar features — so f_lgbm reconstructs a
    plausible weekly date index ending at the series' last week. (Only the
    calendar *pattern* matters here, not the absolute dates.)
    """
    def f_snaive(tr, h): return seasonal_naive(tr, h, SEASON_WEEKS)
    def f_ets(tr, h):    return ets(tr, h, SEASON_WEEKS)
    def f_lgbm(tr, h):
        df = pd.DataFrame({"ds": pd.date_range(end=series_for_lgbm_end, periods=len(tr),
                                               freq="W-MON"), "y": tr})
        return lgbm_forecast(df, h, "W", lags=[1, 2, 4, 8, 52])
    return {"SeasonalNaive": f_snaive, "ETS": f_ets, "LightGBM": f_lgbm}


def _bakeoff(weekly: pd.DataFrame):
    """The model contest for one weekly series.

    Backtests all three models, picks the one with the lowest MASE, then refits
    that winner on the *full* history to produce the actual forward forecast.
    Returns (metrics_per_model, chosen_name, final_forecast). ``metrics`` also
    carries a "chosen" key and is stored verbatim in ForecastRuns.Metrics so the
    UI's model-comparison table can show the head-to-head.
    """
    y = weekly["y"].values
    end = weekly["ds"].max()
    fns = _model_fns(end)
    # Score every model on the same rolling backtest.
    metrics = {name: rolling_backtest(y, fn, BACKTEST_FOLDS, HORIZON_WEEKS, SEASON_WEEKS)
               for name, fn in fns.items()}
    # Pick the lowest-MASE model that actually produced folds; default to ETS if
    # the series was too short for any backtest fold to run.
    ranked = [n for n in metrics if metrics[n]["MASE"] is not None]
    chosen = min(ranked, key=lambda n: metrics[n]["MASE"]) if ranked else "ETS"
    metrics["chosen"] = chosen
    final = fns[chosen](y, HORIZON_WEEKS)   # winner refit on all history
    return metrics, chosen, final


def forecast_tenant(conn, tenant_id: int) -> int:
    """End-to-end forecast for one tenant; writes one run and returns rows written.

    Pipeline: load nominations → aggregate to daily then weekly → bake off the
    total nominations and total spend series → add a daily LightGBM view → model
    every department (nominations and spend) → persist a ForecastRun plus all the
    Forecasts rows. Tenants with very little data are skipped.
    """
    df = load_nominations(conn, tenant_id)
    if df.empty or len(df) < 30:        # not enough signal to forecast meaningfully
        logger.info("[Tenant %s] too few nominations (%d) — skipping", tenant_id, len(df))
        return 0
    df["ds"] = pd.to_datetime(df["ds"])
    start = df["ds"].min().date()
    end = date.today()

    # Load this tenant's country holidays into the module set so the is_holiday
    # calendar feature is correct for whichever country the tenant operates in.
    global _HOLIDAY_SET
    _HOLIDAY_SET = load_holidays(conn, _tenant_country(conn, tenant_id))

    # Build daily series (count via ones, spend via Amount), then weekly versions.
    daily_cnt = daily_frame(df["ds"], np.ones(len(df)), start, end)
    daily_spd = daily_frame(df["ds"], df["amount"].fillna(0), start, end)
    wk_cnt, wk_spd = to_weekly(daily_cnt), to_weekly(daily_spd)

    run_id = str(uuid.uuid4())             # one RunId groups every row from this run
    rows = []   # accumulator: (Series, Level, Dept, Grain, TargetDate, Horizon, Model, point, lo, up)

    def add_weekly(series_name, weekly_df, point, lo, up, model, level="total", dept=None):
        """Append H weekly forecast rows, dating each step from the last observed week."""
        last = weekly_df["ds"].max()
        for i in range(len(point)):
            td = (last + pd.Timedelta(weeks=i + 1)).date()   # week-start of step i+1
            rows.append((series_name, level, dept, "weekly", td, i + 1, model,
                         float(point[i]), float(lo[i]), float(up[i])))

    # ── Totals: bake off nominations and spend at the weekly level ────────────
    m_cnt, chosen_cnt, (p, lo, up) = _bakeoff(wk_cnt)
    add_weekly("nominations", wk_cnt, p, lo, up, chosen_cnt)
    m_spd, chosen_spd, (p, lo, up) = _bakeoff(wk_spd)
    add_weekly("spend", wk_spd, p, lo, up, chosen_spd)

    # ── Daily nominations via LightGBM (finer granularity for the daily view) ──
    # Daily lags include 7/28/364 to capture weekly/monthly/annual structure.
    dp, dl, du = lgbm_forecast(daily_cnt, HORIZON_WEEKS * 7, "D", lags=[1, 7, 14, 28, 364])
    last_d = daily_cnt["ds"].max()
    for i in range(len(dp)):
        td = (last_d + pd.Timedelta(days=i + 1)).date()
        rows.append(("nominations", "total", None, "daily", td, i + 1, "LightGBM",
                     float(dp[i]), float(dl[i]), float(du[i])))

    # ── departments: model BOTH nominations and spend per department ──────────
    # Global LightGBM (pools across departments, helping sparse titles) vs a
    # per-department ETS on the holdout; the lower-MASE model is kept per dept.
    counts = df["title"].fillna("Unknown").value_counts()
    top = list(counts.head(TOP_DEPARTMENTS).index)
    df["bucket"] = df["title"].where(df["title"].isin(top), "Other")
    df["wk"] = df["ds"].dt.to_period("W-MON").dt.start_time

    def _dept_panel(agg: str) -> pd.DataFrame:
        """Weekly per-department panel: agg='count' (nominations) or 'amount' (spend)."""
        grp = df.groupby(["wk", "bucket"])
        s = grp.size() if agg == "count" else grp["amount"].sum()
        p = pd.DataFrame([{"ds": pd.Timestamp(wk), "Title": b, "y": float(v)}
                          for (wk, b), v in s.items()])
        if p.empty:
            return p
        p = p.sort_values(["Title", "ds"])
        mx = p["ds"].max()
        if (end - mx.date()).days < 6:        # drop partial last week
            p = p[p["ds"] < mx]
        return p

    def _run_dept(panel: pd.DataFrame, series_name: str) -> dict:
        md: dict = {}
        if panel.empty:
            return md
        glob = lgbm_forecast(panel, HORIZON_WEEKS, "W", lags=[1, 2, 4, 8, 52], group_col="Title")
        for t in panel["Title"].unique():
            s = panel[panel["Title"] == t].sort_values("ds")["y"].values
            chosen_d, gp = "GlobalLGBM", glob[t]
            if len(s) >= SEASON_WEEKS + HORIZON_WEEKS:        # holdout compare vs ETS
                tr, te = s[:-HORIZON_WEEKS], s[-HORIZON_WEEKS:]
                ptr = panel[panel["ds"] < panel["ds"].max() - pd.Timedelta(weeks=HORIZON_WEEKS - 1)]
                e_g = mase(te, lgbm_forecast(ptr, HORIZON_WEEKS, "W", lags=[1, 2, 4, 8, 52],
                                             group_col="Title")[t][0][:len(te)], tr, SEASON_WEEKS)
                e_e = mase(te, ets(tr, HORIZON_WEEKS, SEASON_WEEKS)[0][:len(te)], tr, SEASON_WEEKS)
                md[t] = {"GlobalLGBM": round(e_g, 4), "ETS": round(e_e, 4)}
                if e_e < e_g:
                    chosen_d, gp = "ETS", ets(s, HORIZON_WEEKS, SEASON_WEEKS)
            add_weekly(series_name, panel[panel["Title"] == t], gp[0], gp[1], gp[2],
                       chosen_d, level="department", dept=t)
        return md

    dept_metrics       = _run_dept(_dept_panel("count"),  "nominations")
    dept_spend_metrics = _run_dept(_dept_panel("amount"), "spend")

    metrics = {"nominations_total": m_cnt, "spend_total": m_spd,
               "departments": dept_metrics, "departments_spend": dept_spend_metrics}
    _persist(conn, run_id, tenant_id, start, end, metrics, rows)
    logger.info("[Tenant %s] forecast run %s — %d rows (nom=%s, spend=%s)",
                tenant_id, run_id[:8], len(rows), chosen_cnt, chosen_spd)
    return len(rows)


def _persist(conn, run_id, tenant_id, start, end, metrics, rows):
    """Write one ForecastRuns header row + all Forecasts detail rows for this run.

    The header carries the model-comparison metrics as JSON (the UI reads it);
    the detail rows are bulk-inserted with fast_executemany. The API serves the
    most recent run per tenant, so old runs are simply left in place as history.
    """
    cur = conn.cursor()
    # Header: one row per run, with the full per-model metrics blob.
    cur.execute("""
        INSERT INTO dbo.ForecastRuns
            (RunId, TenantId, GeneratedAt, HorizonWeeks, HistoryStart, HistoryEnd, Confidence, Metrics, Status)
        VALUES (?, ?, GETDATE(), ?, ?, ?, ?, ?, 'complete')
    """, run_id, tenant_id, HORIZON_WEEKS, start, end, CONFIDENCE, json.dumps(metrics))
    # Detail: every forecast point (all series/levels/departments/grains) in one batch.
    cur.fast_executemany = True
    cur.executemany("""
        INSERT INTO dbo.Forecasts
            (RunId, TenantId, Series, Level, DepartmentTitle, Grain, TargetDate, Horizon, Model, PointForecast, Lower, Upper)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(run_id, tenant_id, s, lvl, dep, grain, td, hz, mdl, pt, lo, up)
          for (s, lvl, dep, grain, td, hz, mdl, pt, lo, up) in rows])
    conn.commit()


# ── Entrypoint (called by run_job) ──────────────────────────────────────────────

def main() -> None:
    logger.info("Forecast models stage starting")
    conn = get_db_connection()
    try:
        tenants = get_tenants(conn)
        logger.info("Found %d tenant(s)", len(tenants))
        total = 0
        for tenant_id, name in tenants:
            try:
                total += forecast_tenant(conn, tenant_id)
            except Exception as exc:
                logger.error("[Tenant %s] forecast failed: %s", tenant_id, exc, exc_info=True)
        logger.info("Forecast models stage complete — %d forecast rows written", total)
    finally:
        conn.close()


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    main()
