"""Database engine, request sessions and the rules every transaction follows.

Lock order. A transaction takes locks in this order and never goes back up it, so two postings
wait for each other instead of deadlocking:

1. the party (customer);
2. the document being posted or reversed (and the purchase order or claim it belongs to);
3. `product_costs` rows, in product id order (`inventory.stock.lock_costs`);
4. `stock_balances` rows;
5. document numbers (`shared.sequences`) other than the journal's;
6. the journal number `JV`, taken by every `post_journal`, and what is written after it
   (`daily_sales_summary`, claimable items).

In particular: nothing may lock a product, or draw another document number, after the first
`post_journal` of the transaction.

If a deadlock or serialisation failure happens all the same, the whole request is run once
more (`core.errors.RetryTransientConflicts`).
"""

from collections.abc import Iterator
from contextvars import ContextVar
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# 40001 serialization_failure, 40P01 deadlock_detected: PostgreSQL rolled the transaction back,
# and running it again can succeed.
TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})

# Set by the retry middleware for one request; every commit made while handling it is noted.
commit_log: ContextVar[list[bool] | None] = ContextVar("commit_log", default=None)


def is_transient_conflict(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) in TRANSIENT_SQLSTATES


@event.listens_for(Session, "after_commit")
def _note_commit(_: Session) -> None:
    log = commit_log.get()
    if log is not None:
        log.append(True)


def get_db() -> Iterator[Session]:
    """One session per request.

    Services never commit; the router commits once at the end of a successful
    business operation, so each operation is a single atomic transaction.
    Anything left uncommitted is rolled back when the session closes.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
