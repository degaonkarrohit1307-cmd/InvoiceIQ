"""
Phase 6B tests: GST Sandbox API verification.

Field names and response shapes used throughout these tests ("status",
"validGstin", "legalName", the /authenticate access_token wrapper) mirror
the REAL documented Sandbox GST API contract (developer.sandbox.co.in),
not invented values.

Uses fake/mock clients for most tests -- NO real HTTP calls are made. The
tests that exercise HttpxGSTSandboxClient directly use httpx's
MockTransport, which also never touches the real network.
"""
import json
import time

import httpx
import pytest

from app.compliance.gst_sandbox_client import (
    GSTSandboxConnectionError,
    GSTSandboxNotConfiguredError,
    GSTSandboxResponseError,
    GSTSandboxTimeoutError,
    HttpxGSTSandboxClient,
)
from app.compliance.gst_sandbox_models import GSTVerificationStatus
from app.compliance.gst_sandbox_service import verify_gstin_externally

VALID_GSTIN = "27AAAAA0000A1Z5"

# Shaped exactly like the real API's data.data taxpayer object.
ACTIVE_TAXPAYER = {
    "legalName": "Acme Trading Co", "bussNature": "Service Provider and Others",
    "stateName": "Gujarat", "validGstin": True, "stateCode": "24",
    "pan": "ABKCS2033B", "gstin": VALID_GSTIN, "regStartDate": "06/02/2023", "status": "Active",
}
CANCELLED_TAXPAYER = {**ACTIVE_TAXPAYER, "status": "Cancelled on application of Taxpayer"}
NOT_VALID_TAXPAYER = {**ACTIVE_TAXPAYER, "validGstin": False}


class _FakeClient:
    """A mockable client satisfying the GSTSandboxClient protocol -- no network access."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.called_with: list[str] = []

    def verify_gstin(self, gstin: str) -> dict:
        self.called_with.append(gstin)
        if self._exc is not None:
            raise self._exc
        return self._response


def _mock_transport(authenticate_response: dict, verify_response: dict | None = None, verify_status: int = 200):
    """Builds an httpx.MockTransport that answers both /authenticate and the verify endpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authenticate":
            return httpx.Response(200, json=authenticate_response)
        if request.url.path == "/gst/compliance/public/gstin/verify":
            return httpx.Response(verify_status, json=verify_response)
        raise AssertionError(f"Unexpected path: {request.url.path}")
    return httpx.MockTransport(handler)


def _configure(monkeypatch, base_url="https://test-api.sandbox.co.in", key="test-key", secret="test-secret"):
    from app.core import config as config_module

    monkeypatch.setenv("GST_SANDBOX_API_BASE_URL", base_url)
    monkeypatch.setenv("GST_SANDBOX_API_KEY", key)
    monkeypatch.setenv("GST_SANDBOX_API_SECRET", secret)
    config_module.get_settings.cache_clear()
    return config_module


# --------------------------------------------------------------------
# Successful verification: active / inactive(cancelled) / not valid
# --------------------------------------------------------------------

def test_successful_verification_active_gstin():
    client = _FakeClient(response=ACTIVE_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)

    assert result.status == GSTVerificationStatus.API_VERIFIED_ACTIVE
    assert result.format_valid is True
    assert result.legal_name == "Acme Trading Co"
    assert client.called_with == [VALID_GSTIN]


