"""Password hashing and token primitives."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.shared.dates import now_utc

_password_hash = PasswordHash.recommended()  # Argon2id
# Used to equalise timing when the username does not exist.
_DUMMY_HASH = _password_hash.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


ISSUER = "tradesphere"
AUDIENCE = "tradesphere-api"


@dataclass(frozen=True)
class AccessClaims:
    user_id: int
    tenant_id: int
    mfa_at: float | None = None  # when the second factor was last checked for this token
    support_grant: int | None = None  # set: read-only support access, not a membership


def create_access_token(user_id: int, tenant_id: int, *, mfa: bool = False) -> str:
    now = now_utc()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": tenant_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    if mfa:
        payload["mfa"] = now.timestamp()
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "sub", "type", "tid", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token expired.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid access token.", code="invalid_token") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid access token.", code="invalid_token")
    return AccessClaims(
        user_id=int(payload["sub"]),
        tenant_id=int(payload["tid"]),
        mfa_at=payload.get("mfa"),
        support_grant=payload.get("sg"),
    )


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


PLATFORM_AUDIENCE = "tradesphere-platform"
PLATFORM_MINUTES = 30


def _encode(payload: dict[str, Any], lifetime: timedelta) -> str:
    now = now_utc()
    claims = {"iss": ISSUER, "iat": now, "exp": now + lifetime, "jti": uuid.uuid4().hex, **payload}
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def create_support_token(
    operator_id: int, tenant_id: int, grant_id: int, lifetime: timedelta
) -> str:
    return _encode(
        {
            "sub": str(operator_id),
            "tid": tenant_id,
            "sg": grant_id,
            "aud": AUDIENCE,
            "type": "access",
        },
        lifetime,
    )


def create_platform_token(operator_id: int) -> str:
    """A different audience: it opens no dealership route, and no dealership token opens a
    platform route."""
    return _encode(
        {"sub": str(operator_id), "aud": PLATFORM_AUDIENCE, "type": "platform"},
        timedelta(minutes=PLATFORM_MINUTES),
    )


def decode_platform_token(token: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=PLATFORM_AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "sub", "type", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token expired.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid access token.", code="invalid_token") from exc
    if payload.get("type") != "platform":
        raise UnauthorizedError("Invalid access token.", code="invalid_token")
    return int(payload["sub"])
