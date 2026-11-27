"""Business dates are in the dealership's timezone; timestamps are stored in UTC."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.shared.business import BusinessConfig, current_config


def _system_clock() -> datetime:
    return datetime.now(UTC)


_clock: Callable[[], datetime] = _system_clock


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """For tests: replace the source of "now" (None restores the system clock)."""
    global _clock  # noqa: PLW0603
    _clock = clock or _system_clock


def now_utc() -> datetime:
    return _clock()


def business_date(at: datetime | None = None, config: BusinessConfig | None = None) -> date:
    """Calendar date in the business timezone for a UTC instant (default: now)."""
    zone = ZoneInfo((config or current_config()).timezone)
    return (at or now_utc()).astimezone(zone).date()


def fiscal_year_start(d: date, config: BusinessConfig | None = None) -> date:
    """Start of the fiscal year containing `d` (Bangladesh: 1 July)."""
    month = (config or current_config()).fiscal_year_start_month
    year = d.year if d.month >= month else d.year - 1
    return date(year, month, 1)


def fiscal_year_code(d: date, config: BusinessConfig | None = None) -> str:
    """FY 2025-26 -> "2526". With a January start, FY 2026 -> "2626"."""
    config = config or current_config()
    start = fiscal_year_start(d, config)
    end_year = start.year + (1 if config.fiscal_year_start_month > 1 else 0)
    return f"{start.year % 100:02d}{end_year % 100:02d}"
