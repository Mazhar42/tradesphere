from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.shared.schemas import ReadSchema, WriteSchema


class BusinessProfile(WriteSchema):
    name: Annotated[str, Field(min_length=1, max_length=150)] = "My Dealership"
    name_bn: Annotated[str | None, Field(max_length=150)] = None
    address: Annotated[str | None, Field(max_length=500)] = None
    phone: Annotated[str | None, Field(max_length=50)] = None
    email: Annotated[str | None, Field(max_length=150)] = None
    trade_license_no: Annotated[str | None, Field(max_length=50)] = None
    bin_no: Annotated[str | None, Field(max_length=50)] = None  # VAT BIN
    invoice_footer: Annotated[str | None, Field(max_length=300)] = None


class Preferences(WriteSchema):
    default_language: Literal["en", "bn"] = "en"
    invoice_paper: Literal["a4", "thermal80"] = "a4"
    allow_sell_below_dp: bool = False
    near_expiry_days: Annotated[int, Field(ge=1, le=365)] = 30


class PeriodLock(WriteSchema):
    lock_date: date | None = None  # nothing may be posted on or before this date


class SettingsRead(ReadSchema):
    business_profile: BusinessProfile
    preferences: Preferences
    period_lock: PeriodLock
