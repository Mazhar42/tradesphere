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

D = Decimal


def test_two_sales_of_the_last_units_cannot_both_succeed(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as setup:
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
        user_id = user.id
        product_id = product.id
        customer_id = customer.id
        setup.commit()

    outcomes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def sell(invoice_id: int) -> None:
        with Session(engine) as session:
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

    try:
        assert sorted(outcomes) == ["insufficient_stock", "ok"]
        with Session(engine) as session:
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
    finally:
        _cleanup(engine, invoice_ids, product_id, customer_id, user_id)


def _cleanup(
    engine: Engine, invoice_ids: list[int], product_id: int, customer_id: int, user_id: int
) -> None:
    """Ledgers are append-only: remove this test's rows with triggers off, in this session only."""
    with Session(engine) as session:
        session.execute(text("SET session_replication_role = replica"))
        params = {"ids": invoice_ids}
        session.execute(
            text(
                "DELETE FROM journal_lines WHERE entry_id IN (SELECT id FROM journal_entries "
                "WHERE (source_doc_type = 'sales_invoice' AND source_doc_id = ANY(:ids)) "
                "OR (source_doc_type = 'customer_receipt' AND source_doc_id IN "
                "(SELECT id FROM customer_receipts WHERE sales_invoice_id = ANY(:ids))))"
            ),
            params,
        )
        session.execute(
            text(
                "DELETE FROM journal_entries WHERE (source_doc_type = 'sales_invoice' "
                "AND source_doc_id = ANY(:ids)) OR (source_doc_type = 'customer_receipt' "
                "AND source_doc_id IN "
                "(SELECT id FROM customer_receipts WHERE sales_invoice_id = ANY(:ids)))"
            ),
            params,
        )
        session.execute(
            text(
                "DELETE FROM receipt_allocations WHERE receipt_id IN "
                "(SELECT id FROM customer_receipts WHERE sales_invoice_id = ANY(:ids))"
            ),
            params,
        )
        session.execute(
            text("DELETE FROM customer_receipts WHERE sales_invoice_id = ANY(:ids)"), params
        )
        session.execute(
            text(
                "DELETE FROM sale_item_batches WHERE invoice_item_id IN "
                "(SELECT id FROM sales_invoice_items WHERE invoice_id = ANY(:ids))"
            ),
            params,
        )
        session.execute(
            text("DELETE FROM sales_invoice_items WHERE invoice_id = ANY(:ids)"), params
        )
        session.execute(text("DELETE FROM sales_invoices WHERE id = ANY(:ids)"), params)
        session.execute(
            text("DELETE FROM daily_sales_summary WHERE product_id = :p"), {"p": product_id}
        )
        session.execute(
            text("DELETE FROM inventory_movements WHERE product_id = :p"), {"p": product_id}
        )
        session.execute(text("DELETE FROM stock_balances WHERE product_id = :p"), {"p": product_id})
        session.execute(text("DELETE FROM product_costs WHERE product_id = :p"), {"p": product_id})
        session.execute(
            text(
                "DELETE FROM audit_logs WHERE actor_id = :u OR (entity_type = 'sales_invoice' "
                "AND entity_id = ANY(:sids))"
            ),
            {"u": user_id, "sids": [str(i) for i in invoice_ids]},  # audit ids are text
        )
        session.execute(text("DELETE FROM products WHERE id = :p"), {"p": product_id})
        session.execute(text("DELETE FROM customers WHERE id = :c"), {"c": customer_id})
        session.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
