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


def create_access_token(user_id: int, tenant_id: int) -> str:
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
    return AccessClaims(user_id=int(payload["sub"]), tenant_id=int(payload["tid"]))


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
