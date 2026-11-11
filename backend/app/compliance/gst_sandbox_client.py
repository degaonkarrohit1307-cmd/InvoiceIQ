"""
GST Sandbox API client (Phase 6B).

Contract verified against Sandbox's published OpenAPI specs at
developer.sandbox.co.in (fetched live, not invented):
  - Authenticate:   POST {base_url}/authenticate
                     headers: x-api-key, x-api-secret, x-api-version
                     -> {"code":200,"data":{"access_token": "<jwt>"}, ...}
                     Token validity: 24h. NOT a bearer token -- passed in
                     the `authorization` header WITHOUT a "Bearer " prefix
                     (this is explicit in Sandbox's docs).
  - Verify GSTIN:   POST {base_url}/gst/compliance/public/gstin/verify
                     headers: authorization (the JWT above), x-api-key,
                     x-api-version
                     body: {"gstin": "<gstin>"}
                     200 -> {"code":200,"data":{"data":{legalName,
                             bussNature, stateName, validGstin, stateCode,
                             pan, gstin, regStartDate, status}, "status_cd":
                             "1"}, "transaction_id": "..."}
                     422 -> {"code":422,"message":"Invalid GSTIN pattern", ...}

Servers per the OpenAPI spec: test = https://test-api.sandbox.co.in,
prod = https://api.sandbox.co.in. This project targets the TEST
environment -- GST_SANDBOX_API_BASE_URL should be set to the test host;
nothing here hardcodes either URL.

The documented contract does NOT define a "GSTIN not found" response
shape (only 200-success and 422-invalid-pattern are documented) -- this
client does not invent one. An undocumented status code is surfaced as
GSTSandboxResponseError rather than mapped to a guessed meaning.

`GSTSandboxClient` is a Protocol (structural interface): any object with
a matching `verify_gstin(gstin: str) -> dict` method satisfies it, so
tests can inject a fake/mock client with no real network dependency and
no real credentials. `HttpxGSTSandboxClient` is the one real
implementation, used only when credentials are actually configured.

SECURITY: the API key/secret and the JWT obtained from them are used only
to build outgoing request headers -- never logged, never included in an
exception message, and never present in any value this module returns.
"""
from __future__ import annotations

import time
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class GSTSandboxClientError(Exception):
    """Base class for all GST Sandbox API client errors."""


class GSTSandboxNotConfiguredError(GSTSandboxClientError):
    """Raised when base URL, API key, and/or API secret are not set in configuration."""


class GSTSandboxTimeoutError(GSTSandboxClientError):
    """Raised when an API call exceeds the configured timeout."""


class GSTSandboxConnectionError(GSTSandboxClientError):
    """Raised for network-level failures (DNS, connection refused, TLS, etc.)."""


class GSTSandboxResponseError(GSTSandboxClientError):
    """
    Raised when the API responds, but with an error status (including the
    documented 422 invalid-GSTIN-pattern case), an authentication failure,
    or an unparseable/unexpected body shape.
    """


class GSTSandboxClient(Protocol):
    """Structural interface every GST Sandbox client (real or fake) implements."""

    def verify_gstin(self, gstin: str) -> dict:
        """
        Returns the taxpayer detail dict for a single GSTIN, matching the
        real API's `data.data` shape: {"legalName", "bussNature",
        "stateName", "validGstin", "stateCode", "pan", "gstin",
        "regStartDate", "status"}.
        Raises GSTSandboxTimeoutError, GSTSandboxConnectionError, or
        GSTSandboxResponseError on failure. Never returns credentials.
        """
        ...


