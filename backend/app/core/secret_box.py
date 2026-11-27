"""Encrypting a stored credential with a key that is not in the database."""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet(purpose: str) -> Fernet:
    key = settings.mfa_encryption_key or settings.jwt_secret  # fallback: development only
    digest = hashlib.sha256(purpose.encode() + b":" + key.get_secret_value().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(purpose: str, value: str) -> str:
    return _fernet(purpose).encrypt(value.encode()).decode()


def unseal(purpose: str, token: str) -> str:
    return _fernet(purpose).decrypt(token.encode()).decode()
