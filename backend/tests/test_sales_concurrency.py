"""Two counters posting sales of the last units at the same moment (committed data, real threads).

Acceptance: exactly one sale succeeds and stock never goes negative.
"""

import threading
import uuid
from decimal import Decimal

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.core.permissions import CurrentUser
from app.modules.accounting.models import PaymentAccount
from app.modules.catalog.models import Product, Uom
from app.modules.inventory.stock import get_location_id, stock_in
from app.modules.parties.models import Customer
from app.modules.sales import invoices
from app.modules.sales.schemas import InvoiceLineIn, InvoiceWrite
from app.modules.users.permissions_catalog import PERMISSIONS
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.shared.dates import business_date
from tests.conftest import IN_TEST_TENANT

D = Decimal


def test_two_sales_of_the_last_units_cannot_both_succeed(scratch_engine: Engine) -> None:
    engine = scratch_engine
    suffix = uuid.uuid4().hex[:8]
    with Session(engine, info=IN_TEST_TENANT) as setup:
        user = create_user(
            setup,
            UserCreate(
                username=f"conc-{suffix}",
                full_name="Concurrent Owner",
                password="Str0ng-Passw0rd",
                roles=["owner"],
            ),
            actor=None,
        )
        pcs = setup.query(Uom).filter(Uom.code == "PCS").one()
        product = Product(
            sku=f"SCONC-{suffix}",
            name_en="Concurrent sale",
            base_uom_id=pcs.id,
            dp=D(0),
            tp=D(20),
            mrp=D(0),
            reorder_level=D(0),
        )
        customer = Customer(code=f"SCC-{suffix}", name="Concurrent shop", credit_limit=D(0))
        setup.add_all([product, customer])
        setup.flush()
        stock_in(
            setup,
            product_id=product.id,
            location_id=get_location_id(setup, "SELLABLE"),
            qty=D(10),
            value=D(100),
            business_date=business_date(),
            movement_type="grn",
            source_doc_type="SCONC",
            source_doc_id=1,
        )
        cash = setup.scalar(select(PaymentAccount).where(PaymentAccount.name == "Cash in Hand"))
        assert cash is not None
        actor = CurrentUser(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            roles=("owner",),
            permissions=frozenset(PERMISSIONS),
        )
        invoice_ids = []
        for _ in range(2):
            draft = invoices.create_draft(
                setup,
                InvoiceWrite(
                    customer_id=customer.id,
                    items=[InvoiceLineIn(product_id=product.id, qty=D(10))],
                    paid_amount=D(200),
                    payment_account_id=cash.id,
                ),
                actor,
            )
            invoice_ids.append(draft.id)
        product_id = product.id
        setup.commit()

    outcomes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def sell(invoice_id: int) -> None:
        with Session(engine, info=IN_TEST_TENANT) as session:
            try:
                barrier.wait(timeout=10)
                invoices.post_invoice(session, invoice_id, actor)
                session.commit()
                result = "ok"
            except BusinessRuleError as exc:
                session.rollback()
                result = exc.code or "rule"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=sell, args=(i,)) for i in invoice_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["insufficient_stock", "ok"]
    with Session(engine, info=IN_TEST_TENANT) as session:
        qty = session.scalar(
            text("SELECT qty_on_hand FROM product_costs WHERE product_id = :p"),
            {"p": product_id},
        )
        posted = session.scalar(
            text(
                "SELECT count(*) FROM sales_invoices WHERE id = ANY(:ids) AND status = 'posted'"
            ).bindparams(ids=invoice_ids)
        )
        assert qty == D(0)
        assert posted == 1
