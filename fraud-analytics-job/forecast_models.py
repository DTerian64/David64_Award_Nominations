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

# Tuning
HORIZON_WEEKS = 8
BACKTEST_FOLDS = 5
SEASON_WEEKS = 52
CONFIDENCE = 0.80
Z = 1.2816                      # ~80% two-sided normal quantile
HISTORY_DAYS = 900             # ~2.5 years
TOP_DEPARTMENTS = 6

US_HOLIDAYS = {
    date(2024,1,1),date(2024,1,15),date(2024,2,19),date(2024,5,27),date(2024,6,19),
    date(2024,7,4),date(2024,9,2),date(2024,11,11),date(2024,11,28),date(2024,11,29),date(2024,12,25),
    date(2025,1,1),date(2025,1,20),date(2025,2,17),date(2025,5,26),date(2025,6,19),
    date(2025,7,4),date(2025,9,1),date(2025,11,11),date(2025,11,27),date(2025,11,28),date(2025,12,25),
    date(2026,1,1),date(2026,1,19),date(2026,2,16),date(2026,5,25),date(2026,6,19),
    date(2026,7,3),date(2026,9,7),date(2026,11,11),date(2026,11,26),date(2026,11,27),date(2026,12,25),
}


# ── DB ──────────────────────────────────────────────────────────────────────────

def get_db_connection():
    return pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USER')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )


def get_tenants(conn) -> list:
    df = pd.read_sql("SELECT TenantId, TenantName FROM dbo.Tenants ORDER BY TenantId", conn)
    return list(df.itertuples(index=False, name=None))


def load_nominations(conn, tenant_id: int) -> pd.DataFrame:
    """Per-record NominationDate, Amount, Title for the tenant's window."""
    q = """
        SELECT n.NominationDate AS ds, n.Amount AS amount, u.Title AS title
        FROM dbo.Nominations n
        JOIN dbo.Users u ON u.UserId = n.BeneficiaryId
        WHERE u.TenantId = ?
          AND n.NominationDate >= DATEADD(DAY, ?, CAST(GETDATE() AS DATE))
    """
    return pd.read_sql(q, conn, params=[tenant_id, -abs(HISTORY_DAYS)])


# ── Aggregation ─────────────────────────────────────────────────────────────────

def daily_frame(ds_series, value_series, start, end) -> pd.DataFrame:
    s = pd.Series(np.asarray(value_series, dtype=float),
                  index=pd.to_datetime(pd.Series(ds_series).dt.date))
    daily = s.groupby(s.index).sum()
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    daily = daily.reindex(idx, fill_value=0.0)
    return pd.DataFrame({"ds": idx, "y": daily.values})


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    g = df.set_index("ds")["y"].resample("W-MON", label="left", closed="left")
    s, counts = g.sum(), g.count()
    if len(counts) and counts.iloc[-1] < 7:
        s = s.iloc[:-1]
    return pd.DataFrame({"ds": s.index, "y": s.values})


# ── Metrics ─────────────────────────────────────────────────────────────────────

def mase(y_true, y_pred, y_train, m):
    y_true, y_pred, y_train = map(lambda a: np.asarray(a, float), (y_true, y_pred, y_train))
    denom = np.mean(np.abs(y_train[m:] - y_train[:-m])) if len(y_train) > m else 0
    if not denom:
        denom = np.mean(np.abs(np.diff(y_train))) or 1e-9
    return float(np.mean(np.abs(y_true - y_pred)) / denom)


def smape(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    d = np.abs(y_true) + np.abs(y_pred); d[d == 0] = 1e-9
    return float(100 * np.mean(2 * np.abs(y_pred - y_true) / d))


def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((np.asarray(y_true, float) - np.asarray(y_pred, float)) ** 2)))


# ── Models ──────────────────────────────────────────────────────────────────────

def seasonal_naive(train, h, m, z=Z):
    train = np.asarray(train, float)
    if len(train) >= m:
        base = train[-m:]
        point = np.array([base[i % m] for i in range(h)]); resid = train[m:] - train[:-m]
    else:
        point = np.full(h, train[-1] if len(train) else 0.0)
        resid = np.diff(train) if len(train) > 1 else np.array([0.0])
    sigma = np.std(resid) if len(resid) else 0.0
    sd = sigma * np.sqrt(np.arange(1, h + 1) / max(m, 1) + 1)
    return np.maximum(point, 0), np.maximum(point - z * sd, 0), point + z * sd


