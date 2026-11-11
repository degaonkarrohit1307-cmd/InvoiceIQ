"""
Health-check endpoints.

Two levels are exposed deliberately:
  - /health/live    -> "is the process up at all" (no dependencies touched)
  - /health/ready    -> "is the process up AND can it reach PostgreSQL"

Separating these is standard practice: a liveness check should never fail
just because a downstream dependency (like the DB) is temporarily down.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging_config import get_logger
from app.models.system import HealthCheckLog

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)


@router.get("/live")
def liveness() -> dict:
    return {"status": "ok", "check": "liveness"}


@router.get("/ready")
def readiness(db: Session = Depends(get_db)) -> dict:
    """Verifies the API can actually talk to PostgreSQL (SELECT 1 + a real write+read)."""
    db.execute(text("SELECT 1"))

    record = HealthCheckLog(status="ok")
    db.add(record)
    db.commit()
    db.refresh(record)

    total_checks = db.query(HealthCheckLog).count()

    logger.info("Readiness check passed. Total health_check_log rows: %s", total_checks)

    return {
        "status": "ok",
        "check": "readiness",
        "database": "connected",
        "last_check_id": str(record.id),
        "total_recorded_checks": total_checks,
    }
