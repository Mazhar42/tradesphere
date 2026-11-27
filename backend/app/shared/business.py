"""How the business being served tells time and money: timezone, fiscal year, currency.

A request, job or command sets it once for the dealership it works on; date and numbering
functions use it unless given one explicitly. Nothing set means the deployment's defaults.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class BusinessConfig:
    timezone: str
    fiscal_year_start_month: int
    currency_code: str = "BDT"


def default_config() -> BusinessConfig:
    return BusinessConfig(settings.business_timezone, settings.fiscal_year_start_month)


# A holder, not the value: request dependencies run in worker threads that copy the context,
# so they must change an object the whole request shares.
_holder: ContextVar[list[BusinessConfig] | None] = ContextVar("business_config", default=None)


def current_config() -> BusinessConfig:
    holder = _holder.get()
    return holder[-1] if holder else default_config()


@contextmanager
def business_scope(config: BusinessConfig | None = None) -> Iterator[None]:
    """A fresh scope (a request, a job, a command), optionally already set."""
    token = _holder.set([config] if config else [])
    try:
        yield
    finally:
        _holder.reset(token)


def use_business(config: BusinessConfig) -> None:
    """Set the business for the current scope."""
    holder = _holder.get()
    if holder is None:
        _holder.set([config])
    else:
        holder[:] = [config]
