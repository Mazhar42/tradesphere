"""Gapless document numbering.

The counter row is updated inside the caller's transaction via an atomic
INSERT ... ON CONFLICT DO UPDATE ... RETURNING. That row stays locked until
the transaction ends, and a rollback also rolls the counter back - so posted
document numbers never have gaps or duplicates, even under concurrency.
"""

from datetime import date

from sqlalchemy import String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.dates import fiscal_year_code
from app.shared.models import Base

GLOBAL_PERIOD = "0000"  # for master-data codes that don't reset each fiscal year


class DocumentSequence(Base):
    __tablename__ = "document_sequences"

    doc_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    period: Mapped[str] = mapped_column(String(4), primary_key=True)
    last_value: Mapped[int]


def next_value(session: Session, doc_type: str, period: str) -> int:
    stmt = (
        insert(DocumentSequence)
        .values(doc_type=doc_type, period=period, last_value=1)
        .on_conflict_do_update(
            index_elements=[DocumentSequence.doc_type, DocumentSequence.period],
            set_={"last_value": DocumentSequence.last_value + 1},
        )
        .returning(DocumentSequence.last_value)
    )
    return session.execute(stmt).scalar_one()


def next_doc_number(session: Session, doc_type: str, on: date) -> str:
    """e.g. next_doc_number(s, "INV", date(2025, 8, 1)) -> "INV-2526-000001"."""
    fy = fiscal_year_code(on)
    return f"{doc_type}-{fy}-{next_value(session, doc_type, fy):06d}"


def next_master_code(session: Session, prefix: str, width: int = 5) -> str:
    """e.g. next_master_code(s, "CUS") -> "CUS-00001"."""
    return f"{prefix}-{next_value(session, prefix, GLOBAL_PERIOD):0{width}d}"


def current_value(session: Session, doc_type: str, period: str) -> int:
    value = session.scalar(
        select(DocumentSequence.last_value).where(
            DocumentSequence.doc_type == doc_type, DocumentSequence.period == period
        )
    )
    return value or 0
