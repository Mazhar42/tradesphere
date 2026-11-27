from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import BusinessRuleError

CursorQuery = Annotated[str | None, Query(max_length=60)]


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]


def paginate(
    session: Session, stmt: Select[*tuple[Any, ...]], params: PageParams
) -> tuple[Sequence[Any], int]:
    """Run `stmt` for one page and return (items, total)."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = session.scalars(stmt.limit(params.page_size).offset(params.offset)).all()
    return items, total


def paginate_rows(
    session: Session, stmt: Select[*tuple[Any, ...]], params: PageParams
) -> tuple[Sequence[Any], int]:
    """Like `paginate`, for statements selecting several columns (returns Row objects)."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.execute(stmt.limit(params.page_size).offset(params.offset)).all()
    return rows, total


def paginate_keyset(
    session: Session,
    stmt: Select[*tuple[Any, ...]],
    params: PageParams,
    keys: tuple[InstrumentedAttribute[Any], ...],
    cursor: str | None,
    *,
    rows: bool = False,
) -> tuple[Sequence[Any], int, str | None]:
    """A page by number, or (with `cursor`) the rows after the last one seen, which stays
    fast however deep the list goes. `stmt` must be ordered by `keys`, all descending.
    With `rows`, the keys are read from the first entity of each row."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    if cursor:
        parts = cursor.split("|")
        try:
            if len(parts) != len(keys):
                raise ValueError(cursor)
            values = [
                date.fromisoformat(part) if key.type.python_type is date else int(part)
                for key, part in zip(keys, parts, strict=True)
            ]
        except ValueError as error:
            raise BusinessRuleError("That cursor is not valid.", code="invalid_cursor") from error
        stmt = stmt.where(tuple_(*keys) < tuple_(*values))
    else:
        stmt = stmt.offset(params.offset)
    result = session.execute(stmt.limit(params.page_size + 1))
    found = result.all() if rows else result.scalars().all()
    items = found[: params.page_size]
    following = None
    if len(found) > params.page_size:
        last = items[-1][0] if rows else items[-1]
        following = "|".join(str(getattr(last, key.key)) for key in keys)
    return items, total, following
