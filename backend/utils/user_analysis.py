"""Read-only, tenant-scoped integrity research across a user's nominations.

Counts describe nominations involving a user, not wrongdoing by that user.
Engine signals and human review outcomes remain separate; this module never
creates training labels or changes nomination workflow state.
"""

import json

from sqlalchemy import text

from utils.sqlhelper2 import compact_graph_result, get_db_context


def search_users(tenant_id, query, page=1, page_size=25):
    query = query.strip().lstrip('#')
    # Treat SQL LIKE metacharacters as literal search input.
    search = query.replace('[', '[[]').replace('%', '[%]').replace('_', '[_]')
    params = {'tid': tenant_id, 'q': query, 'search': f'%{search}%',
              'offset': (page - 1) * page_size, 'page_size': page_size}
    where = """
        FROM dbo.Users
        WHERE TenantId = :tid AND :q <> '' AND (
            CONVERT(NVARCHAR(20), UserId) = :q
            OR FirstName + ' ' + LastName LIKE :search
            OR userEmail LIKE :search)
    """
    with get_db_context() as session:
        total = session.execute(text('SELECT COUNT(*) ' + where), params).scalar_one()
        rows = session.execute(text("""
            SELECT UserId AS user_id, FirstName + ' ' + LastName AS name,
                   userEmail AS email
        """ + where + """
            ORDER BY LastName, FirstName, UserId
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """), params).mappings().all()
    return {'items': [dict(row) for row in rows], 'total': total,
            'page': page, 'page_size': page_size}


ENGINE_COLUMNS = {
    'rf': 'RfResultJson', 'graph': 'GraphResultJson',
    'gnn': 'GnnResultJson', 'semantic': 'SemanticResultJson',
}


def _concern_expression(engine, column):
    document = f'idr.{column}'
    # Findings can exist even below the routing threshold. Unavailable engines
    # must never be classified as clean or as an engine concern.
    trigger = (
        f"JSON_VALUE({document}, '$.combined_decision.action') IN ('flag', 'reject')"
        if engine == 'semantic' else
        f"JSON_VALUE({document}, '$.risk_level') IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"
    )
    return f"""CASE WHEN JSON_VALUE({document}, '$.available') = 'true'
        AND ({trigger} OR EXISTS (
            SELECT 1 FROM OPENJSON({document}, '$.findings') finding
            WHERE finding.type = 1 AND LTRIM(RTRIM(finding.value)) <> ''
        )) THEN 1 ELSE 0 END AS {engine}_concern"""


