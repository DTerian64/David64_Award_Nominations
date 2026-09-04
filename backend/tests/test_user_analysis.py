"""User Analysis API, tenant boundary, and query contract regression tests.

Run from backend: python -m pytest tests/test_user_analysis.py -v
Database calls are mocked; no Azure access or production writes are required.
SQL Server execution must additionally be smoke-tested in sandbox after deploy.
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault('CLIENT_ID', 'unit-test-client')

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import require_analytics_access
from routers.model_analysis_router import router
from utils import sqlhelper2, user_analysis


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_analytics_access] = lambda: {
        'actual_user': {'TenantId': 1},
        'effective_user': {'TenantId': 7, 'UserId': 92},
    }
    return TestClient(app)


def test_user_search_uses_effective_tenant(client):
    with patch.object(user_analysis, 'search_users', return_value={'items': []}) as search:
        response = client.get('/api/model-analysis/users?q=Ada&page=2&page_size=10')
    assert response.status_code == 200
    search.assert_called_once_with(7, 'Ada', 2, 10)


def test_analysis_passes_all_filters_and_tenant(client):
    with patch.object(user_analysis, 'get_user_analysis', return_value={'items': []}) as lookup:
        response = client.get('/api/model-analysis/users/42/nominations', params={
            'role': 'nominee', 'engine': 'semantic', 'risk': 'NONE',
            'outcome': 'CONFIRMED_SEMANTIC_CONCERN', 'start_date': '2026-08-01',
            'end_date': '2026-08-31', 'page': 2, 'page_size': 10,
        })
    assert response.status_code == 200
    args = lookup.call_args.kwargs
    assert args['tenant_id'] == 7 and args['user_id'] == 42
    assert args['role'] == 'nominee' and args['engine'] == 'semantic'
    assert args['risk'] == 'NONE' and args['outcome'] == 'CONFIRMED_SEMANTIC_CONCERN'
    assert str(args['start_date']) == '2026-08-01'
    assert str(args['end_date']) == '2026-08-31'
    assert args['page'] == 2 and args['page_size'] == 10


@pytest.mark.parametrize('params', [
    {'role': 'manager'}, {'engine': "rf; DROP TABLE dbo.Users"},
    {'risk': 'BAN'}, {'outcome': 'Rejected'}, {'page': 0}, {'page_size': 101},
    {'start_date': '2026-09-02', 'end_date': '2026-09-01'},
])
def test_invalid_analysis_filters_do_not_reach_database(client, params):
    with patch.object(user_analysis, 'get_user_analysis') as lookup:
        response = client.get('/api/model-analysis/users/42/nominations', params=params)
    assert response.status_code == 422
    lookup.assert_not_called()


def test_cross_tenant_or_missing_user_returns_404(client):
    with patch.object(user_analysis, 'get_user_analysis', return_value=None):
        assert client.get('/api/model-analysis/users/42/nominations').status_code == 404


@pytest.mark.parametrize('path', ['/users?q=Ada', '/users/42/nominations'])
def test_both_routes_require_analytics_authorization(path):
    route = next(r for r in router.routes if r.path == '/api/model-analysis' + path.split('?')[0].replace('42', '{user_id}'))
    assert require_analytics_access in [dependency.call for dependency in route.dependant.dependencies]


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalar_one(self):
        return self.value

    def first(self):
        return self.value

    def one(self):
        return self.value

    def all(self):
        return self.value


class Session:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def execute(self, query, params):
        self.calls.append((str(query), dict(params)))
        return Result(next(self.results))


@contextmanager
def database(session):
    with patch.object(user_analysis, 'get_db_context') as context:
        context.return_value.__enter__.return_value = session
        yield


def test_user_search_is_literal_paginated_and_tenant_scoped():
    session = Session([1, [{'user_id': 4, 'name': 'Ada', 'email': 'a@example.com'}]])
    with database(session):
        result = user_analysis.search_users(7, 'a_%[', page=3, page_size=10)
    assert result['total'] == 1 and result['page'] == 3
    for sql, params in session.calls:
        assert 'TenantId = :tid' in sql
        assert params['tid'] == 7 and params['offset'] == 20
        assert params['search'] == '%a[_][%][[]%'
        assert 'a_%[' not in sql
    assert 'FETCH NEXT :page_size ROWS ONLY' in session.calls[1][0]


def test_unknown_user_stops_before_nomination_query():
    session = Session([None])
    with database(session):
        assert user_analysis.get_user_analysis(7, 999) is None
    assert len(session.calls) == 1
    assert 'UserId = :uid AND TenantId = :tid' in session.calls[0][0]


def test_analysis_summary_and_pagination_share_exact_filter_cte():
    summary = {'total': 40, 'nominations_made': 12, 'nominations_received': 28,
               'engine_concerns': 2, 'confirmed_issues': 0,
               'cleared_concerns': 1, 'unsubstantiated': 1,
               'not_for_training': 1, 'missing_evidence': 5}
    row = {'nomination_id': 55}
    for engine in user_analysis.ENGINE_COLUMNS:
        row[engine] = json.dumps({'available': engine != 'gnn', 'risk_level': 'NONE',
                                  'findings': ['Historical signal'] if engine == 'rf' else []})
        row[engine + '_concern'] = int(engine == 'rf')
    session = Session([{'user_id': 42}, summary, [row]])
    with database(session):
        result = user_analysis.get_user_analysis(7, 42, engine='rf', role='either', page=2)
    assert result['total'] == 40 and len(result['items']) == 1
    assert result['summary']['nominations_made'] == 12
    assert result['summary']['nominations_received'] == 28
    assert result['items'][0]['engines']['rf']['concern'] is True
    assert result['items'][0]['engines']['gnn']['available'] is False
    summary_sql, params = session.calls[1]
    rows_sql = session.calls[2][0]
    assert summary_sql.split('SELECT COUNT(*)')[0] == rows_sql.split('SELECT * FROM filtered')[0]
    assert 'nom.TenantId = :tid' in rows_sql and 'ben.TenantId = :tid' in rows_sql
    assert 'idr.TenantId = :tid' in rows_sql
    assert 'AND rf_concern = 1' in rows_sql
    assert 'DATEADD(DAY, 1, :end_date)' in rows_sql
    assert 'ORDER BY nomination_date DESC, nomination_id DESC' in rows_sql
    assert params['offset'] == 25 and params['tid'] == 7
    # No rejected-status-to-label inference; only explicit HRBP dispositions.
    assert "review_outcome IN ('CONFIRMED_CONCERN', 'CONFIRMED_SEMANTIC_CONCERN')" in summary_sql
    assert "training_disposition = 'EXCLUDED'" in summary_sql
    assert "status = 'Rejected'" not in summary_sql
    assert "user_role IN ('nominator', 'both')" in summary_sql
    assert "user_role IN ('nominee', 'both')" in summary_sql


def test_concern_expression_requires_availability_and_preserves_low_score_findings():
    for engine, column in user_analysis.ENGINE_COLUMNS.items():
        expression = user_analysis._concern_expression(engine, column)
        assert "'$.available') = 'true'" in expression
        assert "OPENJSON(idr." + column + ", '$.findings')" in expression
        if engine == 'semantic':
            assert "IN ('flag', 'reject')" in expression
        else:
            assert "IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')" in expression


def test_helper_rejects_untrusted_sql_column_selection():
    with pytest.raises(ValueError):
        user_analysis.get_user_analysis(7, 42, engine='rf_concern;--')


def test_historical_graph_findings_are_compacted_to_winner_and_count():
    original = {
        'available': True,
        'findings': [
            '[Graph] nominator: Ring (88.20, HIGH)',
            '[Graph] nominator: Ring (84.29, HIGH)',
            '[Graph] beneficiary: CopyPaste (73.00, MEDIUM)',
        ],
        'winning_pattern_type': 'Ring',
    }
    result = sqlhelper2.compact_graph_result(original)
    assert result['findings'] == ['[Graph] nominator: Ring (88.20, HIGH)']
    assert result['winning_pattern_type'] == 'Ring'
    assert result['winning_pattern_count'] == 2
    assert len(original['findings']) == 3  # Raw persisted evidence is untouched.


def test_structured_graph_evidence_counts_only_relevant_winning_findings():
    result = sqlhelper2.compact_graph_result({
        'available': True,
        'findings': [],
        'winning_finding_hash': 'winner',
        'pattern_findings': [
            {'finding_hash': 'winner', 'pattern_type': 'Ring', 'finding_score': 88.2,
             'affected_roles': ['nominator'], 'routing_relevant': True},
            {'finding_hash': 'ring-2', 'pattern_type': 'Ring', 'finding_score': 85,
             'routing_relevant': True},
            {'finding_hash': 'ring-3', 'pattern_type': 'Ring', 'finding_score': 99,
             'routing_relevant': False},
        ],
    })
    assert result['winning_pattern_type'] == 'Ring'
    assert result['winning_pattern_count'] == 2
    assert result['findings'] == ['[Graph] nominator: Ring (88.20)']
