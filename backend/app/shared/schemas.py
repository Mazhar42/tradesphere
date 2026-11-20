from pydantic import BaseModel, ConfigDict


class ReadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
