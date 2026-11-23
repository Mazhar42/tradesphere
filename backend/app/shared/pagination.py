from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


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