def get_user_analysis(tenant_id, user_id, role='either', engine=None,
                      risk=None, outcome=None, start_date=None, end_date=None,
                      page=1, page_size=25):
    """Paginate nomination evidence and aggregate over the full filtered set.

    Engine filter means that engine raised a concern. Risk is composite risk.
    Summary categories overlap (e.g. a semantic confirmation is excluded from
    training) and are never inferred from a nomination's Rejected status.
    """
    if role not in {'either', 'nominator', 'nominee'}:
        raise ValueError('Unknown user role filter')
    if engine is not None and engine not in ENGINE_COLUMNS:
        raise ValueError('Unknown engine')
    params = {'tid': tenant_id, 'uid': user_id, 'role': role, 'risk': risk,
              'outcome': outcome, 'start_date': start_date, 'end_date': end_date,
              'offset': (page - 1) * page_size, 'page_size': page_size}
    concerns = ', '.join(_concern_expression(k, v) for k, v in ENGINE_COLUMNS.items())
    cte = f"""
        WITH evidence AS (
            SELECT n.NominationId AS nomination_id, n.NominationDate AS nomination_date,
                n.Status AS status, n.Amount AS amount, n.Currency AS currency,
                nom.FirstName + ' ' + nom.LastName AS nominator_name,
                ben.FirstName + ' ' + ben.LastName AS beneficiary_name,
                CASE WHEN n.NominatorId = :uid AND n.BeneficiaryId = :uid THEN 'both'
                     WHEN n.NominatorId = :uid THEN 'nominator' ELSE 'nominee' END AS user_role,
                COALESCE(idr.CompositeRiskLevel, 'UNKNOWN') AS risk_level,
                idr.CompositeScore AS composite_score, idr.FinalRoute AS final_route,
                idr.HumanReviewOutcome AS review_outcome,
                idr.TrainingDisposition AS training_disposition,
                idr.ReviewReason AS review_reason, idr.ReviewedAt AS reviewed_at,
                CASE WHEN idr.NominationId IS NOT NULL THEN 1 ELSE 0 END AS has_evidence,
                idr.RfResultJson AS rf, idr.GraphResultJson AS graph,
                idr.GnnResultJson AS gnn, idr.SemanticResultJson AS semantic,
                {concerns}
            FROM dbo.Nominations n
            JOIN dbo.Users nom ON nom.UserId = n.NominatorId AND nom.TenantId = :tid
            JOIN dbo.Users ben ON ben.UserId = n.BeneficiaryId AND ben.TenantId = :tid
            LEFT JOIN dbo.IntegrityDecisionResults idr
                ON idr.NominationId = n.NominationId AND idr.TenantId = :tid
            WHERE ((:role IN ('either', 'nominator') AND n.NominatorId = :uid)
                OR (:role IN ('either', 'nominee') AND n.BeneficiaryId = :uid))
              AND (:start_date IS NULL OR n.NominationDate >= :start_date)
              AND (:end_date IS NULL OR n.NominationDate < DATEADD(DAY, 1, :end_date))
        ), filtered AS (
            SELECT * FROM evidence
            WHERE (:risk IS NULL OR risk_level = :risk)
              AND (:outcome IS NULL OR review_outcome = :outcome
                   OR (:outcome = 'NOT_REVIEWED' AND review_outcome IS NULL))
              {'AND ' + engine + '_concern = 1' if engine else ''}
        )
    """
    with get_db_context() as session:
        user = session.execute(text("""
            SELECT UserId AS user_id, FirstName + ' ' + LastName AS name, userEmail AS email
            FROM dbo.Users WHERE UserId = :uid AND TenantId = :tid
        """), params).mappings().first()
        if not user:
            return None
        summary = dict(session.execute(text(cte + """
            SELECT COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN user_role IN ('nominator', 'both') THEN 1 ELSE 0 END), 0) AS nominations_made,
                COALESCE(SUM(CASE WHEN user_role IN ('nominee', 'both') THEN 1 ELSE 0 END), 0) AS nominations_received,
                COALESCE(SUM(CASE WHEN rf_concern + graph_concern + gnn_concern + semantic_concern > 0 THEN 1 ELSE 0 END), 0) AS engine_concerns,
                COALESCE(SUM(CASE WHEN review_outcome IN ('CONFIRMED_CONCERN', 'CONFIRMED_SEMANTIC_CONCERN') THEN 1 ELSE 0 END), 0) AS confirmed_issues,
                COALESCE(SUM(CASE WHEN review_outcome = 'CLEARED_NO_CONCERN' THEN 1 ELSE 0 END), 0) AS cleared_concerns,
                COALESCE(SUM(CASE WHEN review_outcome = 'CLEARED_UNSUBSTANTIATED' THEN 1 ELSE 0 END), 0) AS unsubstantiated,
                COALESCE(SUM(CASE WHEN training_disposition = 'EXCLUDED' THEN 1 ELSE 0 END), 0) AS not_for_training,
                COALESCE(SUM(CASE WHEN has_evidence = 0 THEN 1 ELSE 0 END), 0) AS missing_evidence
            FROM filtered
        """), params).mappings().one())
        rows = session.execute(text(cte + """
            SELECT * FROM filtered
            ORDER BY nomination_date DESC, nomination_id DESC
            OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
        """), params).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        # Return compact engine evidence, not full feature vectors for every row.
        engines = {}
        for key in ENGINE_COLUMNS:
            document = json.loads(item.pop(key) or '{}')
            if not isinstance(document, dict):
                document = {}
            if key == 'graph':
                document = compact_graph_result(document) or {}
            engines[key] = {field: document.get(field) for field in (
                'available', 'status', 'risk_level', 'score', 'findings',
                'unavailable_reason', 'combined_decision',
                'winning_pattern_type', 'winning_pattern_count',
                'winning_finding', 'explanation',
            )}
            findings = document.get('findings')
            engines[key]['findings'] = [
                value for value in findings if isinstance(value, str) and value.strip()
            ] if isinstance(findings, list) else []
            combined = document.get('combined_decision')
            if isinstance(combined, dict):
                combined = dict(combined)
                checks = combined.get('checks')
                combined['checks'] = [
                    value for value in checks if isinstance(value, str)
                ] if isinstance(checks, list) else []
            else:
                combined = None
            engines[key]['combined_decision'] = combined
            engines[key]['concern'] = bool(item.pop(key + '_concern'))
        item['engines'] = engines
        items.append(item)
    return {'user': dict(user), 'items': items, 'summary': summary,
            'total': summary['total'], 'page': page, 'page_size': page_size}
