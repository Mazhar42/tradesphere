from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from app.shared.text import nfc_deep


class ReadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _one_unicode_form(cls, value: Any, info: ValidationInfo) -> Any:
        # Passwords are checked exactly as typed, or existing ones would stop matching.
        if info.field_name and "password" in info.field_name:
            return value
        return nfc_deep(value)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    # Set by lists that can also be read by cursor; absent when there is nothing further.
    next_cursor: str | None = None
