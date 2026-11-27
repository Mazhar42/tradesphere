"""Login and refresh-token rotation.

Refresh tokens are opaque random strings stored only as SHA-256 hashes.
Each refresh rotates the token. Re-using an already-rotated token outside a
short grace window (several tabs refreshing at once) is treated as theft:
the whole token family is revoked and the user must log in again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import client_ip_var, request_id_var
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.security import create_access_token, hash_token, new_refresh_token, verify_password
from app.modules.auth.schemas import MeRead, TokenResponse
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import AuthEvent
from app.modules.users.models import RefreshToken, User
from app.modules.users.service import get_by_username, lock_sessions
from app.shared.dates import now_utc

INVALID_CREDENTIALS = "Invalid username or password."
TOO_MANY_ATTEMPTS = "Too many failed sign-in attempts. Try again in a few minutes."


def record_event(
    session: Session, action: str, *, user_id: int | None, identifier: str | None = None
) -> None:
    session.add(
        AuthEvent(
            user_id=user_id,
            identifier=identifier,
            action=action,
            ip=client_ip_var.get(),
            request_id=request_id_var.get(),
        )
    )


def _recent_failures(session: Session, *, username: str | None, ip: str | None) -> int:
    since = now_utc() - timedelta(minutes=settings.login_window_minutes)
    conditions = [AuthEvent.action == "login_failed", AuthEvent.occurred_at >= since]
    if username is not None:
        conditions.append(AuthEvent.identifier == username.lower())
    else:
        conditions.append(AuthEvent.ip == ip)
    return int(session.scalar(select(func.count(AuthEvent.id)).where(*conditions)) or 0)


def _sign_in_blocked(session: Session, username: str, ip: str | None) -> bool:
    """Counts come from the database, so every worker and every restart sees the same limit."""
    if (
        _recent_failures(session, username=username, ip=None)
        >= settings.login_max_failures_per_user
    ):
        return True
    return ip is not None and (
        _recent_failures(session, username=None, ip=ip) >= settings.login_max_failures_per_ip
    )


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


def _token_response(session: Session, user: User, tenant_id: int) -> TokenResponse:
    tenancy.enter(session, user.id, tenant_id)
    session.expire(user, ["roles"])
    return TokenResponse(
        access_token=create_access_token(user.id, tenant_id),
        expires_in=settings.access_token_minutes * 60,
        user=me_from_user(user),
    )


def _default_tenant(session: Session, user: User) -> int | None:
    memberships = tenancy.memberships_of(session, user.id)
    return memberships[0][0].id if memberships else None


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
    if _sign_in_blocked(session, username, ip):
        raise RateLimitedError(TOO_MANY_ATTEMPTS, code="login_rate_limited")
    user = get_by_username(session, username)
    valid = verify_password(password, user.password_hash if user else None)
    tenant_id = None
    if user is not None and valid and user.is_active:
        tenant_id = _default_tenant(session, user)
    if user is None or tenant_id is None:
        record_event(
            session,
            "login_failed",
            user_id=user.id if user else None,
            identifier=username.lower(),
        )
        session.commit()  # keep the failed-attempt record even though we raise
        raise UnauthorizedError(INVALID_CREDENTIALS, code="invalid_credentials")

    user.last_login_at = now_utc()
    _, raw = _issue_refresh_token(session, user, str(uuid.uuid4()), user_agent, ip)
    record_event(session, "login", user_id=user.id, identifier=user.username)
    return AuthResult(_token_response(session, user, tenant_id), raw)


def refresh(
    session: Session, raw_token: str | None, user_agent: str | None, ip: str | None
) -> AuthResult:
    if not raw_token:
        raise UnauthorizedError("No refresh token.", code="no_refresh_token")
    by_hash = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    user_id = session.scalar(by_hash.with_only_columns(RefreshToken.user_id))
    token = None
    if user_id is not None:
        lock_sessions(session, user_id, ending=False)
        token = session.scalar(by_hash.with_for_update().execution_options(populate_existing=True))
    now = now_utc()
    if token is None or token.expires_at <= now:
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    user = session.get(User, token.user_id)
    tenant_id = _default_tenant(session, user) if user is not None and user.is_active else None
    if user is None or tenant_id is None:
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    if token.revoked_at is not None:
        within_grace = (
            token.replaced_by_id is not None
            and (now - token.revoked_at).total_seconds() <= settings.refresh_reuse_grace_seconds
        )
        if within_grace:
            # Concurrent refresh (e.g. two tabs): the browser already holds the
            # newer cookie, so hand out an access token without rotating again.
            # Only while the session is still open: not after a logout or password reset.
            if not _family_is_alive(session, token.family_id, now):
                raise UnauthorizedError(
                    "Session expired. Please log in again.", code="session_expired"
                )
            return AuthResult(_token_response(session, user, tenant_id), None)
        _revoke_family(session, token.family_id, now)
        record_event(session, "refresh_reuse", user_id=user.id)
        session.commit()
        raise UnauthorizedError("Session expired. Please log in again.", code="session_expired")

    new_token, raw = _issue_refresh_token(session, user, token.family_id, user_agent, ip)
    token.revoked_at = now
    token.replaced_by_id = new_token.id
    return AuthResult(_token_response(session, user, tenant_id), raw)


def logout(session: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    token = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if token is None:
        return
    lock_sessions(session, token.user_id, ending=True)
    _revoke_family(session, token.family_id, now_utc())
    record_event(session, "logout", user_id=token.user_id)


def _family_is_alive(session: Session, family_id: str, now: datetime) -> bool:
    live = select(RefreshToken.id).where(
        RefreshToken.family_id == family_id,
        RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > now,
    )
    return session.scalar(live.limit(1)) is not None


def _revoke_family(session: Session, family_id: str, now: object) -> None:
    tokens = session.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for t in tokens:
        t.revoked_at = now  # type: ignore[assignment]
