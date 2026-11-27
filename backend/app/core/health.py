"""Health endpoints.

- `/api/live`: the process answers. It does not touch the database, so a database outage does
  not make a host restart a healthy process over and over.
- `/api/ready`: the database answers and its schema is not behind the code.
- `/api/health`: database only, kept for the existing host and compose checks.
"""

import logging
from functools import lru_cache
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.core.errors import problem

logger = logging.getLogger("app.health")

MIGRATIONS = Path(__file__).resolve().parents[2] / "alembic"

router = APIRouter(prefix="/api", tags=["health"])


@lru_cache
def _revisions() -> tuple[str | None, frozenset[str]]:
    """The newest migration shipped with this code, and every one it knows."""
    script = ScriptDirectory(str(MIGRATIONS))
    return script.get_current_head(), frozenset(r.revision for r in script.walk_revisions())


def _schema_is_behind(db: Session) -> bool:
    current = db.scalar(text("SELECT version_num FROM alembic_version"))
    head, known = _revisions()
    # A revision this code has never heard of is newer than it: that is the previous release
    # still running (or rolled back to) after a migration, which must keep working.
    return current is None or (current != head and current in known)


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
def health(db: DbSession) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/ready", response_model=dict[str, str])
def ready(db: DbSession) -> dict[str, str] | JSONResponse:
    try:
        behind = _schema_is_behind(db)
    except SQLAlchemyError:
        logger.exception("Readiness check could not read the database")
        return problem(503, "Not ready", "The database is not available.", "database_unavailable")
    if behind:
        return problem(503, "Not ready", "Database migrations are pending.", "migrations_pending")
    return {"status": "ready"}
