
from backend.app.workers.celery_app import celery_app


@celery_app.task(name="aiops.health_check")
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "aiops-celery-worker",
    }
