"""Tests for health, readiness, and PostgreSQL connectivity/read-write."""
from app.models.system import HealthCheckLog


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["service"] == "AI Powered Invoice Verification and Tax Compliance System"


def test_liveness_does_not_touch_db(client):
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "check": "liveness"}


def test_readiness_reports_db_connected(client):
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert "last_check_id" in body


def test_readiness_actually_writes_a_row_to_postgres(client, db_session):
    before = db_session.query(HealthCheckLog).count()
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200

    # Re-query using a fresh query (same session, but row was committed by the
    # endpoint's own session which is the same overridden session here).
    after = db_session.query(HealthCheckLog).count()
    assert after == before + 1


def test_readiness_counter_increments_across_multiple_calls(client):
    first = client.get("/api/v1/health/ready").json()["total_recorded_checks"]
    second = client.get("/api/v1/health/ready").json()["total_recorded_checks"]
    third = client.get("/api/v1/health/ready").json()["total_recorded_checks"]
    assert second == first + 1
    assert third == second + 1


def test_direct_db_write_and_read_round_trip(db_session):
    """Bypasses the API entirely: writes an ORM object straight to Postgres and reads it back."""
    record = HealthCheckLog(status="direct_test")
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)

    fetched = db_session.query(HealthCheckLog).filter_by(id=record.id).one()
    assert fetched.status == "direct_test"
    assert fetched.checked_at is not None