def test_api_reports_gstin_registered_but_cancelled_inactive():
    client = _FakeClient(response=CANCELLED_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_VERIFIED_INACTIVE
    assert "Cancelled on application of Taxpayer" in result.detail


def test_api_reports_valid_gstin_false_as_not_found():
    client = _FakeClient(response=NOT_VALID_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_VERIFIED_NOT_FOUND


# --------------------------------------------------------------------
# Malformed response / timeout / connection failure
# --------------------------------------------------------------------

def test_malformed_api_response_produces_api_error():
    client = _FakeClient(response={"unexpected_shape": True})  # missing required "status"/"validGstin" keys
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_ERROR


def test_timeout_produces_api_timeout_status():
    client = _FakeClient(exc=GSTSandboxTimeoutError("simulated timeout"))
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_TIMEOUT
    assert result.format_valid is True  # format was still valid; only the API call failed


def test_connection_failure_produces_api_unavailable_status():
    client = _FakeClient(exc=GSTSandboxConnectionError("simulated connection failure"))
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_UNAVAILABLE


def test_response_error_produces_api_error_status():
    client = _FakeClient(exc=GSTSandboxResponseError("simulated bad response"))
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.API_ERROR


def test_documented_422_invalid_pattern_maps_to_api_error(monkeypatch):
    """The real API's documented 422 'Invalid GSTIN pattern' response is surfaced as API_ERROR, not reinterpreted."""
    _configure(monkeypatch)
    try:
        transport = _mock_transport(
            authenticate_response={"code": 200, "data": {"access_token": "fake.jwt.token"}},
            verify_response={"code": 422, "message": "Invalid GSTIN pattern"},
            verify_status=422,
        )

        def fake_post(url, headers=None, json=None, timeout=None):
            with httpx.Client(transport=transport) as c:
                return c.post(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr(httpx, "post", fake_post)

        client = HttpxGSTSandboxClient()
        with pytest.raises(GSTSandboxResponseError):
            client.verify_gstin(VALID_GSTIN)
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


# --------------------------------------------------------------------
# Missing API credentials/configuration (now requires key AND secret)
# --------------------------------------------------------------------

def test_not_configured_client_produces_format_valid_not_verified():
    client = _FakeClient(exc=GSTSandboxNotConfiguredError("not configured"))
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.status == GSTVerificationStatus.FORMAT_VALID_NOT_API_VERIFIED
    assert result.status not in {GSTVerificationStatus.API_VERIFIED_ACTIVE, GSTVerificationStatus.API_VERIFIED_INACTIVE}


def test_no_client_and_no_env_config_does_not_crash(monkeypatch):
    """With no injected client and no GST_SANDBOX_API_* env vars set, must degrade gracefully, never raise."""
    from app.core import config as config_module

    monkeypatch.delenv("GST_SANDBOX_API_BASE_URL", raising=False)
    monkeypatch.delenv("GST_SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("GST_SANDBOX_API_SECRET", raising=False)
    config_module.get_settings.cache_clear()
    try:
        result = verify_gstin_externally(VALID_GSTIN, client=None)
        assert result.status == GSTVerificationStatus.FORMAT_VALID_NOT_API_VERIFIED
    finally:
        config_module.get_settings.cache_clear()


def test_httpx_client_raises_not_configured_when_settings_empty(monkeypatch):
    from app.core import config as config_module

    monkeypatch.delenv("GST_SANDBOX_API_BASE_URL", raising=False)
    monkeypatch.delenv("GST_SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("GST_SANDBOX_API_SECRET", raising=False)
    config_module.get_settings.cache_clear()
    try:
        client = HttpxGSTSandboxClient()
        with pytest.raises(GSTSandboxNotConfiguredError):
            client.verify_gstin(VALID_GSTIN)
    finally:
        config_module.get_settings.cache_clear()


def test_httpx_client_requires_secret_not_just_key(monkeypatch):
    """Real contract needs BOTH key and secret (two-step auth) -- key alone must not be treated as configured."""
    from app.core import config as config_module

    monkeypatch.setenv("GST_SANDBOX_API_BASE_URL", "https://test-api.sandbox.co.in")
    monkeypatch.setenv("GST_SANDBOX_API_KEY", "key-only-no-secret")
    monkeypatch.delenv("GST_SANDBOX_API_SECRET", raising=False)
    config_module.get_settings.cache_clear()
    try:
        client = HttpxGSTSandboxClient()
        with pytest.raises(GSTSandboxNotConfiguredError):
            client.verify_gstin(VALID_GSTIN)
    finally:
        config_module.get_settings.cache_clear()


# --------------------------------------------------------------------
# Format-invalid GSTIN must not call the API at all
# --------------------------------------------------------------------

def test_format_invalid_gstin_never_calls_the_api():
    client = _FakeClient(response=ACTIVE_TAXPAYER)  # would succeed IF called
    result = verify_gstin_externally("NOT-A-VALID-GSTIN", client=client)

    assert result.status == GSTVerificationStatus.FORMAT_INVALID
    assert client.called_with == []  # API was never invoked


def test_none_gstin_is_not_checkable_and_never_calls_api():
    client = _FakeClient(response=ACTIVE_TAXPAYER)
    result = verify_gstin_externally(None, client=client)
    assert result.status == GSTVerificationStatus.NOT_CHECKABLE
    assert client.called_with == []


# --------------------------------------------------------------------
# API result stays distinct from local format validation (Phase 6A)
# --------------------------------------------------------------------

def test_api_result_type_is_distinct_from_phase_6a_rulecheck():
    from app.compliance.gst_sandbox_models import GSTVerificationResult
    from app.services.math_verification import RuleCheck

    client = _FakeClient(response=ACTIVE_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)

    assert isinstance(result, GSTVerificationResult)
    assert not isinstance(result, RuleCheck)


def test_format_valid_does_not_imply_api_verified_status_value():
    """Format PASS (Phase 6A) and API verification (Phase 6B) use non-overlapping status vocabularies."""
    client = _FakeClient(exc=GSTSandboxNotConfiguredError("not configured"))
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    assert result.format_valid is True
    assert "FORMAT_VALID_NOT_API_VERIFIED" == result.status.value
    assert "ACTIVE" not in result.status.value


# --------------------------------------------------------------------
# JSON serialization
# --------------------------------------------------------------------

def test_result_to_dict_is_json_serializable():
    client = _FakeClient(response=ACTIVE_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    serialized = json.dumps(result.to_dict())
    parsed = json.loads(serialized)
    assert parsed["status"] == "API_VERIFIED_ACTIVE"
    assert parsed["gstin"] == VALID_GSTIN


# --------------------------------------------------------------------
# Credentials are never exposed
# --------------------------------------------------------------------

def test_result_dict_never_contains_credential_like_keys():
    client = _FakeClient(response=ACTIVE_TAXPAYER)
    result = verify_gstin_externally(VALID_GSTIN, client=client)
    serialized = json.dumps(result.to_dict()).lower()
    assert "api_key" not in serialized
    assert "api_secret" not in serialized
    assert "authorization" not in serialized
    assert "bearer" not in serialized
    assert "secret" not in serialized
    assert "access_token" not in serialized


def test_not_configured_error_message_never_contains_actual_secret_value(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("GST_SANDBOX_API_KEY", "key-but-no-secret")
    monkeypatch.delenv("GST_SANDBOX_API_SECRET", raising=False)
    monkeypatch.delenv("GST_SANDBOX_API_BASE_URL", raising=False)
    config_module.get_settings.cache_clear()
    try:
        client = HttpxGSTSandboxClient()
        with pytest.raises(GSTSandboxNotConfiguredError) as exc_info:
            client.verify_gstin(VALID_GSTIN)
        assert "key-but-no-secret" not in str(exc_info.value)
    finally:
        config_module.get_settings.cache_clear()


def test_httpx_client_never_logs_or_returns_credentials_in_connection_error(monkeypatch):
    """Uses httpx.MockTransport (no real network) to simulate a connection failure during /authenticate."""
    _configure(monkeypatch, key="super-secret-key-should-not-leak", secret="super-secret-secret-should-not-leak")
    try:
        def _raise_connect_error(request):
            raise httpx.ConnectError("simulated", request=request)

        transport = httpx.MockTransport(_raise_connect_error)

        def fake_post(url, headers=None, json=None, timeout=None):
            with httpx.Client(transport=transport) as c:
                return c.post(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr(httpx, "post", fake_post)

        client = HttpxGSTSandboxClient()
        with pytest.raises(GSTSandboxConnectionError) as exc_info:
            client.verify_gstin(VALID_GSTIN)
        assert "super-secret-key-should-not-leak" not in str(exc_info.value)
        assert "super-secret-secret-should-not-leak" not in str(exc_info.value)
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


# --------------------------------------------------------------------
# JWT access-token caching / expiry (hardening)
# --------------------------------------------------------------------

def _counting_transport(authenticate_response: dict, verify_response: dict, call_log: list):
    """Like _mock_transport, but records every /authenticate call so tests can assert how many happened."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authenticate":
            call_log.append("authenticate")
            return httpx.Response(200, json=authenticate_response)
        if request.url.path == "/gst/compliance/public/gstin/verify":
            return httpx.Response(200, json=verify_response)
        raise AssertionError(f"Unexpected path: {request.url.path}")
    return httpx.MockTransport(handler)


def _patch_post(monkeypatch, transport):
    def fake_post(url, headers=None, json=None, timeout=None):
        with httpx.Client(transport=transport) as c:
            return c.post(url, headers=headers, json=json, timeout=timeout)
    monkeypatch.setattr(httpx, "post", fake_post)


def test_valid_cached_token_is_reused_not_refetched(monkeypatch):
    """Two verify_gstin() calls in a row should only hit /authenticate once while the cached token is still valid."""
    _configure(monkeypatch)
    try:
        auth_calls = []
        transport = _counting_transport(
            authenticate_response={"code": 200, "data": {"access_token": "token-1"}},
            verify_response={"code": 200, "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"}},
            call_log=auth_calls,
        )
        _patch_post(monkeypatch, transport)

        client = HttpxGSTSandboxClient()
        client.verify_gstin(VALID_GSTIN)
        client.verify_gstin(VALID_GSTIN)

        assert auth_calls == ["authenticate"]  # only one /authenticate call across both verifications
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


def test_expired_token_triggers_reauthentication(monkeypatch):
    """A token whose TTL has already elapsed must trigger a fresh /authenticate call before the next verify."""
    _configure(monkeypatch)
    try:
        auth_calls = []
        transport = _counting_transport(
            authenticate_response={"code": 200, "data": {"access_token": "token-expired-case"}},
            verify_response={"code": 200, "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"}},
            call_log=auth_calls,
        )
        _patch_post(monkeypatch, transport)

        # TTL of 10s with a 0s margin -- so the token is "expired" almost immediately for test purposes.
        client = HttpxGSTSandboxClient(token_ttl_seconds=10.0, token_refresh_margin_seconds=0.0)
        client.verify_gstin(VALID_GSTIN)
        assert auth_calls == ["authenticate"]

        # Force the cached expiry into the past without sleeping in the test.
        client._token_expires_at -= 1000.0

        client.verify_gstin(VALID_GSTIN)
        assert auth_calls == ["authenticate", "authenticate"]  # re-authenticated
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


def test_token_near_expiry_within_safety_margin_triggers_refresh(monkeypatch):
    """A token inside the configured refresh-margin window (even if not technically expired) should be refreshed."""
    _configure(monkeypatch)
    try:
        auth_calls = []
        transport = _counting_transport(
            authenticate_response={"code": 200, "data": {"access_token": "token-margin-case"}},
            verify_response={"code": 200, "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"}},
            call_log=auth_calls,
        )
        _patch_post(monkeypatch, transport)

        # TTL of 100s with a 90s safety margin -> usable TTL is only ~10s.
        client = HttpxGSTSandboxClient(token_ttl_seconds=100.0, token_refresh_margin_seconds=90.0)
        client.verify_gstin(VALID_GSTIN)
        assert auth_calls == ["authenticate"]

        # Simulate "11 seconds later" (past the ~10s usable window, well within
        # the original 100s raw TTL) by moving the recorded expiry into the past.
        client._token_expires_at -= 11.0

        client.verify_gstin(VALID_GSTIN)
        assert auth_calls == ["authenticate", "authenticate"]
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


def test_authenticate_response_without_expires_in_field_handled_safely(monkeypatch):
    """
    The real documented response has no expiry field at all -- confirms
    the client falls back to the configured TTL rather than crashing or
    treating the token as having no expiry (which would reintroduce the
    original indefinite-reuse problem).
    """
    _configure(monkeypatch)
    try:
        transport = _mock_transport(
            authenticate_response={"code": 200, "data": {"access_token": "token-no-expiry-field"}},  # no expires_in
            verify_response={"code": 200, "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"}},
        )
        _patch_post(monkeypatch, transport)

        client = HttpxGSTSandboxClient(token_ttl_seconds=3600.0, token_refresh_margin_seconds=60.0)
        client.verify_gstin(VALID_GSTIN)

        assert client._cached_access_token == "token-no-expiry-field"
        assert client._token_expires_at is not None
        # Expiry should reflect the configured fallback TTL (3600 - 60 = 3540s from now), not be unset/infinite.
        remaining = client._token_expires_at - time.monotonic()
        assert 3500 < remaining <= 3540
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


def test_authenticate_response_with_expires_in_field_is_honored(monkeypatch):
    """If a future Sandbox response DOES include expires_in, it should be used instead of the configured default."""
    _configure(monkeypatch)
    try:
        transport = _mock_transport(
            authenticate_response={"code": 200, "data": {"access_token": "token-with-expiry", "expires_in": 120}},
            verify_response={"code": 200, "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"}},
        )
        _patch_post(monkeypatch, transport)

        client = HttpxGSTSandboxClient(token_ttl_seconds=99999.0, token_refresh_margin_seconds=10.0)
        client.verify_gstin(VALID_GSTIN)

        remaining = client._token_expires_at - time.monotonic()
        # Should reflect expires_in=120 minus the 10s margin (~110s), NOT the configured 99999s default.
        assert 100 < remaining <= 110


    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()


# --------------------------------------------------------------------
# End-to-end real-contract flow via HttpxGSTSandboxClient + MockTransport
# (still no real network call -- MockTransport intercepts everything)
# --------------------------------------------------------------------

def test_httpx_client_full_authenticate_then_verify_flow(monkeypatch):
    _configure(monkeypatch)
    try:
        transport = _mock_transport(
            authenticate_response={
                "code": 200, "data": {"access_token": "fake.jwt.token"},
                "timestamp": 1750687659809, "transaction_id": "test-txn",
            },
            verify_response={
                "code": 200, "timestamp": 1775824753133,
                "data": {"data": ACTIVE_TAXPAYER, "status_cd": "1"},
                "transaction_id": "test-txn-2",
            },
        )

        def fake_post(url, headers=None, json=None, timeout=None):
            with httpx.Client(transport=transport) as c:
                return c.post(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr(httpx, "post", fake_post)

        client = HttpxGSTSandboxClient()
        taxpayer = client.verify_gstin(VALID_GSTIN)
        assert taxpayer["status"] == "Active"
        assert taxpayer["validGstin"] is True

        result = verify_gstin_externally(VALID_GSTIN, client=client)
        assert result.status == GSTVerificationStatus.API_VERIFIED_ACTIVE
    finally:
        from app.core import config as config_module
        config_module.get_settings.cache_clear()
