"""
Application configuration.

Loads settings from environment variables / a .env file. No secrets are
hard-coded here — see .env.example for the variables this project expects.
"""
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    APP_NAME: str = "AI Powered Invoice Verification and Tax Compliance System"
    APP_ENV: str = Field(default="development")
    DEBUG: bool = Field(default=True)
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_USER: str = Field(default="invoice_app")
    POSTGRES_PASSWORD: str = Field(default="invoice_app_dev_pw")
    POSTGRES_DB: str = Field(default="invoice_compliance_db")

    # --- Database admin/bootstrap credentials (TEST INFRASTRUCTURE ONLY) ---
    # Used exclusively by tests/conftest.py to CREATE/DROP the disposable
    # test database. The application itself never uses these. Keeping this
    # separate means the app's own POSTGRES_USER (invoice_app) can remain a
    # normal, least-privilege role with no CREATEDB/SUPERUSER grant.
    # Defaults to the standard 'postgres' superuser role, which is what a
    # fresh PostgreSQL install (Windows, Linux, or the official Docker
    # image) already provides -- no extra setup required unless your
    # environment restricts it, in which case point these at any role that
    # does have CREATEDB.
    POSTGRES_ADMIN_USER: str = Field(default="postgres")
    POSTGRES_ADMIN_PASSWORD: str = Field(default="")

    # --- CORS ---
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])

    # --- File storage ---
    UPLOAD_DIR: str = Field(default="./storage/uploads")
    MAX_UPLOAD_SIZE_MB: int = Field(default=15)

    # --- Logging ---
    LOG_LEVEL: str = Field(default="INFO")

    # --- OCR (Phase 3) ---
    # Optional explicit path to the Tesseract executable. Leave unset to
    # rely on PATH. Set on Windows if Tesseract isn't on PATH, e.g.:
    #   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
    TESSERACT_CMD: str | None = Field(default=None)

    # --- GST Sandbox API (Phase 6B) ---
    # Real contract verified against developer.sandbox.co.in's published
    # OpenAPI spec (Authenticate + Verify GSTIN). Sandbox uses a two-step
    # key+secret -> JWT flow, NOT a single bearer-style API key:
    #   1. POST {base_url}/authenticate with x-api-key + x-api-secret
    #      headers -> returns a JWT access_token (valid 24h).
    #   2. POST {base_url}/gst/compliance/public/gstin/verify with
    #      `authorization: <access_token>` (NOT "Bearer <token>" -- the
    #      docs explicitly say this is not a bearer token) + x-api-key.
    # No default/hardcoded credentials or endpoint -- if any required
    # value is unset, the GST verification service reports
    # FORMAT_VALID_NOT_API_VERIFIED rather than attempting a call. Never
    # logged or included in any response.
    GST_SANDBOX_API_BASE_URL: str | None = Field(default=None)  # TEST env per docs: https://test-api.sandbox.co.in
    GST_SANDBOX_API_KEY: str | None = Field(default=None)
    GST_SANDBOX_API_SECRET: str | None = Field(default=None)
    GST_SANDBOX_API_VERSION: str = Field(default="1.0.0")
    GST_SANDBOX_API_TIMEOUT_SECONDS: float = Field(default=5.0)

    # The documented /authenticate response body does NOT include an
    # expiry field (only {"data": {"access_token": "..."}}). Sandbox's
    # own docs state in prose that "the access token has a validity of
    # 24 hours" -- that figure (not invented) is used as the default TTL
    # here. REFRESH_MARGIN_SECONDS causes the client to re-authenticate
    # slightly before the token would actually expire, rather than
    # waiting for the exact boundary.
    GST_SANDBOX_TOKEN_TTL_SECONDS: float = Field(default=86400.0)  # documented 24h
    GST_SANDBOX_TOKEN_REFRESH_MARGIN_SECONDS: float = Field(default=300.0)  # 5 min safety buffer

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Sync URL used by Alembic migrations."""
        return self.DATABASE_URL

    @property
    def ADMIN_DATABASE_URL(self) -> str:
        """
        Connects as the admin/bootstrap role to Postgres's default
        'postgres' maintenance database -- the standard connection target
        for CREATE DATABASE / DROP DATABASE statements, which cannot run
        inside a transaction against the DB being created/dropped itself.

        TEST INFRASTRUCTURE ONLY. The application does not use this.
        """
        return (
            f"postgresql+psycopg://{self.POSTGRES_ADMIN_USER}:{self.POSTGRES_ADMIN_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/postgres"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
