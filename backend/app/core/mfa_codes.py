"""Time-based one-time codes (RFC 6238), recovery codes and secret encryption.

The authenticator secret is encrypted with a key that is not in the database, so a leaked
backup does not hand out second factors.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

from cryptography.fernet import Fernet

from app.core.config import settings

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # steps of clock drift accepted either way
RECOVERY_CODES = 10


def _fernet() -> Fernet:
    key = settings.mfa_encryption_key or settings.jwt_secret  # fallback: development only
    digest = hashlib.sha256(b"mfa:" + key.get_secret_value().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**DIGITS:0{DIGITS}d}"


def step_of(at: datetime) -> int:
    return int(at.timestamp()) // STEP_SECONDS


def matching_step(secret: str, code: str, at: datetime, *, after: int) -> int | None:
    """The time step `code` belongs to, if acceptable. `after` is the last step used: a code
    works once, even within its own 30 seconds."""
    now = step_of(at)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if step > after and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def setup_uri(secret: str, account: str) -> str:
    return f"otpauth://totp/TradeSphere:{quote(account)}?secret={secret}&issuer=TradeSphere"


def new_recovery_codes() -> list[str]:
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODES)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
