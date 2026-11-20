"""Business dates live in Asia/Dhaka; timestamps are stored in UTC."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

BUSINESS_TZ = ZoneInfo(settings.business_timezone)


def now_utc() -> datetime:
    return datetime.now(UTC)


def business_date(at: datetime | None = None) -> date:
    """Calendar date in the business timezone for a UTC instant (default: now)."""
    return (at or now_utc()).astimezone(BUSINESS_TZ).date()


def fiscal_year_start(d: date) -> date:
    """Start of the fiscal year containing `d` (Bangladesh: 1 July)."""
    month = settings.fiscal_year_start_month
    year = d.year if d.month >= month else d.year - 1
    return date(year, month, 1)


def fiscal_year_code(d: date) -> str:
    """FY 2025-26 -> "2526". With a January start, FY 2026 -> "2626"."""
    start = fiscal_year_start(d)
    end_year = start.year + (1 if settings.fiscal_year_start_month > 1 else 0)
    return f"{start.year % 100:02d}{end_year % 100:02d}"
