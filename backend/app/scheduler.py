"""Background thread that keeps the local movie index fresh.

Downloads never happen on a user's request path. This thread:
  * on startup, ensures an index exists (downloads immediately if empty);
  * then wakes every EXPORT_CHECK_INTERVAL seconds and ingests the day's
    export once it's available and not yet loaded.
"""
from __future__ import annotations

import logging
import threading
import time

from . import config
from . import export_ingest

log = logging.getLogger("moviebeli.scheduler")

_thread: threading.Thread | None = None
_stop = threading.Event()


def _run() -> None:
    # Startup: make sure we have *something* to search against.
    try:
        if export_ingest.index_count() == 0:
            log.info("Movie index is empty — performing initial import.")
        result = export_ingest.ingest_if_needed()
        log.info("Startup export check: %s", result)
    except Exception:  # never let the scheduler die on a transient error
        log.exception("Initial export ingest failed; will retry on the next tick.")

    while not _stop.wait(config.EXPORT_CHECK_INTERVAL):
        try:
            result = export_ingest.ingest_if_needed()
            if result.get("status") == "ingested":
                log.info("Daily export refreshed: %s", result)
        except Exception:
            log.exception("Scheduled export ingest failed; will retry next tick.")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="export-scheduler", daemon=True)
    _thread.start()
    log.info("Export scheduler started (interval=%ss).", config.EXPORT_CHECK_INTERVAL)


def stop() -> None:
    _stop.set()
