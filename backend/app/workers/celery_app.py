
import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
REDIS_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://localhost:6379/2",
)

celery_app = Celery(
    "aiops",
    broker=REDIS_URL,
    backend=REDIS_RESULT_BACKEND,
    include=["backend.app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "daily-cleanup-candidate-scan": {
            "task": "aiops.scan_scheduled_cleanup_candidates",
            "schedule": 86400.0,
        },
    },
)
