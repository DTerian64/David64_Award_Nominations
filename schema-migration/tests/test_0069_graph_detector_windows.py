import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

path = Path(__file__).resolve().parents[1] / "alembic/versions/0069_graph_detector_history_windows.py"
spec = importlib.util.spec_from_file_location("migration_0069", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_same_windows_for_every_tenant_preserve_other_engines():
    original = {"gnn": {"window_days": 365}, "tabular": {"window_days": 270}, "other": [1]}
    updated = json.loads(migration._with_detector_windows(json.dumps(original)))
    assert updated["graph_pattern"]["detector_windows"]["Ring"] == 60
    assert all(days == 180 for name, days in updated["graph_pattern"]["detector_windows"].items() if name != "Ring")
    assert all(updated[key] == value for key, value in original.items())
    assert json.loads(migration._with_detector_windows(json.dumps(updated))) == updated
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(1, None), (5, None)]
    with patch.object(migration.op, "get_bind", return_value=conn):
        migration.upgrade()
    assert [call.args[1]["tenant_id"] for call in conn.execute.call_args_list[1:]] == [1, 5]
    assert migration.down_revision == "0068"
