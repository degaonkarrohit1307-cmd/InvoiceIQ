"""Tests for Alembic migration state consistency."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
# sys.executable is the interpreter currently running pytest -- it's already
# inside whatever virtualenv was activated to run the test suite, on any OS
# (Linux venv/bin/python3, Windows .venv\Scripts\python.exe, etc.). This
# avoids hard-coding any platform-specific venv layout.
PYTHON_EXECUTABLE = sys.executable


def _run_alembic(args):
    """
    Runs alembic in a subprocess against the DEV database specifically.

    conftest.py sets POSTGRES_DB=invoice_compliance_test_db in os.environ for
    the pytest process (so app/API tests hit an isolated test DB). Subprocesses
    inherit that by default, which would make these migration-state checks
    silently check the wrong database. We explicitly override POSTGRES_DB back
    to the real dev database name for this subprocess call only.
    """
    env = dict(os.environ)
    env["POSTGRES_DB"] = "invoice_compliance_db"
    cmd = [PYTHON_EXECUTABLE, "-m", "alembic"] + args
    return subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True, env=env)


def test_alembic_has_exactly_one_head():
    result = _run_alembic(["heads"])
    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.splitlines() if line.strip() and "head" in line]
    assert len(heads) == 1, f"Expected exactly one migration head, found: {heads}"


def test_dev_database_is_at_migration_head():
    """
    Confirms the DEV database (invoice_compliance_db) alembic_version matches
    the latest migration script's revision id -- i.e. no un-applied migrations.
    """
    heads_result = _run_alembic(["heads"])
    assert heads_result.returncode == 0
    head_rev = heads_result.stdout.strip().split()[0]

    current_result = _run_alembic(["current"])
    assert current_result.returncode == 0, current_result.stderr
    assert head_rev in current_result.stdout, (
        f"Dev DB is not at head. head={head_rev}, current output={current_result.stdout}"
    )


def test_model_metadata_has_no_pending_autogenerate_diff():
    """
    Runs alembic's autogenerate check by generating a throwaway revision and
    confirming it would be empty (no schema drift between ORM models and the
    real Postgres schema). Cleans up the generated file either way.
    """
    result = _run_alembic(["revision", "--autogenerate", "-m", "drift_check_tmp"])
    assert result.returncode == 0, result.stderr

    versions_dir = BACKEND_DIR / "migrations" / "versions"
    generated = sorted(versions_dir.glob("*drift_check_tmp.py"), key=lambda p: p.stat().st_mtime)
    assert generated, "Expected a new revision file to be generated"
    new_file = generated[-1]

    content = new_file.read_text()
    # Clean up immediately regardless of the assertion outcome below.
    new_file.unlink()

    assert "pass" in content and "op." not in content, (
        "Detected schema drift between ORM models and the real database: "
        f"{content}"
    )
