from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.settings import service
from app.modules.settings.schemas import BusinessProfile, PeriodLock, Preferences, SettingsRead

router = APIRouter(prefix="/settings", tags=["settings"])

CanRead = Annotated[CurrentUser, Depends(require("settings:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("settings:write"))]


@router.get("", response_model=SettingsRead)
def get_settings(db: DbSession, _: CanRead) -> SettingsRead:
    return service.get_all(db)


@router.put("/business-profile", response_model=SettingsRead)
def put_business_profile(db: DbSession, actor: CanWrite, data: BusinessProfile) -> SettingsRead:
    service.put_setting(db, "business_profile", data, actor)
    db.commit()
    return service.get_all(db)


@router.put("/preferences", response_model=SettingsRead)
def put_preferences(db: DbSession, actor: CanWrite, data: Preferences) -> SettingsRead:
    service.put_setting(db, "preferences", data, actor)
    db.commit()
    return service.get_all(db)


@router.put("/period-lock", response_model=SettingsRead)
def put_period_lock(db: DbSession, actor: CanWrite, data: PeriodLock) -> SettingsRead:
    service.set_period_lock(db, data, actor)
    db.commit()
    return service.get_all(db)