def ets(train, h, m, z=Z):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    train = np.asarray(train, float)
    try:
        if len(train) >= 2 * m:
            fit = ExponentialSmoothing(train, trend="add", seasonal="add",
                                       seasonal_periods=m, initialization_method="estimated").fit()
        else:
            fit = ExponentialSmoothing(train, trend="add",
                                       initialization_method="estimated").fit()
        point = np.asarray(fit.forecast(h), float)
        sigma = np.std(train - fit.fittedvalues)
    except Exception:
        return seasonal_naive(train, h, m, z)
    sd = sigma * np.sqrt(np.arange(1, h + 1))
    return np.maximum(point, 0), np.maximum(point - z * sd, 0), point + z * sd


def _calendar(ds: pd.Timestamp) -> dict:
    return {"month": ds.month, "quarter": ds.quarter,
            "weekofyear": int(ds.isocalendar().week), "dayofyear": ds.dayofyear,
            "is_quarter_end_month": int(ds.month in (3, 6, 9, 12)),
            "is_holiday": int(ds.date() in US_HOLIDAYS)}


def _supervised(df, lags, group_col=None):
    frames = []
    for g in (df[group_col].unique() if group_col else [None]):
        sub = (df[df[group_col] == g] if group_col else df).sort_values("ds").copy()
        for L in lags:
            sub[f"lag{L}"] = sub["y"].shift(L)
        sub["roll4"] = sub["y"].shift(1).rolling(4).mean()
        sub = pd.concat([sub, sub["ds"].apply(_calendar).apply(pd.Series)], axis=1)
        if group_col:
            sub[group_col] = g
        frames.append(sub)
    return pd.concat(frames, ignore_index=True)


def lgbm_forecast(history, h, freq, lags, group_col=None, confidence=CONFIDENCE, seed=42):
    import lightgbm as lgb
    lo_q, hi_q = (1 - confidence) / 2, 1 - (1 - confidence) / 2
    feat = [f"lag{L}" for L in lags] + ["roll4", "month", "quarter", "weekofyear",
            "dayofyear", "is_quarter_end_month", "is_holiday"]
    cats = [group_col] if group_col else []
    if group_col:
        feat = feat + [group_col]
    sup = _supervised(history, lags, group_col).dropna(subset=[f"lag{max(lags)}"])
    if group_col:
        sup[group_col] = sup[group_col].astype("category")
    X, y = sup[feat], sup["y"]
    p = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20,
             subsample=0.9, colsample_bytree=0.9, random_state=seed, verbose=-1)
    mp = lgb.LGBMRegressor(objective="regression", **p).fit(X, y, categorical_feature=cats or "auto")
    ml = lgb.LGBMRegressor(objective="quantile", alpha=lo_q, **p).fit(X, y, categorical_feature=cats or "auto")
    mh = lgb.LGBMRegressor(objective="quantile", alpha=hi_q, **p).fit(X, y, categorical_feature=cats or "auto")
    step = pd.Timedelta(days=1) if freq == "D" else pd.Timedelta(weeks=1)
    out = {}
    for g in (history[group_col].unique() if group_col else [None]):
        hist = (history[history[group_col] == g] if group_col else history).sort_values("ds")
        yh = list(hist["y"].values); last = hist["ds"].max()
        pt, lo, up = [], [], []
        for i in range(h):
            nds = last + step * (i + 1)
            row = {f"lag{L}": (yh[-L] if len(yh) >= L else np.nan) for L in lags}
            row["roll4"] = np.mean(yh[-4:]) if len(yh) >= 4 else np.nan
            row.update(_calendar(nds))
            if group_col:
                row[group_col] = g
            Xr = pd.DataFrame([row])[feat]
            if group_col:
                Xr[group_col] = Xr[group_col].astype("category")
            pv = max(float(mp.predict(Xr)[0]), 0.0)
            pt.append(pv); lo.append(min(max(float(ml.predict(Xr)[0]), 0.0), pv))
            up.append(max(float(mh.predict(Xr)[0]), pv)); yh.append(pv)
        out[g] = (np.array(pt), np.array(lo), np.array(up))
    return out if group_col else out[None]


