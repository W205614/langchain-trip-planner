"""Scrape durable state instead of relying on in-memory counters after restarts."""
from datetime import datetime, timezone
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select
from threading import enumerate as threads
from shutil import disk_usage


class DurableCollector:
    def describe(self):
        return []

    def collect(self):
        from ..db.database import SessionLocal
        from ..db.models import TripTask, RagSyncJob, KnowledgeIngestJob
        jobs = GaugeMetricFamily("trip_durable_jobs", "Persisted job counts", labels=["kind", "status"])
        oldest = GaugeMetricFamily("trip_oldest_queue_age_seconds", "Age of oldest queued generation")
        failures = GaugeMetricFamily("trip_task_failures", "Persisted terminal failures", labels=["code"])
        with SessionLocal() as db:
            for kind, model in (("trip", TripTask), ("history", RagSyncJob), ("knowledge", KnowledgeIngestJob)):
                counts = dict(db.execute(select(model.status, func.count()).group_by(model.status)).all())
                for status in ("queued", "pending", "running", "waiting", "retry", "failed", "succeeded", "cancelled", "needs_attention"):
                    jobs.add_metric([kind, status], counts.get(status, 0))
            created = db.scalar(select(func.min(TripTask.created_at)).where(TripTask.status == "queued"))
            oldest.add_metric([], max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds()) if created else 0)
            for code in ("TASK_TIMEOUT", "PROCESS_INTERRUPTED", "GENERATION_FAILED", "WORKER_START_FAILED", "TRUSTED_POI_UNAVAILABLE", "UPSTREAM_CONFIG_ERROR"):
                failures.add_metric([code], db.scalar(select(func.count()).select_from(TripTask).where(
                    TripTask.status == "failed", TripTask.error_code == code)))
        yield jobs
        yield oldest
        yield failures
        workers = GaugeMetricFamily("trip_worker_alive", "Local worker thread presence", labels=["kind"])
        names = {t.name for t in threads() if t.is_alive()}
        for name in ("trip-task-scheduler", "rag-sync-worker", "knowledge-ingest-worker"):
            workers.add_metric([name], int(name in names))
        yield workers
        stalled = GaugeMetricFamily("trip_oldest_sync_age_seconds", "Age of unfinished sync work", labels=["kind"])
        with SessionLocal() as db:
            for kind, model in (("history", RagSyncJob), ("knowledge", KnowledgeIngestJob)):
                created = db.scalar(select(func.min(model.created_at)).where(model.status.in_(("pending", "running", "waiting", "retry"))))
                stalled.add_metric([kind], max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds()) if created else 0)
        yield stalled
        from ..db.database import DATA_DIR
        disk = disk_usage(DATA_DIR)
        free = GaugeMetricFamily("trip_data_disk_free_ratio", "Free space on the data volume")
        free.add_metric([], disk.free / disk.total)
        yield free
