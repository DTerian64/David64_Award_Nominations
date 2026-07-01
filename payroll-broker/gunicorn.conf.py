# gunicorn.conf.py
# ─────────────────────────────────────────────────────────────────────────────
# Gunicorn configuration for the Payroll Broker service.
#
# Azure Monitor / OTel is intentionally NOT configured in post_fork().
# UvicornWorker calls logging.config.dictConfig() during its own startup,
# which runs after post_fork() and wipes any handlers added here.  OTel is
# therefore initialized in the FastAPI lifespan startup event (main.py),
# which runs after uvicorn has finished configuring logging.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os

logger = logging.getLogger(__name__)


def post_fork(server, worker):
    """Called in each worker immediately after forking from the master."""
    pass
