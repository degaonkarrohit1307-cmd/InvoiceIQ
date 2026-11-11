"""
Foundation-only models.

Intentionally minimal for Phase 1: this table exists solely to prove the
ORM -> Alembic migration -> PostgreSQL round trip actually works end to end.
Business tables (invoices, vendors, fraud_results, etc. per the DB design in
the proposal) are introduced starting Phase 2, when the corresponding
features are implemented.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HealthCheckLog(Base):
    """Records each successful DB-connectivity health check."""

    __tablename__ = "health_check_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
