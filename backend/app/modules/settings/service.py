from datetime import date

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError
from app.core.permissions import CurrentUser
from app.modules.settings.models import AppSetting
from app.modules.settings.schemas import BusinessProfile, PeriodLock, Preferences, SettingsRead
from app.shared.audit import record_audit
from app.shared.dates import business_date

SETTING_MODELS: dict[str, type[BaseModel]] = {
    "business_profile": BusinessProfile,
    "preferences": Preferences,
    "period_lock": PeriodLock,
}


def get_setting[T: BaseModel](session: Session, key: str, model: type[T]) -> T:
    row = session.get(AppSetting, key)
    return model.model_validate(row.value) if row else model()


def get_all(session: Session) -> SettingsRead:
    return SettingsRead(
        business_profile=get_setting(session, "business_profile", BusinessProfile),
        preferences=get_setting(session, "preferences", Preferences),
        period_lock=get_setting(session, "period_lock", PeriodLock),
    )


def put_setting(session: Session, key: str, value: BaseModel, actor: CurrentUser | None) -> None:
    row = session.get(AppSetting, key)
    new_value = value.model_dump(mode="json")
    before = row.value if row else None
    if row is None:
        session.add(AppSetting(key=key, value=new_value, updated_by_id=actor.id if actor else None))
    else:
        row.value = new_value
        row.updated_by_id = actor.id if actor else None
    session.flush()
    record_audit(
        session,
        actor_id=actor.id if actor else None,
        action="update",
        entity_type="setting",
        entity_id=key,
        before=before,
        after=new_value,
    )


def set_period_lock(session: Session, value: PeriodLock, actor: CurrentUser) -> None:
    if value.lock_date and value.lock_date > business_date():
        raise BusinessRuleError(
            "The lock date cannot be in the future.",
            errors=[{"field": "lock_date", "message": "Must be today or earlier"}],
        )
    put_setting(session, "period_lock", value, actor)


def period_lock_date(session: Session) -> date | None:
    return get_setting(session, "period_lock", PeriodLock).lock_date


def ensure_period_open(session: Session, on: date) -> None:
    """Reject postings dated on or before the period lock date."""
    lock = period_lock_date(session)
    if lock is not None and on <= lock:
        raise ConflictError(
            f"The period up to {lock.isoformat()} is locked; nothing can be posted or reversed "
            f"on {on.isoformat()}.",
            code="period_locked",
        )
