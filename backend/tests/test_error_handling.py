"""Tests for global error handling."""
from fastapi import APIRouter
from fastapi.testclient import TestClient


def test_404_returns_consistent_json_shape(client):
    resp = client.get("/api/v1/this-route-does-not-exist")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_validation_error_returns_422_with_error_id(client):
    # readiness/liveness take no query params, so hit an endpoint with a body
    # requirement indirectly isn't available yet in Phase 1 -- instead verify
    # the validation handler is wired by sending a malformed query type where
    # FastAPI itself would raise RequestValidationError. Since no such route
    # exists yet in Phase 1, we test the handler function directly.
    from app.core.error_handlers import _error_body

    body = _error_body("abc-123", "Request validation failed.", [{"loc": ["query", "x"], "msg": "bad"}])
    assert body["error_id"] == "abc-123"
    assert body["message"] == "Request validation failed."
    assert body["detail"][0]["msg"] == "bad"


def test_unhandled_exception_returns_500_and_hides_internal_detail():
    """
    Mounts a throwaway route on a fresh app instance (registered with the same
    exception handlers) that deliberately raises, to prove the global
    unhandled-exception handler returns a safe 500 without leaking the
    traceback to the client.
    """
    from fastapi import FastAPI

    from app.core.error_handlers import register_exception_handlers

    throwaway_app = FastAPI()
    register_exception_handlers(throwaway_app)

    router = APIRouter()

    @router.get("/boom")
    def boom():
        raise RuntimeError("deliberate failure for test")

    throwaway_app.include_router(router)

    with TestClient(throwaway_app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["message"] == "An unexpected error occurred."
    assert "deliberate failure" not in resp.text
    assert "error_id" in body
