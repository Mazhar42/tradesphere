"""Joining a dealership, proving an email address and recovering a password.

One-time tokens are random, stored only as SHA-256 hashes, single use and short lived.
"""

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from app.core.permissions import CurrentUser
from app.core.security import hash_password, hash_token, new_refresh_token, verify_password
from app.core.tenant_context import set_context, tenant_id_of
from app.modules.auth.service import record_event
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import AuthEvent, Tenant, TenantInvitation, TenantMembership
from app.modules.users import service as users
from app.modules.users.models import User, UserToken
from app.shared import email
from app.shared.audit import record_audit
from app.shared.business import use_business
from app.shared.dates import now_utc

INVITATION_HOURS = 72
VERIFY_HOURS = 24
RESET_MINUTES = 30
INVALID_LINK = "This link is not valid any more. Ask for a new one."


def _link(path: str, token: str) -> str:
    # The token goes after '#', which browsers do not send to servers or put in referrers.
    return f"{settings.public_url.rstrip('/')}{path}#token={token}"


# --- Invitations ---------------------------------------------------------------------------------


def invite(
    session: Session,
    *,
    email_address: str | None,
    note: str | None,
    roles: list[str],
    actor: CurrentUser,
) -> tuple[TenantInvitation, str | None]:
    """Returns the invitation and, only when there is no email address, the link to share.

    An emailed link is never shown to the inviter: accepting it is what proves the address.
    """
    users.ensure_may_grant(session, actor, roles)
    if email_address and not email.can_send():
        raise BusinessRuleError(
            "Email is not set up here. Leave the address empty and share the link instead.",
            code="email_not_configured",
            errors=[{"field": "email", "message": "Cannot send email"}],
        )
    secret = new_refresh_token()
    invitation = TenantInvitation(
        email=email_address,
        note=note,
        roles=sorted(set(roles)),
        token_hash=hash_token(secret),
        expires_at=now_utc() + timedelta(hours=INVITATION_HOURS),
        created_by_id=actor.id,
    )
    session.add(invitation)
    session.flush()
    # The dealership is part of the token: it is needed before the row can be read.
    link = _link("/accept-invitation", f"{tenant_id_of(session)}.{secret}")
    record_audit(
        session,
        actor_id=actor.id,
        action="invite",
        entity_type="invitation",
        entity_id=invitation.id,
        after={"email": email_address, "roles": invitation.roles},
    )
    if email_address:
        tenant = session.get(Tenant, tenant_id_of(session))
        assert tenant is not None
        email.deliver(
            email.Message(
                email_address,
                f"Join {tenant.legal_name} on TradeSphere",
                f"You have been invited to {tenant.legal_name}.\n"
                f"Open this link within {INVITATION_HOURS} hours:\n{link}\n",
            )
        )
        return invitation, None
    return invitation, link


def list_invitations(session: Session) -> Sequence[TenantInvitation]:
    return session.scalars(
        select(TenantInvitation).order_by(TenantInvitation.id.desc()).limit(100)
    ).all()


def revoke_invitation(session: Session, invitation_id: int, actor: CurrentUser) -> None:
    invitation = session.scalar(
        select(TenantInvitation).where(TenantInvitation.id == invitation_id).with_for_update()
    )
    if invitation is None:
        raise NotFoundError("Invitation not found.")
    if invitation.accepted_at is not None:
        raise ConflictError("This invitation was already accepted.", code="already_accepted")
    invitation.revoked_at = invitation.revoked_at or now_utc()
    record_audit(
        session,
        actor_id=actor.id,
        action="revoke",
        entity_type="invitation",
        entity_id=invitation.id,
    )


def open_invitation(session: Session, token: str) -> tuple[TenantInvitation, Tenant]:
    """Find a usable invitation and make its dealership the context. Locks the row."""
    tenant_part, _, secret = token.partition(".")
    invalid = NotFoundError(INVALID_LINK, code="invalid_link")
    if not tenant_part.isdigit() or not secret:
        raise invalid
    set_context(session, tenant_id=int(tenant_part))
    invitation = session.scalar(
        select(TenantInvitation)
        .where(TenantInvitation.token_hash == hash_token(secret))
        .with_for_update()
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now_utc()
    ):
        raise invalid
    tenant = session.get(Tenant, invitation.tenant_id)
    if tenant is None or tenant.status in tenancy.CLOSED_STATUSES:
        raise invalid
    use_business(tenancy.business_config(tenant))
    return invitation, tenant


def accept_invitation(
    session: Session, *, token: str, username: str, full_name: str | None, password: str
) -> User:
    """With a new username: create the account. With an existing one: its password must be
    given, and the account joins without this dealership gaining control over it."""
    invitation, _ = open_invitation(session, token)
    existing = users.get_by_username(session, username)
    if existing is not None:
        if not existing.is_active or not verify_password(password, existing.password_hash):
            raise UnauthorizedError("Invalid username or password.", code="invalid_credentials")
        set_context(session, user_id=existing.id)
        if session.get(TenantMembership, (invitation.tenant_id, existing.id)):
            raise ConflictError("This account already belongs here.", code="already_member")
        user = existing
        tenancy.add_member(session, user.id, managed=False, invited_by=invitation.created_by_id)
    else:
        if not full_name:
            raise BusinessRuleError(
                "Enter your name.", errors=[{"field": "full_name", "message": "Required"}]
            )
        users.check_password(password, privileged="owner" in invitation.roles)
        if invitation.email and users.get_by_email(session, invitation.email):
            raise ConflictError(
                "An account with this email exists. Accept with its username and password.",
                code="account_exists",
            )
        user = User(
            username=username,
            full_name=full_name,
            password_hash=hash_password(password),
            email=invitation.email,
            email_verified_at=now_utc() if invitation.email else None,
        )
        session.add(user)
        session.flush()
        tenancy.add_member(session, user.id, managed=True, invited_by=invitation.created_by_id)
        set_context(session, user_id=user.id)
    users.assign_roles(session, user, invitation.roles)
    invitation.accepted_at = now_utc()
    invitation.accepted_by_id = user.id
    record_audit(
        session,
        actor_id=user.id,
        action="accept",
        entity_type="invitation",
        entity_id=invitation.id,
    )
    record_event(session, "invitation_accepted", user_id=user.id, identifier=user.username)
    return user


