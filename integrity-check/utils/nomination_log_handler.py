"""
utils/nomination_log_handler.py — persist nomination-scoped logs to SQL (SOC 2).

A non-blocking logging.Handler that captures every INFO+ record carrying a
`nomination_id` attribute, enqueues it, and a background daemon thread
batch-inserts into dbo.Nomination_Logs via the injected `insert_batch`
callback. Fully failure-isolated: a DB problem drops/retries in the flush
thread and never propagates into the request or worker path.

Attach it FIRST in setup_logging() (before the stdout handler) so it reads the
clean record before the console handler's filters mutate record.msg.
"""

import atexit
import json
import logging
import queue
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List

# LogRecord attributes that are NOT application extras.
_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "msg", "name",
    "module", "msecs", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "taskName", "thread", "threadName",
})
# Extras promoted to their own columns — excluded from the `details` JSON blob.
_COLUMN_EXTRAS = frozenset({"nomination_id", "tenant_id", "message_id"})


class NominationLogDBHandler(logging.Handler):
    """Buffer nomination-scoped log records and batch-insert them off the hot path."""

    def __init__(
        self,
        insert_batch: Callable[[List[Dict[str, Any]]], None],
        service: str,
        actor: str,
        *,
        level: int = logging.INFO,
        max_queue: int = 10_000,
        batch_size: int = 100,
        flush_interval: float = 2.0,
    ):
        super().__init__(level=level)
        self._insert_batch = insert_batch
        self._service = service
        self._actor = actor
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nomlog-flush", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        nom = getattr(record, "nomination_id", None)
        if nom is None:
            return
        try:
            nomination_id = int(nom)
        except (TypeError, ValueError):
            return
        try:
            details = {
                k: v for k, v in record.__dict__.items()
                if k not in _STANDARD_ATTRS and k not in _COLUMN_EXTRAS and not k.startswith("_")
            }
            tenant_id = getattr(record, "tenant_id", None)
            row = {
                "nomination_id": nomination_id,
                "tenant_id":     int(tenant_id) if tenant_id is not None else None,
                "log_time":      datetime.utcfromtimestamp(record.created),  # naive UTC (DATETIME2)
                "level":         record.levelname,
                "service":       self._service,
                "logger":        record.name,
                "message":       record.getMessage(),
                "message_id":    (getattr(record, "message_id", None) or None),
                "details":       json.dumps(details, default=str) if details else None,
                "exception":     self.formatException(record.exc_info) if record.exc_info else None,
                "created_by":    self._actor,
                "updated_by":    self._actor,
            }
            self._q.put_nowait(row)
        except queue.Full:
            pass  # under pressure, drop — logging must never block business logic
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain()
            if batch:
                try:
                    self._insert_batch(batch)
                except Exception:
                    pass  # never crash the flush thread on a DB error
            else:
                self._stop.wait(self._flush_interval)

    def _drain(self) -> List[Dict[str, Any]]:
        batch: List[Dict[str, Any]] = []
        try:
            batch.append(self._q.get(timeout=self._flush_interval))
        except queue.Empty:
            return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def close(self) -> None:
        if self._stop.is_set():
            super().close()
            return
        self._stop.set()
        try:
            remaining: List[Dict[str, Any]] = []
            while True:
                try:
                    remaining.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if remaining:
                self._insert_batch(remaining)
        except Exception:
            pass
        super().close()
