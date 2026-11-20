"""Login and refresh-token rotation.

Refresh tokens are opaque random strings stored only as SHA-256 hashes.
Each refresh rotates the token. Re-using an already-rotated token outside a
short grace window (several tabs refreshing at once) is treated as theft:
the whole token family is revoked and the user must log in again.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import create_access_token, hash_token, new_refresh_token, verify_password
from app.modules.auth.schemas import MeRead, TokenResponse
from app.modules.users.models import RefreshToken, User
from app.modules.users.service import get_by_username
from app.shared.audit import record_audit
from app.shared.dates import now_utc

INVALID_CREDENTIALS = "Invalid username or password."


@dataclass
class AuthResult:
    response: TokenResponse
    refresh_token: str | None  # None => keep the cookie the browser already has


def me_from_user(user: User) -> MeRead:
    return MeRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        roles=user.role_codes,
        permissions=sorted(user.permission_codes),
    )


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_minutes * 60,
        user=me_from_user(user),
    )


def _issue_refresh_token(
    session: Session, user: User, family_id: str, user_agent: str | None, ip: str | None
) -> tuple[RefreshToken, str]:
    raw = new_refresh_token()
    token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        family_id=family_id,
        expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
        user_agent=(user_agent or "")[:300] or None,
        ip=ip,
    )
    session.add(token)
    session.flush()
    return token, raw


def login(
    session: Session, username: str, password: str, user_agent: str | None, ip: str | None
) -> AuthResult:
    user = get_by_username(session, username)
    valid = verify_password(password, user.password_hash if user else None)
    if user is None or not valid or not user.is_active:
        record_audit(
            session,
            actor_id=user.id if user else None,
            action="login_failed",
            entity_type="auth",
            after={"username": username.lower()},
        )
        session.commit()  # keep the failed-attempt record even though we raise
        raise UnauthorizedError(INVALID_CREDENTIALS, code="invalid_credentials")

    user.last_login_at = now_utc()
    _, raw = _issue_refresh_token(session, user, str(uuid.uuid4()), user_agent, ip)
    record_audit(session, actor_id=user.id, action="login", entity_type="auth", entity_id=user.id)
    return AuthResult(_token_response(user), raw)


def refresh(
    session: Session, raw_token: str | None, user_agent: str | None, ip: str | None
) -> AuthResult:
    if not raw_token:
        raise UnauthorizedError("No refresh token.", code="no_refresh_token")
    token = session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw_token))
        .with_for_update()
    )
    now = now_utc()
    if token is None or token.expires_at <= now:
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    if token.revoked_at is not None:
        within_grace = (
            token.replaced_by_id is not None
            and (now - token.revoked_at).total_seconds() <= settings.refresh_reuse_grace_seconds
        )
        if within_grace:
            # Concurrent refresh (e.g. two tabs): the browser already holds the
            # newer cookie, so hand out an access token without rotating again.
            return AuthResult(_token_response(user), None)
        _revoke_family(session, token.family_id, now)
        record_audit(
            session, actor_id=user.id, action="refresh_reuse", entity_type="auth", entity_id=user.id
        )
        session.commit()
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    new_token, raw = _issue_refresh_token(session, user, token.family_id, user_agent, ip)
    token.revoked_at = now
    token.replaced_by_id = new_token.id
    return AuthResult(_token_response(user), raw)


def logout(session: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    token = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if token is None:
        return
    _revoke_family(session, token.family_id, now_utc())
    record_audit(
        session,
        actor_id=token.user_id,
        action="logout",
        entity_type="auth",
        entity_id=token.user_id,
    )


def _revoke_family(session: Session, family_id: str, now: object) -> None:
    tokens = session.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for t in tokens:
        t.revoked_at = now  # type: ignore[assignment]
