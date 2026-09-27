from datetime import datetime, timedelta, timezone
import numpy as np
import pytest
import torch

from integrity_engine.gnn.encoder import HeteroEncoder
from integrity_engine.gnn.features import USER_FEATURE_COLUMNS, NOMINATION_FEATURE_COLUMNS
from integrity_engine.gnn.live_graph import build_live_graph_inputs, LiveGraphReplay, target_causal_history


def fixture():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    users = [{"UserId": i, "TenantId": 5} for i in range(1, 5)]
    rows = [dict(NominationId=i + 1, NominatorId=a, BeneficiaryId=b, Amount=100,
                 CategoryId=1, Status="Paid", CreatedAt=now - timedelta(days=age))
            for i, (a, b, age) in enumerate([(2, 3, 10), (3, 4, 9), (4, 1, 8), (1, 2, 90)])]
    target = dict(NominationId=100, NominatorId=1, BeneficiaryId=2, CreatedAt=now)
    prep = {"user_feature_columns": USER_FEATURE_COLUMNS,
            "nomination_feature_columns": NOMINATION_FEATURE_COLUMNS,
            "user_scaler_mean": [0.] * 3, "user_scaler_std": [1.] * 3,
            "nomination_scaler_mean": [0.] * len(NOMINATION_FEATURE_COLUMNS),
            "nomination_scaler_std": [1.] * len(NOMINATION_FEATURE_COLUMNS),
            "category_amount_stats": {"global": {"median": 100., "scale": 100.}},
            "causal_context_window_days": 60}
    return users, rows, target, prep


def test_live_graph_excludes_target_future_ineligible_and_expired():
    users, rows, target, prep = fixture()
    rows += [{**rows[0], **target, "Status": "Pending"},
             {**rows[0], "NominationId": 101, "CreatedAt": target["CreatedAt"]},
             {**rows[0], "NominationId": 90, "IsBehaviorEligible": False}]
    graph = build_live_graph_inputs(users, rows, target, prep)
    assert graph.nomination_ids == [1, 2, 3]
    assert graph.x_dict["user"].shape == (4, 3)
    assert set(graph.edge_index_dict) == {
        ("user", "nominates", "nomination"), ("nomination", "benefits", "user"),
        ("nomination", "rev_nominates", "user"), ("user", "rev_benefits", "nomination"),
        ("nomination", "belongs_to", "category"), ("category", "rev_belongs_to", "nomination"),
    }


def test_same_timestamp_earlier_id_is_history_and_boundary_is_inclusive():
    users, rows, target, prep = fixture()
    rows += [{**rows[0], "NominationId": 99, "CreatedAt": target["CreatedAt"]},
             {**rows[0], "NominationId": 98, "CreatedAt": target["CreatedAt"] - timedelta(days=60)}]
    assert set(build_live_graph_inputs(users, rows, target, prep).nomination_ids) == {1, 2, 3, 98, 99}


def test_replay_is_identical_to_direct_live_builder_including_expiry():
    users, rows, target, prep = fixture()
    replay = LiveGraphReplay(users, rows, prep)
    for age in (20, 0, -65):
        item = {**target, "CreatedAt": target["CreatedAt"] - timedelta(days=age)}
        direct, cached = build_live_graph_inputs(users, rows, item, prep), replay.build(item)
        assert direct.nomination_ids == cached.nomination_ids
        for key in direct.x_dict:
            torch.testing.assert_close(direct.x_dict[key], cached.x_dict[key])
        for key in direct.edge_index_dict:
            assert torch.equal(direct.edge_index_dict[key], cached.edge_index_dict[key])


