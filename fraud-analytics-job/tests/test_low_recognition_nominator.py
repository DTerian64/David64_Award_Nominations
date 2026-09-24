"""Analytics-only participation findings must not become fraud routing evidence."""

from modeling import graph_analytics as graph


def test_low_recognition_nominator_has_counts_and_no_routing():
    policy = {
        "version": 9,
        "detection_window_days": 365,
        "thresholds": {"low": 25, "medium": 50, "high": 75, "critical": 90},
        "patterns": {
            "LowRecognitionNominator": {
                "enabled": True,
                "enabled_for_routing": False,
                "applicable_roles": ["nominator"],
                "base_score": 10,
                "minimum_score": 0,
                "maximum_score": 100,
                "parameters": {
                    "minimum_nominations_made": 8,
                    "minimum_distinct_beneficiaries": 4,
                    "maximum_nominations_received": 1,
                    "nominations_reference": 20,
                    "beneficiaries_reference": 10,
                    "activity_weight": 10,
                    "breadth_weight": 10,
                },
            },
        },
    }
    nominations = [
        {"NominationId": index, "NominatorId": 1, "BeneficiaryId": index + 10}
        for index in range(1, 9)
    ]
    nominations.append(
        {"NominationId": 9, "NominatorId": 2, "BeneficiaryId": 1}
    )

    users = [
        {"UserId": user_id, "ManagerId": 99}
        for user_id in (1, 2, *range(11, 19))
    ]
    findings = graph.detect_low_recognition_nominators(
        nominations, users, tenant_id=5, run_id="test", policy=policy
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding["AffectedUsers"] == "[1]"
    assert finding["EnabledForRouting"] is False
    assert finding["ApplicableRoles"] == ["nominator"]
    assert "made 8 nominations" in finding["Detail"]
    assert "received 1" in finding["Detail"]
    assert "not a fraud determination" in finding["Detail"]

    users[0]["ManagerId"] = None
    assert graph.detect_low_recognition_nominators(
        nominations, users, tenant_id=5, run_id="test", policy=policy
    ) == []
