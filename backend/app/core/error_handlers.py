"""
Global error handling.

Ensures every error the API can produce (validation errors, unhandled
exceptions, DB errors) returns a consistent JSON shape and gets logged
server-side with enough detail to debug, without leaking internals to
the client.
"""
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _error_body(error_id: str, message: str, detail=None) -> dict:
    body = {"error_id": error_id, "message": message}
    if detail is not None:
        body["detail"] = detail
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        error_id = str(uuid.uuid4())
        logger.warning("Validation error [%s] on %s: %s", error_id, request.url.path, exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body(error_id, "Request validation failed.", exc.errors()),
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_exception_handler(request: Request, exc: SQLAlchemyError):
        error_id = str(uuid.uuid4())
        logger.error("Database error [%s] on %s: %s", error_id, request.url.path, str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_body(error_id, "A database error occurred. Please try again."),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        error_id = str(uuid.uuid4())
        logger.exception("Unhandled error [%s] on %s", error_id, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(error_id, "An unexpected error occurred."),
        )