# --- One-time tokens for a person ----------------------------------------------------------------


def _issue(
    session: Session, user: User, purpose: str, lifetime: timedelta, address: str | None = None
) -> str:
    secret = new_refresh_token()
    session.add(
        UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(secret),
            email=address,
            expires_at=now_utc() + lifetime,
        )
    )
    session.flush()
    return secret


def _redeem(session: Session, purpose: str, secret: str) -> UserToken:
    token = session.scalar(
        select(UserToken)
        .where(UserToken.token_hash == hash_token(secret), UserToken.purpose == purpose)
        .with_for_update()
    )
    if token is None or token.used_at is not None or token.expires_at <= now_utc():
        raise NotFoundError(INVALID_LINK, code="invalid_link")
    token.used_at = now_utc()
    return token


def _too_many(session: Session, action: str, identifier: str | None, ip: str | None) -> bool:
    since = now_utc() - timedelta(minutes=settings.login_window_minutes)
    base = select(func.count(AuthEvent.id)).where(
        AuthEvent.action == action, AuthEvent.occurred_at >= since
    )
    by_name = session.scalar(base.where(AuthEvent.identifier == identifier)) or 0
    by_ip = (session.scalar(base.where(AuthEvent.ip == ip)) or 0) if ip else 0
    return (
        by_name >= settings.login_max_failures_per_user
        or by_ip >= settings.login_max_failures_per_ip
    )


def request_password_reset(session: Session, identifier: str, ip: str | None) -> None:
    """Always succeeds from the caller's view, whether or not the account exists."""
    identifier = identifier.strip().lower()
    if _too_many(session, "reset_requested", identifier, ip):
        raise RateLimitedError("Too many requests. Try again later.", code="rate_limited")
    user = users.get_by_username(session, identifier) or users.get_by_email(session, identifier)
    record_event(
        session, "reset_requested", user_id=user.id if user else None, identifier=identifier
    )
    if user is None or not user.is_active or not user.email or not user.email_verified_at:
        return
    if not email.can_send():
        return
    secret = _issue(session, user, "reset_password", timedelta(minutes=RESET_MINUTES))
    email.deliver(
        email.Message(
            user.email,
            "Reset your TradeSphere password",
            f"Open this link within {RESET_MINUTES} minutes:\n{_link('/reset-password', secret)}\n"
            "If you did not ask for this, ignore this message.\n",
        )
    )


def reset_password_with_token(session: Session, secret: str, new_password: str) -> None:
    token = _redeem(session, "reset_password", secret)
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise NotFoundError(INVALID_LINK, code="invalid_link")
    users.set_password(session, user, new_password)
    record_event(session, "password_reset", user_id=user.id)


def change_own_password(
    session: Session, user_id: int, current_password: str, new_password: str
) -> None:
    user = session.get(User, user_id)
    assert user is not None
    if not verify_password(current_password, user.password_hash):
        raise BusinessRuleError(
            "The current password is wrong.",
            code="wrong_password",
            errors=[{"field": "current_password", "message": "Wrong password"}],
        )
    users.set_password(session, user, new_password)
    record_event(session, "password_changed", user_id=user.id)


def set_own_email(session: Session, user_id: int, address: str, password: str) -> None:
    """The address is used for recovery only once its owner has opened the link sent to it."""
    user = session.get(User, user_id)
    assert user is not None
    if not verify_password(password, user.password_hash):
        raise BusinessRuleError(
            "The password is wrong.",
            code="wrong_password",
            errors=[{"field": "password", "message": "Wrong password"}],
        )
    if not email.can_send():
        raise BusinessRuleError("Email is not set up here.", code="email_not_configured")
    address = address.strip().lower()
    holder = users.get_by_email(session, address)
    if holder is not None and holder.id != user.id:
        raise ConflictError(
            "This address is used by another account.",
            code="duplicate",
            errors=[{"field": "email", "message": "Already exists"}],
        )
    secret = _issue(session, user, "verify_email", timedelta(hours=VERIFY_HOURS), address)
    email.deliver(
        email.Message(
            address,
            "Confirm your email address",
            f"Open this link within {VERIFY_HOURS} hours:\n{_link('/verify-email', secret)}\n",
        )
    )


def verify_email(session: Session, secret: str) -> None:
    token = _redeem(session, "verify_email", secret)
    user = session.get(User, token.user_id)
    assert user is not None and token.email is not None
    holder = users.get_by_email(session, token.email)
    if holder is not None and holder.id != user.id:
        raise ConflictError("This address is used by another account.", code="duplicate")
    user.email = token.email
    user.email_verified_at = now_utc()
    record_event(session, "email_verified", user_id=user.id)
