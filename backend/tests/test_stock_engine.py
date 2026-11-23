"""Stock engine: weighted-average cost, locations, reversals (service level)."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError
from app.modules.inventory.models import InventoryMovement, ProductCost
from app.modules.inventory.stock import (
    get_location_id,
    lock_costs,
    reverse_movements,
    stock_in,
    stock_out,
)
from tests.helpers import create_product_row, today

D = Decimal
_doc = iter(range(1, 10_000))


def _in(
    db: Session,
    product_id: int,
    qty: str,
    value: str,
    *,
    location: str = "SELLABLE",
    doc: int | None = None,
    batch_id: int | None = None,
    kind: str = "grn",
) -> InventoryMovement:
    return stock_in(
        db,
        product_id=product_id,
        location_id=get_location_id(db, location),
        batch_id=batch_id,
        qty=D(qty),
        value=D(value),
        business_date=today(),
        movement_type=kind,
        source_doc_type="TEST",
        source_doc_id=doc or next(_doc),
    )


def _out(
    db: Session, product_id: int, qty: str, *, location: str = "SELLABLE", doc: int | None = None
) -> InventoryMovement:
    return stock_out(
        db,
        product_id=product_id,
        location_id=get_location_id(db, location),
        qty=D(qty),
        business_date=today(),
        movement_type="sale",
        source_doc_type="TEST",
        source_doc_id=doc or next(_doc),
    )


def _cost(db: Session, product_id: int) -> ProductCost:
    return lock_costs(db, [product_id])[product_id]


def test_weighted_average_cost_example_from_spec(db: Session) -> None:
    p = create_product_row(db, "WAC1")
    _in(db, p.id, "100", "100000")  # 100 @ 1000
    _in(db, p.id, "100", "120000")  # 100 @ 1200
    cost = _cost(db, p.id)
    assert (cost.qty_on_hand, cost.total_value, cost.avg_cost) == (
        D(200),
        D(220000),
        D("1100.0000"),
    )

    sold = _out(db, p.id, "50")
    assert sold.value == D(-55000)  # COGS = 50 x 1100
    assert sold.unit_cost == D("1100.0000")
    cost = _cost(db, p.id)
    assert (cost.qty_on_hand, cost.total_value) == (D(150), D(165000))


def test_free_goods_lower_the_average_cost(db: Session) -> None:
    p = create_product_row(db, "FREE1")
    _in(db, p.id, "100", "100000")  # avg 1000
    _in(db, p.id, "100", "0", kind="grn_free")  # 100 free units
    cost = _cost(db, p.id)
    assert (cost.qty_on_hand, cost.total_value, cost.avg_cost) == (D(200), D(100000), D("500.0000"))


def test_taking_all_stock_takes_all_value_no_rounding_residue(db: Session) -> None:
    p = create_product_row(db, "ROUND1")
    _in(db, p.id, "3", "100.01")  # 33.3367 each
    a = _out(db, p.id, "1")
    b = _out(db, p.id, "1")
    c = _out(db, p.id, "1")
    assert -(a.value + b.value + c.value) == D("100.01")  # exactly what came in
    cost = _cost(db, p.id)
    assert (cost.qty_on_hand, cost.total_value, cost.avg_cost) == (D(0), D(0), D(0))


def test_cannot_oversell_and_state_is_unchanged(db: Session) -> None:
    p = create_product_row(db, "OVER1")
    _in(db, p.id, "10", "1000")
    with pytest.raises(BusinessRuleError, match="Insufficient stock"):
        _out(db, p.id, "11")
    assert _cost(db, p.id).qty_on_hand == D(10)


def test_damaged_stock_is_not_sellable(db: Session) -> None:
    p = create_product_row(db, "LOC1")
    _in(db, p.id, "10", "1000", location="SELLABLE")
    _in(db, p.id, "5", "500", location="DAMAGED")
    assert _cost(db, p.id).qty_on_hand == D(15)  # cost is per product, across locations
    _out(db, p.id, "10", location="SELLABLE")
    with pytest.raises(BusinessRuleError, match="Insufficient stock"):
        _out(db, p.id, "1", location="SELLABLE")  # only damaged units remain
    _out(db, p.id, "5", location="DAMAGED")


def test_batches_are_tracked_separately(db: Session) -> None:
    from app.modules.inventory.models import Batch  # noqa: PLC0415

    p = create_product_row(db, "BATCH1", track_batch=True)
    b1 = Batch(product_id=p.id, batch_no="B1")
    b2 = Batch(product_id=p.id, batch_no="B2")
    db.add_all([b1, b2])
    db.flush()
    _in(db, p.id, "10", "100", batch_id=b1.id)
    _in(db, p.id, "10", "100", batch_id=b2.id)
    stock_out(
        db,
        product_id=p.id,
        location_id=get_location_id(db, "SELLABLE"),
        batch_id=b1.id,
        qty=D(10),
        business_date=today(),
        movement_type="sale",
        source_doc_type="TEST",
        source_doc_id=1,
    )
    with pytest.raises(BusinessRuleError, match="Insufficient stock"):
        stock_out(
            db,
            product_id=p.id,
            location_id=get_location_id(db, "SELLABLE"),
            batch_id=b1.id,
            qty=D(1),
            business_date=today(),
            movement_type="sale",
            source_doc_type="TEST",
            source_doc_id=2,
        )


def test_reversal_mirrors_quantity_and_value(db: Session) -> None:
    p = create_product_row(db, "REV1")
    _in(db, p.id, "100", "100000", doc=900)
    reversed_ = reverse_movements(
        db, source_doc_type="TEST", source_doc_id=900, business_date=today(), actor_id=None
    )
    assert len(reversed_) == 1 and reversed_[0].qty_base == D(-100)
    assert reversed_[0].value == D(-100000)
    cost = _cost(db, p.id)
    assert (cost.qty_on_hand, cost.total_value) == (D(0), D(0))


def test_reversal_is_refused_once_stock_has_moved(db: Session) -> None:
    p = create_product_row(db, "REV2")
    _in(db, p.id, "100", "100000", doc=901)
    _out(db, p.id, "10")  # a later movement exists
    with pytest.raises(ConflictError) as error:
        reverse_movements(
            db, source_doc_type="TEST", source_doc_id=901, business_date=today(), actor_id=None
        )
    assert error.value.code == "stock_already_moved"


def test_inbound_requires_positive_quantity(db: Session) -> None:
    p = create_product_row(db, "BAD1")
    with pytest.raises(BusinessRuleError):
        _in(db, p.id, "0", "0")
