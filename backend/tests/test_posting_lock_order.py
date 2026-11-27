"""Postings that run at the same moment must wait for each other, never deadlock.

The lock order is documented in `app/core/db.py`. These tests call the services directly, so
the retry in the HTTP layer cannot hide a deadlock. They use committed data and real threads,
in a throwaway database (`scratch_engine`).
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import INVENTORY, OPENING_EQUITY, JLine, post_journal
from app.modules.accounting.models import PaymentAccount
from app.modules.catalog.models import Product, Uom
from app.modules.inventory.reconcile import reconcile
from app.modules.inventory.stock import get_location_id, stock_in
from app.modules.parties.models import Customer
from app.modules.sales import invoices, receipts
from app.modules.sales.schemas import InvoiceLineIn, InvoiceWrite, ReceiptCreate
from app.modules.users.permissions_catalog import PERMISSIONS
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.shared.dates import business_date

D = Decimal

# Raise this for an acceptance run: RACE_ITERATIONS=1000 pytest tests/test_posting_lock_order.py
ITERATIONS = int(os.environ.get("RACE_ITERATIONS", "40"))


@dataclass
class Counter:
    """A seeded shop: two products with stock and a few customers."""

    engine: Engine
    actor: CurrentUser
    cash_id: int
    product_ids: list[int]
    customer_ids: list[int]

    def draft(self, session: Session, *, customer: int, product: int = 0) -> int:
        """A cash sale of one unit, fully paid at the counter."""
        invoice = invoices.create_draft(
            session,
            InvoiceWrite(
                customer_id=self.customer_ids[customer],
                items=[InvoiceLineIn(product_id=self.product_ids[product], qty=D(1))],
                paid_amount=D(20),
                payment_account_id=self.cash_id,
            ),
            self.actor,
        )
        return invoice.id

    def run(self, work: Callable[[Session], Any]) -> Any:
        with Session(self.engine, autoflush=False, expire_on_commit=False) as session:
            result = work(session)
            session.commit()
            return result

    def sell(self, *, customer: int, product: int = 0) -> int:
        invoice_id = self.run(lambda s: self.draft(s, customer=customer, product=product))
        self.run(lambda s: invoices.post_invoice(s, invoice_id, self.actor))
        return int(invoice_id)

    def take_money(self, *, customer: int) -> None:
        data = ReceiptCreate(
            customer_id=self.customer_ids[customer], amount=D(5), payment_account_id=self.cash_id
        )
        self.run(lambda s: receipts.create_receipt(s, data, self.actor))


@pytest.fixture
def counter(scratch_engine: Engine) -> Counter:
    with Session(scratch_engine, autoflush=False, expire_on_commit=False) as session:
        user = create_user(
            session,
            UserCreate(
                username="race-owner",
                full_name="Race Owner",
                password="Str0ng-Passw0rd",
                roles=["owner"],
            ),
            actor=None,
        )
        pcs = session.scalars(select(Uom).where(Uom.code == "PCS")).one()
        products = [
            Product(
                sku=f"RACE-{n}",
                name_en=f"Race item {n}",
                base_uom_id=pcs.id,
                dp=D(0),
                tp=D(20),
                mrp=D(0),
                reorder_level=D(0),
            )
            for n in range(2)
        ]
        customers = [
            Customer(code=f"RC-{n}", name=f"Shop {n}", credit_limit=D(0)) for n in range(4)
        ]
        session.add_all([*products, *customers])
        session.flush()
        for product in products:
            stock_in(
                session,
                product_id=product.id,
                location_id=get_location_id(session, "SELLABLE"),
                qty=D(100_000),
                value=D(1_000_000),
                business_date=business_date(),
                movement_type="grn",
                source_doc_type="RACE",
                source_doc_id=product.id,
            )
        post_journal(
            session,
            business_date=business_date(),
            source_doc_type="RACE",
            source_doc_id=None,
            source_doc_no=None,
            memo="Stock for the race tests",
            lines=[
                JLine(INVENTORY, debit=D(2_000_000)),
                JLine(OPENING_EQUITY, credit=D(2_000_000)),
            ],
            actor_id=user.id,
        )
        cash = session.scalars(
            select(PaymentAccount).where(PaymentAccount.name == "Cash in Hand")
        ).one()
        actor = CurrentUser(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            roles=("owner",),
            permissions=frozenset(PERMISSIONS),
        )
        session.commit()
        return Counter(
            scratch_engine, actor, cash.id, [p.id for p in products], [c.id for c in customers]
        )


def _wait_until_someone_is_blocked(engine: Engine, timeout: float = 15) -> None:
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    deadline = time.monotonic() + timeout
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        while time.monotonic() < deadline:
            if conn.execute(query).scalar_one():
                return
            time.sleep(0.02)
    raise AssertionError("The second transaction never had to wait; the test proves nothing.")


def _interleave(
    counter: Counter,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pause_before: str,
    first: Callable[[Session], Any],
    second: Callable[[Session], Any],
) -> dict[str, BaseException | None]:
    """Run `first` up to `invoices.<pause_before>`, start `second`, wait until it blocks on a
    lock `first` holds, then let `first` finish. Returns what each one raised."""
    paused, resume = threading.Event(), threading.Event()
    original = getattr(invoices, pause_before)

    def pausing(*args: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name == "first":
            paused.set()
            assert resume.wait(timeout=30)
        return original(*args, **kwargs)

    monkeypatch.setattr(invoices, pause_before, pausing)
    outcomes: dict[str, BaseException | None] = {}

    def attempt(name: str, work: Callable[[Session], Any]) -> None:
        try:
            counter.run(work)
            outcomes[name] = None
        except BaseException as exc:
            outcomes[name] = exc

    one = threading.Thread(target=attempt, args=("first", first), name="first")
    two = threading.Thread(target=attempt, args=("second", second), name="second")
    one.start()
    assert paused.wait(timeout=30), outcomes
    two.start()
    try:
        _wait_until_someone_is_blocked(counter.engine)
    finally:
        resume.set()
        one.join(timeout=30)
        two.join(timeout=30)
    return outcomes


def test_reversing_a_cash_sale_while_the_same_product_is_sold(
    counter: Counter, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = counter.sell(customer=0)
    draft = counter.run(lambda s: counter.draft(s, customer=1))

    outcomes = _interleave(
        counter,
        monkeypatch,
        pause_before="_post_sale_journal",  # stock is locked, no journal number yet
        first=lambda s: invoices.post_invoice(s, draft, counter.actor),
        second=lambda s: invoices.reverse_invoice(s, earlier, counter.actor),
    )

    assert outcomes["first"] is None, outcomes
    # The reversal waits for the sale, then is refused because the product moved again.
    refusal = outcomes["second"]
    assert isinstance(refusal, ConflictError), repr(refusal)
    assert refusal.code == "stock_already_moved"
    assert counter.run(reconcile).ok


def test_cash_sale_and_separate_customer_receipt_at_the_same_time(
    counter: Counter, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter.sell(customer=0)  # creates the number sequences
    draft = counter.run(lambda s: counter.draft(s, customer=1))
    money = ReceiptCreate(
        customer_id=counter.customer_ids[2], amount=D(5), payment_account_id=counter.cash_id
    )

    outcomes = _interleave(
        counter,
        monkeypatch,
        pause_before="apply_sales_summary",  # journal posted, counter receipt still to come
        first=lambda s: invoices.post_invoice(s, draft, counter.actor),
        second=lambda s: receipts.create_receipt(s, money, counter.actor),
    )

    assert outcomes == {"first": None, "second": None}, outcomes
    assert counter.run(reconcile).ok


def test_a_busy_counter_never_deadlocks(counter: Counter) -> None:
    """Sales, reversals (refused and successful) and receipts hammering the same rows."""
    earlier = counter.sell(customer=0)
    stop = threading.Event()
    failures: list[str] = []
    done = {"reversal_refused": 0, "reversed": 0, "receipts": 0}

    def guarded(work: Callable[[], None]) -> None:
        try:
            work()
        except DBAPIError as exc:
            failures.append(f"{type(exc.orig).__name__}: {exc.orig}")
            stop.set()
        except BaseException as exc:
            failures.append(repr(exc))
            stop.set()

    def sell() -> None:
        for _ in range(ITERATIONS):
            if stop.is_set():
                break
            counter.sell(customer=1)
        stop.set()

    def reverse_refused() -> None:
        while not stop.is_set():
            try:
                counter.run(lambda s: invoices.reverse_invoice(s, earlier, counter.actor))
            except ConflictError:
                done["reversal_refused"] += 1

    def sell_and_reverse_another_product() -> None:
        while not stop.is_set():
            mine = counter.sell(customer=2, product=1)
            counter.run(lambda s, i=mine: invoices.reverse_invoice(s, i, counter.actor))
            done["reversed"] += 1

    def take_money() -> None:
        while not stop.is_set():
            counter.take_money(customer=3)
            done["receipts"] += 1

    workers = (sell, reverse_refused, sell_and_reverse_another_product, take_money)
    threads = [threading.Thread(target=guarded, args=(w,)) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=600)

    assert not failures, failures
    assert not any(t.is_alive() for t in threads)
    assert all(done.values()), done  # every kind of traffic really ran
    assert counter.run(reconcile).ok
