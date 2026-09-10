"""Scrape durable state instead of relying on in-memory counters after restarts."""
from datetime import datetime, timezone
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select


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
                for status in ("queued", "pending", "running", "waiting", "retry", "failed", "succeeded", "cancelled"):
                    jobs.add_metric([kind, status], counts.get(status, 0))
            created = db.scalar(select(func.min(TripTask.created_at)).where(TripTask.status == "queued"))
            oldest.add_metric([], max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds()) if created else 0)
            for code in ("TASK_TIMEOUT", "PROCESS_INTERRUPTED", "GENERATION_FAILED", "WORKER_START_FAILED"):
                failures.add_metric([code], db.scalar(select(func.count()).select_from(TripTask).where(
                    TripTask.status == "failed", TripTask.error_code == code)))
        yield jobs
        yield oldest
        yield failures
