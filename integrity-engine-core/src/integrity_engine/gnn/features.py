"""Graph-native feature construction shared by fitting and live encoding."""
from collections import Counter, defaultdict
from datetime import date
import math
import numpy as np

from .causal_context import CAUSAL_CONTEXT_FEATURE_COLUMNS, causal_context_matrix, _timestamp

USER_FEATURE_COLUMNS = ["LogNominationsMade", "LogNominationsReceived", "LogUniqueCounterparties"]
BASE_NOMINATION_FEATURE_COLUMNS = [
    "LogAmount", "CategoryRelativeAmountRobustZScore", "DaysBeforeGraphCutoff",
    "DayOfWeekSin", "DayOfWeekCos", "MonthSin", "MonthCos", "HistoricalStatus",
]
NOMINATION_FEATURE_COLUMNS = [*BASE_NOMINATION_FEATURE_COLUMNS, *CAUSAL_CONTEXT_FEATURE_COLUMNS]


def build_user_features(user_ids, graph_rows):
    made, received, partners = Counter(), Counter(), defaultdict(set)
    for row in graph_rows:
        a, b = int(row["NominatorId"]), int(row["BeneficiaryId"])
        made[a] += 1
        received[b] += 1
        partners[a].add(b)
        partners[b].add(a)
    return np.asarray([
        [math.log1p(made[u]), math.log1p(received[u]), math.log1p(len(partners[u]))]
        for u in user_ids
    ], dtype=np.float32).reshape(len(user_ids), len(USER_FEATURE_COLUMNS))


def build_nomination_features(rows, category_amount_stats, graph_cutoff: date, *,
                              historical, context_rows=None, causal_window_days=365):
    base = np.zeros((len(rows), len(BASE_NOMINATION_FEATURE_COLUMNS)), dtype=np.float32)
    statuses = {"Pending": 0.0, "Approved": 1.0, "Paid": 2.0, "Rejected": 3.0}
    categories = category_amount_stats.get("categories", {})
    fallback = category_amount_stats.get("global", {"median": 0.0, "scale": 1.0})
    for i, row in enumerate(rows):
        day = _timestamp(row["CreatedAt"]).date()
        amount = float(row.get("Amount") or 0.0)
        stats = categories.get(str(int(row.get("CategoryId") or 0)), fallback)
        weekday = 2 * math.pi * day.weekday() / 7
        month = 2 * math.pi * (day.month - 1) / 12
        base[i] = (
            math.log1p(max(amount, 0)),
            (amount - float(stats["median"])) / max(float(stats["scale"]), 1.0),
            max((graph_cutoff - day).days, 0) if historical else 0,
            math.sin(weekday), math.cos(weekday), math.sin(month), math.cos(month),
            statuses.get(str(row.get("Status")), 0) if historical else 0,
        )
    causal = np.asarray(causal_context_matrix(
        rows if context_rows is None else context_rows, rows, window_days=causal_window_days,
    ), dtype=np.float32).reshape(len(rows), len(CAUSAL_CONTEXT_FEATURE_COLUMNS))
    return np.concatenate((base, causal), axis=1)
