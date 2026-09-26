from datetime import datetime, timedelta, timezone
import math
import pytest
from integrity_engine.tabular_history import TabularHistory


def row(identifier, when, nom=1, ben=2, amount=100):
    return {"NominationId": identifier, "NominationDate": when,
            "NominatorId": nom, "BeneficiaryId": ben, "Amount": amount,
            "NominationDescription": f"Description {identifier}"}


def test_window_lower_boundary_inclusive_and_current_target_excluded():
    target_time = datetime(2026, 9, 26)
    history = TabularHistory(180)
    history.add(row(1, target_time - timedelta(days=181)))
    history.add(row(2, target_time - timedelta(days=180), amount=200))
    history.add(row(3, target_time - timedelta(days=1), nom=2, ben=1))
    features = history.features(row(4, target_time))
    assert features["PairNominationCount"] == 1
    assert features["HasReciprocalNomination"] == 1
    assert features["NominatorTotalNominations"] == 1
    assert features["NominatorAvgAmount"] == 200
    assert features["BeneficiaryTotalReceived"] == 1
    assert features["AmountZScore"] == -1
    assert len(history.rows) == 2


def test_equal_timestamp_ordering_cannot_include_self_or_future():
    time = datetime(2026, 9, 26, tzinfo=timezone.utc)
    history = TabularHistory(365)
    history.add(row(1, time))
    assert history.features(row(2, time))["PairNominationCount"] == 1
    with pytest.raises(ValueError, match="Target must follow"):
        history.features(row(1, time))
    with pytest.raises(ValueError, match="ordered"):
        history.add(row(0, time))


def test_latest_twenty_authored_descriptions_and_population_standard_deviation():
    time = datetime(2026, 9, 1)
    history = TabularHistory(180)
    for identifier in range(25):
        history.add(row(identifier, time + timedelta(days=identifier), nom=2, ben=3,
                        amount=100 if identifier % 2 else 200))
    features = history.features(row(26, time + timedelta(days=25), nom=2, ben=1))
    assert features["NominatorTotalNominations"] == 25
    assert features["NominatorStdAmount"] == pytest.approx(math.sqrt(2496))
    assert [r["NominationId"] for r in history.prior_descriptions(2)] == list(range(5, 25))
    assert not history.prior_descriptions(3)  # Receiving != authoring.
    history.features(row(27, time + timedelta(days=365)))
    assert not history.prior_descriptions(2)
    assert not history.rows


@pytest.mark.parametrize("days", [0, -1, True, 1.5])
def test_invalid_windows_are_rejected(days):
    with pytest.raises(ValueError):
        TabularHistory(days)