def rolling_backtest(y, model_fn, folds, h, m):
    y = np.asarray(y, float); n = len(y)
    mae, sm, rm, ma, cov = [], [], [], [], []
    for k in range(folds, 0, -1):
        cut = n - k * h
        if cut <= m:
            continue
        train, test = y[:cut], y[cut:cut + h]
        if len(test) < h:
            continue
        pt, lo, up = model_fn(train, h); pt = pt[:len(test)]
        mae.append(float(np.mean(np.abs(test - pt)))); sm.append(smape(test, pt))
        rm.append(rmse(test, pt)); ma.append(mase(test, pt, train, m))
        cov.append(float(np.mean((test >= lo[:len(test)]) & (test <= up[:len(test)]))))
    agg = lambda a: round(float(np.mean(a)), 4) if a else None
    return {"MASE": agg(ma), "sMAPE": agg(sm), "RMSE": agg(rm),
            "coverage": agg(cov), "folds": len(ma)}


# ── Per-tenant pipeline ─────────────────────────────────────────────────────────

def _model_fns(series_for_lgbm_end):
    """Return weekly model callables; LightGBM wraps a synthetic weekly index."""
    def f_snaive(tr, h): return seasonal_naive(tr, h, SEASON_WEEKS)
    def f_ets(tr, h):    return ets(tr, h, SEASON_WEEKS)
    def f_lgbm(tr, h):
        df = pd.DataFrame({"ds": pd.date_range(end=series_for_lgbm_end, periods=len(tr),
                                               freq="W-MON"), "y": tr})
        return lgbm_forecast(df, h, "W", lags=[1, 2, 4, 8, 52])
    return {"SeasonalNaive": f_snaive, "ETS": f_ets, "LightGBM": f_lgbm}


def _bakeoff(weekly: pd.DataFrame):
    """Backtest the three models; return (metrics_dict, chosen_name, final_forecast)."""
    y = weekly["y"].values
    end = weekly["ds"].max()
    fns = _model_fns(end)
    metrics = {name: rolling_backtest(y, fn, BACKTEST_FOLDS, HORIZON_WEEKS, SEASON_WEEKS)
               for name, fn in fns.items()}
    ranked = [n for n in metrics if metrics[n]["MASE"] is not None]
    chosen = min(ranked, key=lambda n: metrics[n]["MASE"]) if ranked else "ETS"
    metrics["chosen"] = chosen
    final = fns[chosen](y, HORIZON_WEEKS)
    return metrics, chosen, final


def forecast_tenant(conn, tenant_id: int) -> int:
    """Run the bake-off for one tenant and persist a run. Returns rows written."""
    df = load_nominations(conn, tenant_id)
    if df.empty or len(df) < 30:
        logger.info("[Tenant %s] too few nominations (%d) — skipping", tenant_id, len(df))
        return 0
    df["ds"] = pd.to_datetime(df["ds"])
    start = df["ds"].min().date()
    end = date.today()

    daily_cnt = daily_frame(df["ds"], np.ones(len(df)), start, end)
    daily_spd = daily_frame(df["ds"], df["amount"].fillna(0), start, end)
    wk_cnt, wk_spd = to_weekly(daily_cnt), to_weekly(daily_spd)

    run_id = str(uuid.uuid4())
    rows = []   # (Series, Level, Dept, Grain, TargetDate, Horizon, Model, point, lo, up)

    def add_weekly(series_name, weekly_df, point, lo, up, model, level="total", dept=None):
        last = weekly_df["ds"].max()
        for i in range(len(point)):
            td = (last + pd.Timedelta(weeks=i + 1)).date()
            rows.append((series_name, level, dept, "weekly", td, i + 1, model,
                         float(point[i]), float(lo[i]), float(up[i])))

    # total weekly nominations + spend
    m_cnt, chosen_cnt, (p, lo, up) = _bakeoff(wk_cnt)
    add_weekly("nominations", wk_cnt, p, lo, up, chosen_cnt)
    m_spd, chosen_spd, (p, lo, up) = _bakeoff(wk_spd)
    add_weekly("spend", wk_spd, p, lo, up, chosen_spd)

    # total daily nominations via LightGBM (production granularity)
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
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dbo.ForecastRuns
            (RunId, TenantId, GeneratedAt, HorizonWeeks, HistoryStart, HistoryEnd, Confidence, Metrics, Status)
        VALUES (?, ?, GETDATE(), ?, ?, ?, ?, ?, 'complete')
    """, run_id, tenant_id, HORIZON_WEEKS, start, end, CONFIDENCE, json.dumps(metrics))
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
