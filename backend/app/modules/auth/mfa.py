"""Second factor: enrolment and verification."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import mfa_codes
from app.core.errors import BusinessRuleError, ConflictError
from app.core.security import verify_password
from app.modules.users.models import MfaFactor, MfaRecoveryCode, User
from app.shared.dates import now_utc


def factor_of(session: Session, user_id: int) -> MfaFactor | None:
    return session.get(MfaFactor, user_id, populate_existing=True)


def is_enrolled(session: Session, user_id: int) -> bool:
    factor = factor_of(session, user_id)
    return factor is not None and factor.confirmed_at is not None


def _require_password(session: Session, user_id: int, password: str) -> User:
    user = session.get(User, user_id)
    assert user is not None
    if not verify_password(password, user.password_hash):
        raise BusinessRuleError(
            "The password is wrong.",
            code="wrong_password",
            errors=[{"field": "password", "message": "Wrong password"}],
        )
    return user


def start_enrolment(session: Session, user_id: int, password: str) -> tuple[str, str]:
    """A new secret, unused until a code from it is confirmed. Returns (secret, otpauth URI)."""
    user = _require_password(session, user_id, password)
    factor = factor_of(session, user_id)
    if factor is not None and factor.confirmed_at is not None:
        raise ConflictError("Two-step sign-in is already on.", code="mfa_already_enrolled")
    secret = mfa_codes.new_secret()
    if factor is None:
        factor = MfaFactor(user_id=user_id, secret_encrypted="")
        session.add(factor)
    factor.secret_encrypted = mfa_codes.encrypt(secret)
    factor.last_step = 0
    session.flush()
    return secret, mfa_codes.setup_uri(secret, user.username)


def confirm_enrolment(session: Session, user_id: int, code: str) -> list[str]:
    """Turns the factor on and returns the recovery codes, shown only now."""
    factor = factor_of(session, user_id)
    if factor is None or factor.confirmed_at is not None:
        raise ConflictError("Start the setup again.", code="mfa_not_started")
    if not _accept_code(factor, code):
        raise BusinessRuleError(
            "That code is not right. Check the time on your phone.",
            code="invalid_code",
            errors=[{"field": "code", "message": "Wrong code"}],
        )
    factor.confirmed_at = now_utc()
    codes = mfa_codes.new_recovery_codes()
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    session.add_all(
        MfaRecoveryCode(user_id=user_id, code_hash=mfa_codes.hash_recovery_code(c)) for c in codes
    )
    session.flush()
    return codes


def _accept_code(factor: MfaFactor, code: str) -> bool:
    secret = mfa_codes.decrypt(factor.secret_encrypted)
    step = mfa_codes.matching_step(secret, code.strip(), now_utc(), after=factor.last_step)
    if step is None:
        return False
    factor.last_step = step
    return True


def verify(session: Session, user_id: int, code: str) -> bool:
    """An authenticator code, or an unused recovery code."""
    factor = session.scalar(
        select(MfaFactor)
        .where(MfaFactor.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if factor is None or factor.confirmed_at is None:
        return False
    if _accept_code(factor, code):
        session.flush()
        return True
    recovery = session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == mfa_codes.hash_recovery_code(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery is None:
        return False
    recovery.used_at = now_utc()
    session.flush()
    return True


def disable(session: Session, user_id: int, password: str, code: str) -> None:
    _require_password(session, user_id, password)
    if not verify(session, user_id, code):
        raise BusinessRuleError(
            "That code is not right.",
            code="invalid_code",
            errors=[{"field": "code", "message": "Wrong code"}],
        )
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    session.execute(delete(MfaFactor).where(MfaFactor.user_id == user_id))
