"""
GST Sandbox API verification result models (Phase 6B).

These are deliberately a SEPARATE result type from Phase 6A's
GSTComplianceResult/RuleCheck -- Phase 6A's checks are 100% deterministic,
local, offline arithmetic/format rules. A GSTVerificationResult instead
describes the outcome of an attempted EXTERNAL network call, which can
fail, time out, or simply not be configured -- those are fundamentally
different kinds of "result" and keeping the types distinct makes it
impossible to accidentally treat an unreachable API as if it were a
deterministic local check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class GSTVerificationStatus(str, Enum):
    # No GSTIN was supplied at all.
    NOT_CHECKABLE = "NOT_CHECKABLE"

    # Local, offline format check failed -- the API is deliberately NOT
    # called for a structurally invalid GSTIN (no point verifying garbage).
    FORMAT_INVALID = "FORMAT_INVALID"

    # Format is valid, but no external verification was attempted or
    # possible (no client/config, or the API could not be reached). This
    # status is the load-bearing one for the project's core honesty rule:
    # format validity is NEVER, by itself, upgraded to "active"/"verified".
    FORMAT_VALID_NOT_API_VERIFIED = "FORMAT_VALID_NOT_API_VERIFIED"

    # The external API was actually called and returned a usable answer.
    API_VERIFIED_ACTIVE = "API_VERIFIED_ACTIVE"
    API_VERIFIED_INACTIVE = "API_VERIFIED_INACTIVE"       # exists but cancelled/suspended/inactive
    API_VERIFIED_NOT_FOUND = "API_VERIFIED_NOT_FOUND"      # API confirms this GSTIN does not exist

    # The external API was called but the call itself failed in some way.
    API_UNAVAILABLE = "API_UNAVAILABLE"   # not configured, or a connection/network failure
    API_TIMEOUT = "API_TIMEOUT"
    API_ERROR = "API_ERROR"               # the API responded, but with an error or an unparseable/unexpected shape


# Statuses where an external API call was genuinely attempted (used by
# callers/tests that want to distinguish "we tried and something happened"
# from "we never even attempted a call").
API_ATTEMPTED_STATUSES = frozenset({
    GSTVerificationStatus.API_VERIFIED_ACTIVE,
    GSTVerificationStatus.API_VERIFIED_INACTIVE,
    GSTVerificationStatus.API_VERIFIED_NOT_FOUND,
    GSTVerificationStatus.API_TIMEOUT,
    GSTVerificationStatus.API_ERROR,
})


@dataclass
class GSTVerificationResult:
    gstin: str | None
    status: GSTVerificationStatus
    format_valid: bool
    detail: str
    legal_name: str | None = None  # only populated when the API actually returned one
    checked_at: str = ""

    def __post_init__(self) -> None:
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """JSON-serializable representation. Deliberately contains no credentials/headers/raw API payload."""
        return {
            "gstin": self.gstin,
            "status": self.status.value,
            "format_valid": self.format_valid,
            "detail": self.detail,
            "legal_name": self.legal_name,
            "checked_at": self.checked_at,
        }
