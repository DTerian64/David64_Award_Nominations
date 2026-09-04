"""Graph Analytics run metadata and zero-finding snapshot coverage."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from utils import sqlhelper2 as sql


def _runs(history, marker):
    session = MagicMock()
    rows = MagicMock()
    rows.fetchall.return_value = history
    status = MagicMock()
    status.fetchone.return_value = marker
    patterns = MagicMock()
    patterns.fetchall.return_value = [(3, name, i, True, name not in ('HiddenCandidate', 'Desert'))
        for i, name in enumerate(('Ring', 'BipartiteDenseBlock', 'TemporalBurst', 'SuperNominator',
                                 'SuperBeneficiary', 'CopyPaste', 'HiddenCandidate', 'Desert'), 1)]
    session.execute.side_effect = [rows, status, patterns]
    context = MagicMock()
    context.__enter__.return_value = session
    with patch.object(sql, 'get_db_context', return_value=context):
        result = sql.get_integrity_runs(1)
    assert all(call.args[1] == {'tid': 1} for call in session.execute.call_args_list)
    return result


def test_current_zero_snapshot_is_visible_with_all_eight_detectors():
    result = _runs([('old', datetime(2026, 9, 2), 100, 0, 2)],
        ('clean', datetime(2026, 9, 3), json.dumps({'snapshot_schema_version': 2,
            'scoring_policy_version': 3, 'finding_count': 0}), 'AVAILABLE'))
    assert result[0]['runId'] == 'clean'
    assert result[0]['snapshotComplete'] and result[0]['currentSnapshot']
    assert result[0]['totalFindings'] == 0
    assert len(result[0]['detectors']) == 8
    assert sum(p['scoring'] for p in result[0]['detectors']) == 6
    assert not result[1]['snapshotComplete']


def test_existing_complete_run_is_not_duplicated_and_legacy_stays_partial():
    result = _runs([('run', datetime(2026, 9, 3), 20, 1, 3)],
        ('run', datetime(2026, 9, 3), json.dumps({'snapshot_schema_version': 2,
            'scoring_policy_version': 3, 'finding_count': 20}), 'AVAILABLE'))
    assert len(result) == 1 and result[0]['currentSnapshot']
    assert result[0]['snapshotComplete']


def test_unavailable_registry_does_not_advertise_a_clean_snapshot():
    result = _runs([], ('failed', datetime(2026, 9, 3), '{}', 'UNAVAILABLE'))
    assert result == []


def test_old_groups_are_not_complete_archives_even_if_old_flag_says_so():
    result = _runs([('old', datetime(2026, 9, 2), 20, 1, 3)], None)
    assert not result[0]['snapshotComplete']
    assert not result[0]['currentSnapshot']


def test_mismatched_count_cannot_claim_complete_current_snapshot():
    result = _runs([('run', datetime(2026, 9, 3), 2, 0, 3)],
        ('run', datetime(2026, 9, 3), json.dumps({'snapshot_schema_version': 2,
            'scoring_policy_version': 3, 'finding_count': 20}), 'AVAILABLE'))
    assert not result[0]['snapshotComplete']


def test_malformed_metadata_does_not_fabricate_a_current_run():
    for metadata in ('{bad', 'null', '[]'):
        assert _runs([], ('run', datetime(2026, 9, 3), metadata, 'AVAILABLE')) == []
