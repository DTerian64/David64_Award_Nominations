"""Versioned, validated v6 generation inputs; no database or directory access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from .scenarios import SPECIALIST_FAMILIES

DEFAULT_PROFILE = Path(__file__).parent / "profiles" / "v6-balanced.json"


def configuration_hash(configuration: dict) -> str:
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_configuration(path: Path = DEFAULT_PROFILE) -> dict:
    configuration = json.loads(path.read_text(encoding="utf-8"))
    validate_configuration(configuration)
    return deepcopy(configuration)


def validate_configuration(c: dict) -> None:
    if c.get("schema_version") != 1 or c.get("generator_version") != "synthetics-inc-v6.0":
        raise ValueError("Unsupported synthetic configuration/generator version")
    def integer(value, minimum=1):
        return isinstance(value, int) and not isinstance(value, bool) and value >= minimum
    if not integer(c["population"]["user_count"], 200):
        raise ValueError("v6 hierarchy requires at least 200 corpus users")
    for value in (c["corpus"]["nomination_count"], c["corpus"]["window_days"], c["background"]["working_partners"]):
        if not integer(value):
            raise ValueError("Corpus sizes, history and partner counts must be positive integers")
    for value in (c["population"]["quiet_teams"], c["population"]["low_recognition_users"], c["controls_per_episode"], c["description_reuse"]["groups"]):
        if not integer(value, 0):
            raise ValueError("Cohort and episode counts must be nonnegative integers")
    for value in (c["corpus"]["fraud_rate"], c["corpus"]["fraud_rejected_fraction"], c["background"]["department_affinity"], c["background"]["weekend_weight"], *c["tabular_signals"].values(), c["audit"]["minimum_episode_recall"]):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("Probabilities must lie in [0,1]")
    if set(c["scenarios"]) != set(SPECIALIST_FAMILIES):
        raise ValueError("Configure the six explicit confirmed-pattern families; no scenario back-mapping")
    positives = 0
    for family, spec in c["scenarios"].items():
        if not integer(spec["episodes"], 0) or not integer(spec["targets_per_episode"]) or not integer(spec["span_days"]):
            raise ValueError(f"Invalid episode counts for {family}")
        if spec["span_days"] >= c["corpus"]["window_days"]:
            raise ValueError("Scenario span must fit inside the corpus")
        positives += spec["episodes"] * spec["targets_per_episode"]
    if positives != round(c["corpus"]["nomination_count"] * c["corpus"]["fraud_rate"]):
        raise ValueError("Scenario target counts must equal the configured fraud budget")
    if any(size not in (3, 4) for size in c["scenarios"]["RING"]["sizes"]):
        raise ValueError("Ring sizes must be 3 or 4")
    for family in ("TEMPORAL_BURST", "SUPER_NOMINATOR", "SUPER_BENEFICIARY"):
        spec = c["scenarios"][family]
        if not integer(spec["event_count"]) or spec["event_count"] <= spec["targets_per_episode"]:
            raise ValueError(f"{family} needs causal precursor events")
    block = c["scenarios"]["BIPARTITE_DENSE_BLOCK"]
    if not all(integer(block[key], 2 if key != "repeats" else 1) for key in ("left_size", "right_size", "repeats")):
        raise ValueError("Invalid dense-block dimensions")
    if block["left_size"] * block["right_size"] * block["repeats"] <= block["targets_per_episode"]:
        raise ValueError("Dense blocks need precursor edges before target labels")
    if not integer(c["description_reuse"]["group_size"], 3) or c["background"]["activity_spread"] < 0:
        raise ValueError("Invalid description group or activity spread")
    if not integer(c["description_reuse"].get("fraud_linked_groups", 0), 0) or c["description_reuse"].get("fraud_linked_groups", 0) > c["description_reuse"]["groups"]:
        raise ValueError("Fraud-linked reuse groups must fit the total reuse budget")
    if block["left_size"] + block["right_size"] > 30:
        raise ValueError("Dense-block dimensions exceed the episode participant budget")
    burst = c["scenarios"]["TEMPORAL_BURST"]
    if not 0 < burst.get("burst_hours", 24) <= burst["span_days"] * 24:
        raise ValueError("Burst hours must fit inside the episode span")
    for name in ("gnn", "tabular"):
        if not integer(c["engine_windows"][name]["window_days"]):
            raise ValueError("Engine windows must be positive integers")
    graph = c["engine_windows"]["graph_pattern"]
    names = {"Ring", "BipartiteDenseBlock", "TemporalBurst", "SuperNominator", "SuperBeneficiary", "CopyPasteFraud", "HiddenCandidate", "LowRecognitionNominator"}
    if set(graph["detector_windows"]) - names or not integer(graph["detection_window_days"]) or not all(integer(days) for days in graph["detector_windows"].values()):
        raise ValueError("Invalid Graph detector windows")
