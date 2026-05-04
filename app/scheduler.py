from __future__ import annotations
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .ingest import run_ingest

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self):
        self._scheduler = BackgroundScheduler()
        self._lock = threading.Lock()
        self._running = False
        self.last_run_at: Optional[str] = None
        self.last_inserted: int = 0
        self.last_error: Optional[str] = None
        self.next_run_at: Optional[str] = None

    def _job(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        try:
            inserted = run_ingest()
            self.last_inserted = inserted
            self.last_error = None
        except Exception as e:
            logger.error("Scheduler job failed: %s", e)
            self.last_error = str(e)
        finally:
            self.last_run_at = datetime.now(timezone.utc).isoformat()
            self._running = False
            job = self._scheduler.get_job("daily_ingest")
            if job and job.next_run_time:
                self.next_run_at = job.next_run_time.isoformat()

    def start(self):
        cfg = get_settings()
        trigger = CronTrigger(
            hour=cfg.daily_hour,
            minute=cfg.daily_minute,
            timezone=cfg.timezone,
        )
        self._scheduler.add_job(self._job, trigger, id="daily_ingest", replace_existing=True)
        self._scheduler.start()
        job = self._scheduler.get_job("daily_ingest")
        if job and job.next_run_time:
            self.next_run_at = job.next_run_time.isoformat()
        logger.info("Scheduler started, next run: %s", self.next_run_at)

    def trigger_now(self):
        t = threading.Thread(target=self._job, daemon=True)
        t.start()

    def get_state(self) -> dict:
        return {
            "running": self._running,
            "last_run_at": self.last_run_at,
            "last_inserted": self.last_inserted,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
        }

    def shutdown(self):
        self._scheduler.shutdown(wait=False)


_service: Optional[SchedulerService] = None


def get_scheduler() -> SchedulerService:
    global _service
    if _service is None:
        _service = SchedulerService()
    return _service
