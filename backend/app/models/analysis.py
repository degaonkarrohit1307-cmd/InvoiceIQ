"""Versioned invoice analysis persistence (fraud, GST and SHAP)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InvoiceAnalysis(Base):
    __tablename__ = "invoice_analyses"

    __table_args__ = (
        UniqueConstraint(
            "extraction_id",
            "analysis_version",
            name="uq_invoice_analyses_extraction_version",
        ),
        Index("ix_invoice_analyses_extraction_id", "extraction_id"),
        Index("ix_invoice_analyses_risk_category", "fraud_risk_category"),
        Index("ix_invoice_analyses_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )

    analysis_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # LangGraph execution status
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    # ML model information
    model_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Fraud scoring
    fraud_score: Mapped[float | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    fraud_risk_category: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )

    fraud_supervised_score: Mapped[float | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    fraud_anomaly_score: Mapped[float | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    fraud_mathematical_score: Mapped[float | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )

    model_prediction: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    fraud_breakdown: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # GST compliance
    gst_compliance_overall: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    gst_compliance_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    gst_external_verification: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # SHAP explainability
    shap_status: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    shap_explanation: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Aggregated orchestration information
    key_signals: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    validation_errors: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )