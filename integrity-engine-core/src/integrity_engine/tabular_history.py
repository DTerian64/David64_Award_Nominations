"""Shared causal, bounded history for tabular training and live scoring.

Feed rows in (NominationDate, NominationId) order. Read features before adding
the target. The lower time boundary is inclusive; amounts use population std.
This module has no model, database, or pandas dependencies.
"""
from collections import Counter, defaultdict, deque
from datetime import timedelta, timezone
import math

HISTORY_CONTRACT = "tabular-causal-window-v1"


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class _Moments:
    def __init__(self):
        self.count = 0
        self.total = self.squares = 0.0

    def update(self, amount, direction):
        if amount is None or not math.isfinite(float(amount)):
            return
        amount = float(amount)
        self.count += direction
        self.total += direction * amount
        self.squares += direction * amount * amount

    @property
    def mean(self):
        return self.total / self.count if self.count else 0.0

    @property
    def std(self):
        return math.sqrt(max(0.0, self.squares / self.count - self.mean ** 2)) if self.count > 1 else 0.0


class TabularHistory:
    def __init__(self, window_days):
        if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
            raise ValueError("Tabular history window must be a positive integer")
        self.window_days = window_days
        self.rows = deque()
        self.outgoing = Counter()
        self.incoming = Counter()
        self.pairs = Counter()
        self.given = defaultdict(_Moments)
        self.received = defaultdict(_Moments)
        self.beneficiaries = defaultdict(Counter)
        self.descriptions = defaultdict(deque)
        self.amounts = _Moments()
        self.last_key = None

    def _update(self, row, direction):
        nom, ben = row["NominatorId"], row["BeneficiaryId"]
        self.outgoing[nom] += direction
        self.incoming[ben] += direction
        self.pairs[nom, ben] += direction
        self.beneficiaries[nom][ben] += direction
        if not self.beneficiaries[nom][ben]:
            del self.beneficiaries[nom][ben]
        self.given[nom].update(row.get("Amount"), direction)
        self.received[ben].update(row.get("Amount"), direction)
        self.amounts.update(row.get("Amount"), direction)

    def expire(self, target_time):
        cutoff = utc_naive(target_time) - timedelta(days=self.window_days)
        while self.rows and self.rows[0]["NominationDate"] < cutoff:
            row = self.rows.popleft()
            self._update(row, -1)
            if row.get("NominationDescription", ""):
                self.descriptions[row["NominatorId"]].popleft()

    def add(self, row):
        row = dict(row)
        row["NominationDate"] = utc_naive(row["NominationDate"])
        key = row["NominationDate"], int(row["NominationId"])
        if self.last_key is not None and key <= self.last_key:
            raise ValueError("Tabular history rows must be strictly chronologically ordered")
        self.last_key = key
        self.rows.append(row)
        self._update(row, 1)
        if row.get("NominationDescription", ""):
            self.descriptions[row["NominatorId"]].append(row)

    def prior_descriptions(self, author_id):
        # Match the live semantic feature: the beneficiary's own prior authored
        # descriptions, latest 20, not descriptions of future awards received.
        history = self.descriptions[author_id]
        return [history[i] for i in range(max(0, len(history) - 20), len(history))]

    def features(self, target):
        time = utc_naive(target["NominationDate"])
        key = time, int(target["NominationId"])
        if self.last_key is not None and key <= self.last_key:
            raise ValueError("Target must follow all tabular history rows")
        self.expire(time)
        nom, ben = target["NominatorId"], target["BeneficiaryId"]
        amount = float(target["Amount"])
        zscore = (amount - self.amounts.mean) / self.amounts.std if self.amounts.std else 0.0
        day, month = time.weekday(), time.month
        unique = len(self.beneficiaries[nom])
        return {
            "Amount": amount, "DayOfWeek": day, "Month": month,
            "DayOfWeekSin": math.sin(2 * math.pi * day / 7),
            "DayOfWeekCos": math.cos(2 * math.pi * day / 7),
            "MonthSin": math.sin(2 * math.pi * (month - 1) / 12),
            "MonthCos": math.cos(2 * math.pi * (month - 1) / 12),
            "IsWeekend": int(day >= 5),
            "NominatorTotalNominations": self.outgoing[nom],
            "NominatorAvgAmount": self.given[nom].mean,
            "NominatorStdAmount": self.given[nom].std,
            "NominatorUniqueBeneficiaries": unique,
            "BeneficiaryTotalReceived": self.incoming[ben],
            "BeneficiaryAvgAmountReceived": self.received[ben].mean,
            "HasReciprocalNomination": int(self.pairs[ben, nom] > 0),
            "PairNominationCount": self.pairs[nom, ben],
            "AmountZScore": zscore, "IsHighAmount": int(zscore > 2),
            "NominatorConcentrationRatio": self.outgoing[nom] / (unique + 1),
        }


def semantic_features(vector, prior_vectors):
    if not prior_vectors:
        return 0.0, 1.0
    mean = [sum(values) / len(prior_vectors) for values in zip(*prior_vectors)]
    dot = sum(a * b for a, b in zip(vector, mean))
    norm = math.sqrt(sum(a * a for a in vector) * sum(b * b for b in mean))
    return (dot / norm if norm else 0.0), math.sqrt(sum((a - b) ** 2 for a, b in zip(vector, mean)))