@pytest.mark.parametrize("architecture", ["graphsage", "gcn", "gatv2"])
def test_new_precursor_changes_live_embeddings_without_retraining(architecture):
    users, rows, target, prep = fixture()
    torch.manual_seed(42)
    encoder = HeteroEncoder(hidden_dim=8, out_dim=8, architecture=architecture).eval()
    old = build_live_graph_inputs(users, rows[:2], target, prep)
    new = build_live_graph_inputs(users, rows, target, prep)
    with torch.no_grad():
        before = encoder(old.x_dict, old.edge_index_dict)["user"].clone()
        weights = {key: value.clone() for key, value in encoder.state_dict().items()}
        after = encoder(new.x_dict, new.edge_index_dict)["user"]
    assert not torch.allclose(before[0], after[0])
    assert all(torch.equal(value, encoder.state_dict()[key]) for key, value in weights.items())


def test_tenant_bleed_and_scaler_drift_fail_loudly():
    users, rows, target, prep = fixture()
    with pytest.raises(ValueError, match="spans tenants"):
        build_live_graph_inputs([*users, {"UserId": 99, "TenantId": 6}], rows, target, prep)
    with pytest.raises(ValueError, match="outside its tenant"):
        build_live_graph_inputs(users, [{**rows[0], "BeneficiaryId": 99}], target, prep)
    with pytest.raises(ValueError, match="scaler dimensions"):
        build_live_graph_inputs(users, rows, target, {**prep, "user_scaler_mean": [0.]})


@pytest.mark.parametrize("architecture", ["graphsage", "gcn", "gatv2"])
def test_encoder_handles_empty_history_and_new_users(architecture):
    users, _rows, target, prep = fixture()
    inputs = build_live_graph_inputs(users, [], target, prep)
    encoder = HeteroEncoder(hidden_dim=8, out_dim=8, architecture=architecture).eval()
    with torch.no_grad():
        output = encoder(inputs.x_dict, inputs.edge_index_dict)["user"]
    assert output.shape == (4, 8)
    assert torch.isfinite(output).all()


@pytest.mark.parametrize("architecture", ["graphsage", "gcn", "gatv2"])
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_exact_dependency_neighborhood_matches_full_tenant_encoder(architecture, depth):
    users, rows, target, prep = fixture()
    users += [{"UserId": 5, "TenantId": 5}, {"UserId": 6, "TenantId": 5}]
    rows += [{**rows[0], "NominationId": 50, "NominatorId": 5, "BeneficiaryId": 3},
             {**rows[0], "NominationId": 51, "NominatorId": 6, "BeneficiaryId": 5}]
    full = build_live_graph_inputs(users, rows, target, prep)
    local = build_live_graph_inputs(users, rows, target, prep, num_layers=depth)
    replay = LiveGraphReplay(users, rows, prep, num_layers=depth).build(target)
    for key in local.x_dict:
        torch.testing.assert_close(local.x_dict[key], replay.x_dict[key])
    torch.manual_seed(7)
    encoder = HeteroEncoder(hidden_dim=8, out_dim=8, architecture=architecture, num_layers=depth).eval()
    with torch.no_grad():
        expected = encoder(full.x_dict, full.edge_index_dict)["user"]
        actual = encoder(local.x_dict, local.edge_index_dict)["user"]
    for endpoint in (1, 2):
        torch.testing.assert_close(expected[full.user_index[endpoint]], actual[local.user_index[endpoint]])
    if depth == 2:
        assert len(local.nomination_ids) < len(full.nomination_ids)


def test_target_feature_history_filter_is_exact_including_middle_return_edges():
    from integrity_engine.gnn import causal_context_values
    import random
    users, rows, target, prep = fixture()
    rng = random.Random(7)
    rows += [{**rows[0], "NominationId": i + 200, "NominatorId": rng.randrange(1, 11),
              "BeneficiaryId": rng.randrange(1, 11),
              "CreatedAt": target["CreatedAt"] - timedelta(minutes=rng.randrange(1, 120000))}
             for i in range(300)]
    for a, b in ((1, 2), (3, 7), (9, 4)):
        item = {**target, "NominatorId": a, "BeneficiaryId": b}
        complete = causal_context_values(rows, item, window_days=60)
        selected = causal_context_values(target_causal_history(rows, item, 60), item, window_days=60)
        assert complete == selected
