"""Tests for durable nomination-log batching and failure isolation.

Purpose:
    Verify that a transient database failure is retried, that a permanently bad
    row cannot erase its neighboring records, and that exception tracebacks are
    serialized without dropping the record.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m unittest tests.test_nomination_log_handler -v
"""

import logging
import sys
import unittest

from utils.nomination_log_handler import NominationLogDBHandler


def _row(nomination_id: int, message: str) -> dict:
    return {
        "nomination_id": nomination_id,
        "tenant_id": 3,
        "logger": "integrity_check.handler",
        "message": message,
    }


class NominationLogHandlerTests(unittest.TestCase):
    def _handler(self, insert_batch):
        handler = NominationLogDBHandler(
            insert_batch,
            service="integrity-check-test",
            actor="svc:test",
            flush_interval=60,
            retry_delay=0,
        )
        # These tests invoke persistence directly; stop the daemon consumer so
        # it cannot race with assertions against the in-memory queue.
        handler._stop.set()
        handler._thread.join(timeout=1)
        return handler

    def test_transient_batch_failure_is_retried(self):
        calls = []

        def insert_batch(rows):
            calls.append(list(rows))
            if len(calls) == 1:
                raise RuntimeError("temporary SQL error")

        handler = self._handler(insert_batch)
        batch = [_row(1, "first"), _row(1, "second")]

        with self.assertLogs("integrity_check.nomination_log_persistence", "WARNING") as logs:
            handler._persist_batch(batch)

        self.assertEqual(calls, [batch, batch])
        self.assertTrue(any("batch insert failed; retrying" in line for line in logs.output))
        handler.close()

    def test_bad_row_does_not_erase_neighboring_rows(self):
        persisted = []
        calls = []

        def insert_batch(rows):
            calls.append(list(rows))
            if len(rows) > 1:
                raise RuntimeError("batch rejected")
            if rows[0]["message"] == "bad":
                raise ValueError("bad row")
            persisted.extend(rows)

        handler = self._handler(insert_batch)
        good_before = _row(2, "good before")
        bad = _row(2, "bad")
        good_after = _row(2, "good after")

        with self.assertLogs("integrity_check.nomination_log_persistence", "ERROR") as logs:
            handler._persist_batch([good_before, bad, good_after])

        self.assertEqual(persisted, [good_before, good_after])
        self.assertEqual(len(calls), 5)  # two batch attempts, then three rows
        self.assertTrue(any("row insert failed; record dropped" in line for line in logs.output))
        handler.close()

    def test_exception_traceback_is_serialized(self):
        handler = self._handler(lambda _rows: None)
        try:
            raise ValueError("SHAP exploded")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="integrity_check.random_forest",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="RF SHAP assessment failed",
            args=(),
            exc_info=exc_info,
        )
        record.nomination_id = 13872
        record.tenant_id = 3

        handler.emit(record)
        queued = handler._q.get_nowait()

        self.assertIn("ValueError: SHAP exploded", queued["exception"])
        self.assertEqual(queued["nomination_id"], 13872)
        handler.close()


if __name__ == "__main__":
    unittest.main()
