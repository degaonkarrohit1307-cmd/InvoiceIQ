"""
GST Sandbox external verification service (Phase 6B).

Flow:
    1. Local structural format check FIRST (reuses Phase 6A's
       is_valid_gstin_format -- not duplicated here). A format-invalid
       GSTIN is rejected immediately WITHOUT calling the external API --
       there's no reason to burn an API call on something that can't be a
       valid GSTIN no matter what the API says.
    2. Only a format-valid GSTIN proceeds to an external API call attempt.
    3. If no client is configured/available, the result is
       FORMAT_VALID_NOT_API_VERIFIED -- explicitly NOT treated as
       "verified active". This is the core honesty rule of this module.

The `client` parameter uses dependency injection (any object satisfying
the GSTSandboxClient protocol) specifically so tests never need real
network access or real credentials.
"""
from __future__ import annotations

from app.compliance.gst_sandbox_client import (
    GSTSandboxClient,
    GSTSandboxClientError,
    GSTSandboxConnectionError,
    GSTSandboxNotConfiguredError,
    GSTSandboxResponseError,
    GSTSandboxTimeoutError,
    HttpxGSTSandboxClient,
)
from app.compliance.gst_sandbox_models import GSTVerificationResult, GSTVerificationStatus
from app.core.logging_config import get_logger
from app.services.normalization import is_valid_gstin_format, normalize_gstin

logger = get_logger(__name__)


def verify_gstin_externally(gstin: str | None, client: GSTSandboxClient | None = None) -> GSTVerificationResult:
    """
    Verifies a single GSTIN: local format check, then (if format-valid and
    a client is available) an external API lookup.

    `client`: pass an injected/mock client for testing. If None, a
    HttpxGSTSandboxClient built from application settings is used --
    which itself cleanly reports GSTSandboxNotConfiguredError (caught
    here, converted to FORMAT_VALID_NOT_API_VERIFIED) when no API
    credentials are configured, rather than making a doomed network call.
    """
    if gstin is None:
        return GSTVerificationResult(
            gstin=None, status=GSTVerificationStatus.NOT_CHECKABLE,
            format_valid=False, detail="No GSTIN was provided.",
        )

    normalized = normalize_gstin(gstin)

    if not is_valid_gstin_format(normalized):
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.FORMAT_INVALID, format_valid=False,
            detail="GSTIN does not match the expected structural format. The external API was not called.",
        )

    active_client = client if client is not None else HttpxGSTSandboxClient()

    try:
        response = active_client.verify_gstin(normalized)
    except GSTSandboxNotConfiguredError:
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.FORMAT_VALID_NOT_API_VERIFIED, format_valid=True,
            detail=(
                "GSTIN format is valid, but the GST Sandbox API is not configured, so this has NOT been "
                "verified as a real/active/registered GSTIN."
            ),
        )
    except GSTSandboxTimeoutError:
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.API_TIMEOUT, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API did not respond in time.",
        )
    except GSTSandboxConnectionError:
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.API_UNAVAILABLE, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API could not be reached.",
        )
    except GSTSandboxResponseError as exc:
        logger.warning("GST Sandbox API returned an unusable response: %s", exc)
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.API_ERROR, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API response could not be interpreted.",
        )
    except GSTSandboxClientError as exc:  # any other client error we didn't anticipate above
        logger.warning("GST Sandbox API call failed: %s", type(exc).__name__)
        return GSTVerificationResult(
            gstin=normalized, status=GSTVerificationStatus.API_UNAVAILABLE, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API call failed.",
        )

    return _interpret_api_response(normalized, response)


def _interpret_api_response(gstin: str, response: dict) -> GSTVerificationResult:
    """
    Interprets the REAL Sandbox API taxpayer-detail shape:
        {"legalName", "bussNature", "stateName", "validGstin" (bool),
         "stateCode", "pan", "gstin", "regStartDate", "status" (str)}
    `status` examples seen in Sandbox's own docs: "Active",
    "Cancelled on application of Taxpayer". There is no separate
    boolean "active" field in the real response -- we derive it from
    `status == "Active"`.

    The documented contract has no explicit "GSTIN not found" response
    shape. `validGstin: false` is the closest documented signal for
    "the API examined this and says it is not a valid/registered GSTIN",
    so that (documented) field -- not an invented one -- is what maps to
    API_VERIFIED_NOT_FOUND here.
    """
    if not isinstance(response, dict) or "status" not in response or "validGstin" not in response:
        return GSTVerificationResult(
            gstin=gstin, status=GSTVerificationStatus.API_ERROR, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API response was malformed.",
        )

    legal_name = response.get("legalName")

    if not response["validGstin"]:
        return GSTVerificationResult(
            gstin=gstin, status=GSTVerificationStatus.API_VERIFIED_NOT_FOUND, format_valid=True,
            detail="GSTIN format is valid, but the GST Sandbox API reports validGstin=false for this GSTIN.",
            legal_name=legal_name,
        )

    status_text = str(response["status"])
    if status_text.strip().lower() == "active":
        return GSTVerificationResult(
            gstin=gstin, status=GSTVerificationStatus.API_VERIFIED_ACTIVE, format_valid=True,
            detail="GST Sandbox API confirms this GSTIN is registered and active.",
            legal_name=legal_name,
        )

    return GSTVerificationResult(
        gstin=gstin, status=GSTVerificationStatus.API_VERIFIED_INACTIVE, format_valid=True,
        detail=f"GST Sandbox API reports this GSTIN's registration status as: {status_text}.",
        legal_name=legal_name,
    )
