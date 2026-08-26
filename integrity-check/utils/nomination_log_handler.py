"""
utils/nomination_log_handler.py — persist nomination-scoped logs to SQL (SOC 2).

A non-blocking logging.Handler that captures every INFO+ record carrying a
`nomination_id` attribute, enqueues it, and a background daemon thread
batch-inserts into dbo.Nomination_Logs via the injected `insert_batch`
callback. Fully failure-isolated: a DB problem drops/retries in the flush
thread and never propagates into the request or worker path. A failed batch is
retried once, then isolated row-by-row so one malformed record cannot erase
the surrounding nomination trail.

Attach it FIRST in setup_logging() (before the stdout handler) so it reads the
clean record before the console handler's filters mutate record.msg.
"""

import atexit
import json
import logging
import queue
import threading
from datetime import datetime, timezone
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
_diagnostic_logger = logging.getLogger("integrity_check.nomination_log_persistence")


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
        retry_delay: float = 0.25,
    ):
        super().__init__(level=level)
        self._insert_batch = insert_batch
        self._service = service
        self._actor = actor
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._retry_delay = retry_delay
        self._dropped_queue_records = 0
        self._exception_formatter = logging.Formatter()
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
                # SQL DATETIME2 is timezone-naive; create UTC explicitly, then
                # remove tzinfo instead of using deprecated utcfromtimestamp().
                "log_time":      datetime.fromtimestamp(
                    record.created, timezone.utc
                ).replace(tzinfo=None),
                "level":         record.levelname,
                "service":       self._service,
                "logger":        record.name,
                "message":       record.getMessage(),
                "message_id":    (getattr(record, "message_id", None) or None),
                "details":       json.dumps(details, default=str) if details else None,
                "exception":     (
                    self._exception_formatter.formatException(record.exc_info)
                    if record.exc_info else None
                ),
                "created_by":    self._actor,
                "updated_by":    self._actor,
            }
            self._q.put_nowait(row)
        except queue.Full:
            # Keep business processing non-blocking, but make loss observable.
            # Rate-limit the warning so a saturated queue cannot flood stdout.
            self._dropped_queue_records += 1
            if self._dropped_queue_records == 1 or self._dropped_queue_records % 100 == 0:
                self._report(
                    logging.ERROR,
                    "Nomination log queue full; record dropped",
                    dropped_record_count=self._dropped_queue_records,
                    queue_size=self._q.qsize(),
                    source_logger=record.name,
                )
        except Exception as exc:
            self._report(
                logging.ERROR,
                "Nomination log record could not be serialized; record dropped",
                error_type=type(exc).__name__,
                error=str(exc)[:2_000],
                source_logger=record.name,
                failed_nomination_id=nomination_id,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain()
            if batch:
                self._persist_batch(batch)
            else:
                self._stop.wait(self._flush_interval)

    def _persist_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Persist a batch without allowing one failure to erase every row."""
        try:
            self._insert_batch(batch)
            return
        except Exception as exc:
            self._report(
                logging.WARNING,
                "Nomination log batch insert failed; retrying",
                batch_size=len(batch),
                error_type=type(exc).__name__,
                error=str(exc)[:2_000],
            )

        if self._retry_delay > 0:
            self._stop.wait(self._retry_delay)

        try:
            self._insert_batch(batch)
            return
        except Exception as exc:
            self._report(
                logging.ERROR,
                "Nomination log batch retry failed; isolating rows",
                batch_size=len(batch),
                error_type=type(exc).__name__,
                error=str(exc)[:2_000],
            )

        for row in batch:
            try:
                self._insert_batch([row])
            except Exception as exc:
                self._report(
                    logging.ERROR,
                    "Nomination log row insert failed; record dropped",
                    error_type=type(exc).__name__,
                    error=str(exc)[:2_000],
                    failed_nomination_id=row.get("nomination_id"),
                    failed_logger=row.get("logger"),
                    failed_message=row.get("message"),
                )

    @staticmethod
    def _report(level: int, message: str, **details: Any) -> None:
        """Write diagnostics to stdout without re-enqueuing them in this handler."""
        # Deliberately do not use the reserved `nomination_id` extra. The root
        # logger still sends this to the console/App Insights handler, while
        # NominationLogDBHandler ignores it and therefore cannot recurse.
        _diagnostic_logger.log(level, message, extra=details)

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
                self._persist_batch(remaining)
        except Exception as exc:
            self._report(
                logging.ERROR,
                "Nomination log shutdown flush failed",
                error_type=type(exc).__name__,
                error=str(exc)[:2_000],
            )
        super().close()
