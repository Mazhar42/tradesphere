"""API keys. The key names its dealership so that it can be looked up under row-level security;
naming another one simply finds nothing."""

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.core.errors import ForbiddenError, RateLimitedError, UnauthorizedError
from app.core.tenant_context import set_context, tenant_id_of
from app.modules.billing import entitlements
from app.modules.integrations.models import ApiKey
from app.modules.tenancy.service import enter
from app.shared.audit import record_audit
from app.shared.dates import now_utc

SCOPES = ("files:write", "snapshots:write")
PER_MINUTE = 60
_FORM = re.compile(r"^tsk_(\d{1,18})_[A-Za-z0-9_-]{40,60}$")
_bearer = HTTPBearer(auto_error=False, scheme_name="ApiKey")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(
    session: Session, *, name: str, source_system: str, scopes: list[str], actor_id: int
) -> tuple[ApiKey, str]:
    """The key is returned once and never again."""
    entitlements.require_feature(session, "api_access")
    token = f"tsk_{tenant_id_of(session)}_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        name=name,
        source_system=source_system,
        scopes=sorted(set(scopes) & set(SCOPES)),
        hint=token[-4:],
        key_hash=_hash(token),
        created_by_id=actor_id,
    )
    session.add(key)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="api_key",
        entity_id=key.id,
        after={"name": name, "scopes": key.scopes},
    )
    return key, token


def authenticate(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> ApiKey:
    refused = UnauthorizedError("The API key is not valid.", code="invalid_api_key")
    match = _FORM.match(credentials.credentials) if credentials else None
    if credentials is None or match is None:
        raise refused
    set_context(db, tenant_id=int(match.group(1)))
    key = db.scalar(
        select(ApiKey).where(ApiKey.key_hash == _hash(credentials.credentials)).with_for_update()
    )
    if key is None or key.revoked_at is not None or key.created_by_id is None:
        raise refused
    # The person who made the key must still belong here, and the dealership be open.
    enter(db, key.created_by_id, int(match.group(1)))
    entitlements.require_feature(db, "api_access")
    entitlements.ensure_writable(db)
    now = now_utc()
    if key.window_start is None or key.window_start <= now - timedelta(minutes=1):
        key.window_start, key.window_count = now, 0
    if key.window_count >= PER_MINUTE:
        raise RateLimitedError("Too many requests. Try again later.", code="rate_limited")
    key.window_count += 1
    key.last_used_at = now
    db.commit()
    return key


KeyDep = Annotated[ApiKey, Depends(authenticate)]


def require_scope(key: ApiKey, scope: str) -> None:
    if scope not in key.scopes:
        raise ForbiddenError("This key may not do that.", code="scope_missing")
