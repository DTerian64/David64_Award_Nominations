"""Offline audit using production Graph detectors and causal feature builders.

No SQL, Entra, Service Bus, model training or model downloads. Semantic auditing
uses the exact cached production sentence transformer, never a test substitute.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

FAMILY_DETECTORS = {"RING": "Ring", "BIPARTITE_DENSE_BLOCK": "BipartiteDenseBlock",
                    "TEMPORAL_BURST": "TemporalBurst", "SUPER_NOMINATOR": "SuperNominator",
                    "SUPER_BENEFICIARY": "SuperBeneficiary"}


def _production_imports():
    root = Path(__file__).resolve().parents[2]
    for path in (root / "fraud-analytics-job", root / "integrity-engine-core" / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def validate(users, rows, c, as_of):
    ids = {u.logical_id for u in users}
    by_id = {u.logical_id: u for u in users}
    if len(ids) != c["population"]["user_count"] or len({u.upn for u in users}) != len(users):
        raise ValueError("Invalid corpus user identities")
    if any(u.account_enabled for u in users):
        raise ValueError("Synthetic directory users must remain disabled")
    if len(rows) != c["corpus"]["nomination_count"] or len({r.stable_id for r in rows}) != len(rows):
        raise ValueError("Invalid nomination count or stable IDs")
    end = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    start = end - timedelta(days=c["corpus"]["window_days"])
    for row in rows:
        if any(getattr(row, field) not in ids for field in ("nominator_logical_id", "beneficiary_logical_id", "approver_logical_id")):
            raise ValueError("A nomination references an external/operator identity")
        if row.nominator_logical_id == row.beneficiary_logical_id or row.approver_logical_id != by_id[row.beneficiary_logical_id].manager_logical_id:
            raise ValueError("Invalid nomination or beneficiary-manager approval")
        if not start <= datetime.fromisoformat(row.nomination_time_utc) < end:
            raise ValueError("Nomination outside configured corpus interval")
        expected = (row.scenario_family,) if row.training_disposition == "FRAUD" else ()
        if row.confirmed_patterns != expected:
            raise ValueError("Confirmed patterns differ from explicit ground truth")
    positives = Counter(r.scenario_family for r in rows if r.training_disposition == "FRAUD")
    expected = {name: spec["episodes"] * spec["targets_per_episode"] for name, spec in c["scenarios"].items() if spec["episodes"]}
    if dict(positives) != expected:
        raise ValueError("Generated pattern labels differ from configured fraud budget")
    return {"valid": True, "nomination_count": len(rows), "corpus_user_count": len(users),
            "operational_admin_count": 1, "confirmed_pattern_counts": dict(positives),
            "fraud_count": sum(positives.values()), "legitimate_count": len(rows) - sum(positives.values()),
            "window_start": start.date().isoformat(), "window_end": (end - timedelta(days=1)).date().isoformat()}


def audit(users, rows, design, c, as_of: date, *, policy: dict | None = None, semantic: bool = False):
    _production_imports()
    import numpy as np
    import pandas as pd
    from systems.award_nominations.modeling import graph
    from integrity_engine.graph.history_windows import detector_windows, filter_detector_history
    from integrity_engine.gnn.causal_context import causal_context_matrix, CAUSAL_CONTEXT_FEATURE_COLUMNS
    from systems.award_nominations.features.tabular.award_nomination_tabular_v1 import extract_features, add_semantic_features
    from systems.award_nominations.features.tabular import AWARD_NOMINATION_TABULAR_V1_SCHEMA
    from feature_builders.tabular.category_encoding import CategoryFraudRateEncoder

    end = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    mapping = {u.logical_id: i + 1 for i, u in enumerate(users)}
    records = [{"NominationId": i + 1, "NominatorId": mapping[r.nominator_logical_id], "BeneficiaryId": mapping[r.beneficiary_logical_id],
                "Status": r.status, "Amount": r.amount, "Description": r.description,
                "CreatedAt": datetime.fromisoformat(r.nomination_time_utc)} for i, r in enumerate(rows)]
    user_records = [{"UserId": mapping[u.logical_id], "FullName": u.display_name,
                     "ManagerId": mapping.get(u.manager_logical_id)} for u in users]
    behavior = [row for row in records if row["Status"] in ("Pending", "Approved", "Paid")]
    windows = detector_windows(c["engine_windows"]["graph_pattern"])
    if policy is not None:
        published_windows = detector_windows(policy)
        if published_windows != windows:
            raise ValueError("Audit policy windows differ from the configured engine windows")
    findings = {}
    calls = {"Ring": lambda data: graph.detect_rings(data, user_records, 5, "offline-v6", int((policy or {}).get("patterns", {}).get("Ring", {}).get("candidate_evaluation", {}).get("max_ring_size", 4)), policy),
             "BipartiteDenseBlock": lambda data: graph.detect_bipartite_dense_blocks(data, 5, "offline-v6", policy),
             "TemporalBurst": lambda data: graph.detect_temporal_bursts(data, 5, "offline-v6", policy),
             "SuperNominator": lambda data: graph.detect_super_nominators(data, 5, "offline-v6", policy),
             "SuperBeneficiary": lambda data: graph.detect_super_beneficiaries(data, 5, "offline-v6", policy),
             "LowRecognitionNominator": lambda data: graph.detect_low_recognition_nominators(data, user_records, 5, "offline-v6", policy),
             "HiddenCandidate": lambda data: graph.detect_hidden_candidate(data, user_records, 5, "offline-v6", policy=policy)}
    for detector, call in calls.items():
        if policy is not None and not policy.get("patterns", {}).get(detector, {}).get("enabled", False):
            findings[detector] = []
            continue
        findings[detector] = call(filter_detector_history(behavior, windows[detector], end))
    ever_active = {item[key] for item in records for key in ("NominatorId", "BeneficiaryId")}
    findings["Desert"] = graph.detect_deserts(ever_active, user_records, 5, "offline-v6", policy) if policy is None or policy.get("patterns", {}).get("Desert", {}).get("enabled", False) else []

    frame = pd.DataFrame([{**item, "NominationDate": item["CreatedAt"], "NominationDescription": item["Description"],
                           "CategoryId": rows[i].category_key, "IsFraud": int(rows[i].training_disposition == "FRAUD"),
                           "LabelSource": "synthetic_ground_truth"} for i, item in enumerate(records)])
    features, _, _ = extract_features(frame, c["engine_windows"]["tabular"]["window_days"])
    split = max(1, int(len(frame) * .8))
    encoder = CategoryFraudRateEncoder()
    features.loc[:split - 1, "CategoryFraudRate"] = encoder.fit_transform_training(
        frame.loc[:split - 1, "CategoryId"], frame.loc[:split - 1, "IsFraud"],
        occurred_at=frame.loc[:split - 1, "NominationDate"], fit_cutoff=frame.loc[split, "NominationDate"])
    features.loc[split:, "CategoryFraudRate"] = encoder.transform(frame.loc[split:, "CategoryId"])
    semantic_status = "NOT_RUN_LOCAL_MODEL_REQUIRED"
    if semantic:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        vectors = np.asarray(model.encode(frame["NominationDescription"].tolist(), normalize_embeddings=True, batch_size=64, show_progress_bar=False))
        cached = dict(zip(frame["NominationId"], vectors))
        by_text = dict(zip(frame["NominationDescription"], vectors))
        class CachedEncoder:
            def encode(self, texts, **kwargs):
                return np.asarray([by_text[text] for text in texts])
        semantic_frame = add_semantic_features(frame, CachedEncoder(), c["engine_windows"]["tabular"]["window_days"])
        for column in ("DescriptionCosineSim", "DescriptionEmbDistance"):
            features[column] = semantic_frame[column]
        if policy is None or policy.get("patterns", {}).get("CopyPaste", {}).get("enabled", False):
            findings["CopyPaste"] = graph.detect_copy_paste(filter_detector_history(behavior, windows["CopyPaste"], end), 5, "offline-v6", None,
                                                          policy=policy, embedding_vectors=cached)
        else:
            findings["CopyPaste"] = []
        semantic_status = "COMPLETED_FULL_CORPUS"

    gnn_records = [{**item, "IsBehaviorEligible": item["Status"] in ("Pending", "Approved", "Paid") or rows[i].training_disposition == "FRAUD"}
                   for i, item in enumerate(records)]
    gnn_values = pd.DataFrame(causal_context_matrix(gnn_records, records, window_days=c["engine_windows"]["gnn"]["window_days"]), columns=CAUSAL_CONTEXT_FEATURE_COLUMNS)
    summaries = {}
    matched_all = defaultdict(set)
    for family, detector in FAMILY_DETECTORS.items():
        detected = findings[detector]
        details = []
        for episode in [e for e in design["episodes"] if e["family"] == family]:
            target_ids = {i + 1 for i, row in enumerate(rows) if row.scenario_id == episode["id"] and row.scenario_phase == "TARGET"}
            in_scope = {item["NominationId"] for item in filter_detector_history(behavior, windows[detector], end)} & target_ids
            expected_users = {mapping[key] for key in episode["participants"]}
            matches = []
            for index, finding in enumerate(detected):
                evidence = set(json.loads(finding["NominationIds"]))
                participants = set(json.loads(finding["AffectedUsers"]))
                if in_scope & evidence and (family not in ("RING", "BIPARTITE_DENSE_BLOCK") or expected_users <= participants):
                    matches.append(index)
            matched_all[detector].update(matches)
            details.append({"episode_id": episode["id"], "disposition": episode["disposition"], "requested_targets": len(target_ids),
                            "in_scope_targets": len(in_scope), "matching_finding_count": len(matches),
                            "result": "OUT_OF_SCOPE" if not in_scope else "DETECTED" if matches else "MISSED"})
        fraud = [entry for entry in details if entry["disposition"] == "FRAUD"]
        eligible = [entry for entry in fraud if entry["result"] != "OUT_OF_SCOPE"]
        found = sum(entry["result"] == "DETECTED" for entry in eligible)
        summaries[detector] = {"window_days": windows[detector], "requested_fraud_episodes": len(fraud),
                               "eligible_fraud_episodes": len(eligible), "detected_fraud_episodes": found,
                               "missed_fraud_episodes": len(eligible) - found, "out_of_scope_fraud_episodes": len(fraud) - len(eligible),
                               "legitimate_control_episodes_detected": sum(entry["result"] == "DETECTED" and entry["disposition"] == "LEGITIMATE" for entry in details),
                               "observed_findings": len(detected), "incidental_findings": len(detected) - len(matched_all[detector]),
                               "episode_recall": found / len(eligible) if eligible else None, "episodes": details}
    summaries["RECIPROCAL"] = {"requested_fraud_episodes": c["scenarios"]["RECIPROCAL"]["episodes"],
                               "graph_detector_status": "NO_STANDALONE_GRAPH_DETECTOR",
                               "targets_with_causal_reverse_pair": int(sum(gnn_values.iloc[i]["LogPriorReversePairCount"] > 0 for i, r in enumerate(rows) if r.scenario_family == "RECIPROCAL"))}
    for detector, expected_sets in {
        "Desert": [{mapping[key] for key in design["quiet_user_ids"] if next(u.manager_logical_id for u in users if u.logical_id == key) == manager} for manager in design["quiet_managers"]],
        "LowRecognitionNominator": [{mapping[key]} for key in design["low_recognition_user_ids"]],
    }.items():
        observed = [set(json.loads(finding["AffectedUsers"])) for finding in findings[detector]]
        matches = [any(expected <= item for item in observed) for expected in expected_sets]
        summaries[detector] = {"requested_cases": len(expected_sets), "detected_planted_cases": sum(matches),
                               "missed_cases": len(matches) - sum(matches), "observed_findings": len(observed),
                               "incidental_findings": sum(not any(expected <= item for expected in expected_sets) for item in observed)}
    copy_details = []
    matched_copy_findings = set()
    copy_finding_sets = [set(json.loads(finding["NominationIds"])) for finding in findings.get("CopyPaste", [])]
    copy_scope = {row["NominationId"] for row in filter_detector_history(behavior, windows["CopyPaste"], end)}
    logical_map = {row.logical_id: i + 1 for i, row in enumerate(rows)}
    for group in design["description_reuse_groups"]:
        evidence = {logical_map[key] for key in group["logical_nomination_ids"]}
        eligible = evidence <= copy_scope
        matches = [i for i, item in enumerate(copy_finding_sets) if evidence <= item] if semantic and eligible else []
        matched_copy_findings.update(matches)
        matched = bool(matches)
        copy_details.append({"group_id": group["id"], "fraud_linked": group["fraud_linked"],
                             "result": "NOT_RUN" if not semantic else "OUT_OF_SCOPE" if not eligible else "DETECTED" if matched else "MISSED"})
    summaries["CopyPaste"] = {"requested_groups": len(copy_details), "status": semantic_status,
                               "detected_planted_groups": sum(item["result"] == "DETECTED" for item in copy_details),
                               "missed_groups": sum(item["result"] == "MISSED" for item in copy_details),
                               "out_of_scope_groups": sum(item["result"] == "OUT_OF_SCOPE" for item in copy_details),
                               "observed_findings": len(copy_finding_sets),
                               "incidental_findings": len(copy_finding_sets) - len(matched_copy_findings), "groups": copy_details}
    distributions = {}
    for name, getter in {"category": lambda r: r.category_name, "month": lambda r: r.nomination_time_utc[:7],
                         "department": lambda r: next(u.department for u in users if u.logical_id == r.nominator_logical_id),
                         "nominator": lambda r: r.nominator_logical_id,
                         "beneficiary": lambda r: r.beneficiary_logical_id,
                         "fold": lambda r: str(r.segment + 1)}.items():
        groups = defaultdict(Counter)
        for row in rows:
            groups[getter(row)][row.training_disposition] += 1
        distributions[name] = {key: {**dict(values), "fraud_rate": values["FRAUD"] / sum(values.values())} for key, values in sorted(groups.items())}
    def feature_summary(data):
        return {column: {str(label): ({"mean": float(data.loc[frame["IsFraud"] == label, column].mean()),
                                      "p50": float(data.loc[frame["IsFraud"] == label, column].median()),
                                      "p95": float(data.loc[frame["IsFraud"] == label, column].quantile(.95))}
                                     if (frame["IsFraud"] == label).any() else None)
                          for label in (0, 1)} for column in data.select_dtypes(include="number").columns}
    text_counts = Counter(r.description for r in rows)
    warnings = []
    for detector, summary in summaries.items():
        recall = summary.get("episode_recall")
        if recall is not None and recall < c["audit"]["minimum_episode_recall"]:
            warnings.append(f"{detector}: episode recall {recall:.3f} below configured audit target")
        if summary.get("missed_cases", 0) or summary.get("missed_groups", 0):
            warnings.append(f"{detector}: planted cases were missed")
        if summary.get("requested_fraud_episodes", 0) and summary.get("eligible_fraud_episodes") == 0:
            warnings.append(f"{detector}: no requested fraud episode is in the current detector window")
    if summaries["Ring"]["incidental_findings"] > c["audit"]["maximum_incidental_rings"]:
        warnings.append("Incidental rings exceed configured audit budget")
    # Shortcut probe: train small trees on one input at a time, then measure a
    # chronological holdout. Diagnostics only; never mutate labels/corpus.
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.metrics import average_precision_score
    shortcuts = {}
    probes = ("CategoryFraudRate", "MonthSin", "MonthCos", "Amount", "PairNominationCount") if frame.loc[:split - 1, "IsFraud"].nunique() == 2 else ()
    for column in probes:
        probe = DecisionTreeClassifier(max_depth=2, random_state=0).fit(features.loc[:split - 1, [column]], frame.loc[:split - 1, "IsFraud"])
        probability = probe.predict_proba(features.loc[split:, [column]])[:, list(probe.classes_).index(1)]
        shortcuts[column] = {"holdout_pr_auc": float(average_precision_score(frame.loc[split:, "IsFraud"], probability)),
                             "holdout_base_rate": float(frame.loc[split:, "IsFraud"].mean())}
    identity_probes = {}
    for role in ("NominatorId", "BeneficiaryId") if probes else ():
        # A diagnostic of persistent actor-label association, not a serving
        # feature or proof of leakage. No holdout outcomes fit these rates.
        actor_encoder = CategoryFraudRateEncoder()
        actor_encoder.fit_transform_training(frame.loc[:split - 1, role], frame.loc[:split - 1, "IsFraud"],
                                              occurred_at=frame.loc[:split - 1, "NominationDate"],
                                              fit_cutoff=frame.loc[split, "NominationDate"])
        probability = actor_encoder.transform(frame.loc[split:, role])
        identity_probes[role] = {"holdout_pr_auc": float(average_precision_score(frame.loc[split:, "IsFraud"], probability)),
                                "holdout_base_rate": float(frame.loc[split:, "IsFraud"].mean())}
    return {"schema_version": 1, "audit_status": "COMPLETED" if semantic and policy is not None else "PARTIAL",
            "policy_source": "SUPPLIED_POLICY" if policy is not None else "DETECTOR_DEFAULTS_NOT_TENANT_POLICY",
            "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest() if policy is not None else None,
            "semantic_status": semantic_status, "detector_windows": windows, "pattern_results": summaries,
            "observed_findings": {name: len(items) for name, items in findings.items()},
            "label_distributions": distributions, "tabular_feature_distributions": feature_summary(features[[name for name in AWARD_NOMINATION_TABULAR_V1_SCHEMA.feature_columns if name in features]]),
            "gnn_feature_distributions": feature_summary(gnn_values), "category_encoding": encoder.diagnostics,
            "shortcut_probes": shortcuts, "identity_memory_probes": identity_probes,
            "description_reuse": {"requested_groups": c["description_reuse"]["groups"],
                "unique_texts": len(text_counts), "exact_reuse_groups": sum(value >= 3 for value in text_counts.values()),
                "maximum_exact_group_size": max(text_counts.values(), default=0)},
            "warnings": warnings, "acceptance_passed": semantic and policy is not None and not warnings}


def write_bundle(directory: Path, configuration: dict, manifest: dict, report: dict, users, rows):
    import os
    import tempfile
    # Serialize before publication; failed audits never leave a partial bundle.
    payloads = {"configuration.json": configuration, "manifest.json": manifest,
                "audit-report.json": report, "corpus.json": {"users": [asdict(u) for u in users], "nominations": [asdict(r) for r in rows]}}
    serialized = {name: json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) for name, payload in payloads.items()}
    if directory.exists():
        raise ValueError("Refusing to overwrite a synthetic experiment bundle")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix=".v6-bundle-") as temporary:
        staged = Path(temporary) / "bundle"
        staged.mkdir()
        for name, contents in serialized.items():
            (staged / name).write_text(contents, encoding="utf-8")
        os.rename(staged, directory)
