"""
Application entrypoint.

Run with:  uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path

from app.ml.pipeline import FraudAnalysisPipeline
from app.orchestration.graph import build_invoice_verification_graph

from app.api.v1.health import router as health_router
from app.api.v1.invoices import router as invoices_router
from app.core.config import get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging_config import configure_logging, get_logger

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


def create_app() -> FastAPI:

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        artifact_path = (
            Path(__file__).resolve().parent.parent
            / "artifacts"
            / "fraud_pipeline.joblib"
        )

        application.state.fraud_pipeline = None
        application.state.invoice_verification_graph = None

        if artifact_path.is_file():
            try:
                pipeline = FraudAnalysisPipeline.load(artifact_path)

                if not pipeline.is_trained:
                    raise RuntimeError("Loaded fraud pipeline is not trained.")

                verification_graph = build_invoice_verification_graph(
                    pipeline,
                    gst_sandbox_client=None,
                )

                application.state.fraud_pipeline = pipeline
                application.state.invoice_verification_graph = verification_graph

                logger.info("Fraud pipeline loaded successfully.")
                logger.info("Invoice verification graph initialized.")

            except Exception:
                logger.exception(
                    "Failed to initialize fraud analysis pipeline."
                )
        else:
            logger.warning(
                "Fraud model artifact not found: %s",
                artifact_path,
            )

        yield

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description=(
            "AI-powered invoice verification, fraud detection, and GST "
            "compliance analysis system. Phase 1: project foundation."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix=settings.API_V1_PREFIX)
    app.include_router(invoices_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", tags=["root"])
    def root() -> dict:
        return {
            "service": settings.APP_NAME,
            "status": "running",
            "docs": "/docs",
            "api_prefix": settings.API_V1_PREFIX,
        }

    logger.info("Application configured. Environment: %s", settings.APP_ENV)
    return app


app = create_app()
