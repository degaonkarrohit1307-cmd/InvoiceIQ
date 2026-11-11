"""Tests for configuration/environment loading."""
from app.core.config import Settings, get_settings


def test_settings_load_defaults():
    s = Settings(_env_file=None)
    assert s.APP_NAME == "AI Powered Invoice Verification and Tax Compliance System"
    assert s.API_V1_PREFIX == "/api/v1"


def test_database_url_is_well_formed():
    s = get_settings()
    url = s.DATABASE_URL
    assert url.startswith("postgresql+psycopg://")
    assert s.POSTGRES_DB in url
    assert s.POSTGRES_USER in url


def test_get_settings_is_cached():
    # lru_cache means repeated calls return the same instance
    assert get_settings() is get_settings()
