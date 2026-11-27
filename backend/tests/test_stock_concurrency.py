"""Real concurrent transactions against the stock engine (committed data, separate sessions)."""

import threading
import uuid
from decimal import Decimal

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.models import Product, Uom
from app.modules.inventory.stock import get_location_id, stock_in, stock_out
from tests.helpers import today

D = Decimal


def test_two_sales_of_the_last_units_cannot_both_succeed(scratch_engine: Engine) -> None:
    engine = scratch_engine
    """100 units on hand; two users each take 60 at the same moment: exactly one wins."""
    with Session(engine) as session:
        pcs = session.query(Uom).filter(Uom.code == "PCS").one()
        product = Product(
            sku=f"CONC-{uuid.uuid4().hex[:8]}",
            name_en="Concurrent",
            base_uom_id=pcs.id,
            dp=D(0),
            tp=D(0),
            mrp=D(0),
            reorder_level=D(0),
        )
        session.add(product)
        session.flush()
        product_id = product.id
        stock_in(
            session,
            product_id=product_id,
            location_id=get_location_id(session, "SELLABLE"),
            qty=D(100),
            value=D(1000),
            business_date=today(),
            movement_type="grn",
            source_doc_type="CONC",
            source_doc_id=1,
        )
        session.commit()

    outcomes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def sell(doc_id: int) -> None:
        with Session(engine) as session:
            try:
                barrier.wait(timeout=10)
                stock_out(
                    session,
                    product_id=product_id,
                    location_id=get_location_id(session, "SELLABLE"),
                    qty=D(60),
                    business_date=today(),
                    movement_type="sale",
                    source_doc_type="CONC",
                    source_doc_id=doc_id,
                )
                session.commit()
                result = "ok"
            except BusinessRuleError:
                session.rollback()
                result = "insufficient"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=sell, args=(i,)) for i in (10, 11)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes) == ["insufficient", "ok"]
    with Session(engine) as session:
        qty, value = session.execute(
            text("SELECT qty_on_hand, total_value FROM product_costs WHERE product_id = :p"),
            {"p": product_id},
        ).one()
        assert (qty, value) == (D(40), D(400))  # one sale of 60 at cost 10, never negative