class HttpxGSTSandboxClient:
    """Real GST Sandbox API client, built from application configuration."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 api_secret: str | None = None, timeout: float | None = None, api_version: str | None = None,
                 token_ttl_seconds: float | None = None, token_refresh_margin_seconds: float | None = None):
        settings = get_settings()
        self._base_url = base_url if base_url is not None else settings.GST_SANDBOX_API_BASE_URL
        self._api_key = api_key if api_key is not None else settings.GST_SANDBOX_API_KEY
        self._api_secret = api_secret if api_secret is not None else settings.GST_SANDBOX_API_SECRET
        self._timeout = timeout if timeout is not None else settings.GST_SANDBOX_API_TIMEOUT_SECONDS
        self._api_version = api_version if api_version is not None else settings.GST_SANDBOX_API_VERSION
        self._default_token_ttl_seconds = (
            token_ttl_seconds if token_ttl_seconds is not None else settings.GST_SANDBOX_TOKEN_TTL_SECONDS
        )
        self._token_refresh_margin_seconds = (
            token_refresh_margin_seconds if token_refresh_margin_seconds is not None
            else settings.GST_SANDBOX_TOKEN_REFRESH_MARGIN_SECONDS
        )
        # In-memory token cache, expiry-aware (see _authenticate()).
        # `_token_expires_at` is a time.monotonic() timestamp, not wall-clock,
        # so it's immune to system clock adjustments.
        self._cached_access_token: str | None = None
        self._token_expires_at: float | None = None

    def _token_is_valid(self) -> bool:
        return (
            self._cached_access_token is not None
            and self._token_expires_at is not None
            and time.monotonic() < self._token_expires_at
        )

    def _require_configured(self) -> tuple[str, str, str]:
        if not self._base_url or not self._api_key or not self._api_secret:
            raise GSTSandboxNotConfiguredError(
                "GST Sandbox API is not configured "
                "(GST_SANDBOX_API_BASE_URL / GST_SANDBOX_API_KEY / GST_SANDBOX_API_SECRET)."
            )
        return self._base_url, self._api_key, self._api_secret

    def _authenticate(self) -> str:
        if self._token_is_valid():
            return self._cached_access_token

        base_url, api_key, api_secret = self._require_configured()

        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/authenticate",
                headers={"x-api-key": api_key, "x-api-secret": api_secret, "x-api-version": self._api_version},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise GSTSandboxTimeoutError("The GST Sandbox authenticate call did not respond in time.") from exc
        except httpx.ConnectError as exc:
            raise GSTSandboxConnectionError("Could not connect to the GST Sandbox authenticate endpoint.") from exc
        except httpx.HTTPError as exc:
            logger.warning("GST Sandbox authenticate call failed: %s", type(exc).__name__)
            raise GSTSandboxConnectionError("The GST Sandbox authenticate request failed.") from exc

        if response.status_code in (401, 403):
            raise GSTSandboxResponseError("GST Sandbox API rejected the provided credentials.")
        if response.status_code >= 400:
            raise GSTSandboxResponseError(f"GST Sandbox authenticate call failed (HTTP {response.status_code}).")

        try:
            body = response.json()
            data = body["data"]
            access_token = data["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GSTSandboxResponseError("GST Sandbox authenticate response was missing the access token.") from exc

        # The documented response shape is just {"access_token": "..."} --
        # no expiry field. If Sandbox ever adds one (common convention:
        # "expires_in", in seconds), use it; this is a defensive check,
        # not an assumption that it currently exists. Otherwise fall back
        # to the documented-in-prose 24h validity (configured TTL), minus
        # a safety margin so we refresh slightly early rather than risking
        # a request landing right at the boundary.
        reported_ttl = data.get("expires_in") if isinstance(data, dict) else None
        ttl_seconds = float(reported_ttl) if isinstance(reported_ttl, (int, float)) else self._default_token_ttl_seconds
        usable_ttl = max(ttl_seconds - self._token_refresh_margin_seconds, 0.0)

        self._cached_access_token = access_token
        self._token_expires_at = time.monotonic() + usable_ttl
        return access_token

    def verify_gstin(self, gstin: str) -> dict:
        base_url, api_key, _ = self._require_configured()
        access_token = self._authenticate()

        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/gst/compliance/public/gstin/verify",
                headers={
                    # Per Sandbox's docs: NOT a bearer token -- no "Bearer " prefix.
                    "authorization": access_token,
                    "x-api-key": api_key,
                    "x-api-version": self._api_version,
                    "Content-Type": "application/json",
                },
                json={"gstin": gstin},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise GSTSandboxTimeoutError("The GST Sandbox verify-GSTIN call did not respond in time.") from exc
        except httpx.ConnectError as exc:
            raise GSTSandboxConnectionError("Could not connect to the GST Sandbox verify-GSTIN endpoint.") from exc
        except httpx.HTTPError as exc:
            logger.warning("GST Sandbox verify-GSTIN call failed: %s", type(exc).__name__)
            raise GSTSandboxConnectionError("The GST Sandbox verify-GSTIN request failed.") from exc

        if response.status_code in (401, 403):
            raise GSTSandboxResponseError("GST Sandbox API rejected the request credentials/token.")
        if response.status_code == 422:
            # Documented response for a GSTIN the API considers structurally
            # invalid. We already ran our own local format check before
            # ever reaching here, so this is surfaced as an API-level
            # error (a disagreement between local and remote validation),
            # not silently reinterpreted.
            raise GSTSandboxResponseError("GST Sandbox API reported an invalid GSTIN pattern.")
        if response.status_code >= 500:
            raise GSTSandboxResponseError(f"GST Sandbox API returned a server error (HTTP {response.status_code}).")
        if response.status_code >= 400:
            # Any other 4xx isn't documented for this endpoint -- don't guess its meaning.
            raise GSTSandboxResponseError(f"GST Sandbox API returned an undocumented error (HTTP {response.status_code}).")

        try:
            body = response.json()
            taxpayer_data = body["data"]["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GSTSandboxResponseError("GST Sandbox API response did not match the documented shape.") from exc

        if not isinstance(taxpayer_data, dict) or "status" not in taxpayer_data or "validGstin" not in taxpayer_data:
            raise GSTSandboxResponseError("GST Sandbox API response was missing expected taxpayer fields.")

        return taxpayer_data
