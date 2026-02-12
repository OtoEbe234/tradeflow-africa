"""
Celery application configuration.

Defines the Celery app with Redis broker, task autodiscovery,
and periodic beat schedule for the matching engine.
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "tradeflow",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Auto-discover tasks in the tasks package
celery_app.autodiscover_tasks(["app.tasks"])

# Beat schedule — periodic tasks
celery_app.conf.beat_schedule = {
    "run-matching-cycle": {
        "task": "app.tasks.matching_tasks.run_matching_cycle",
        "schedule": settings.MATCHING_CYCLE_INTERVAL_SECONDS,
    },
    "expire-stale-transactions": {
        "task": "app.tasks.payment_tasks.expire_stale_transactions",
        "schedule": 900,  # 15 minutes
    },
}
