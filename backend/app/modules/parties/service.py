from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.parties.models import Customer, Supplier
from app.modules.parties.schemas import (
    CustomerCreate,
    CustomerUpdate,
    SupplierCreate,
    SupplierUpdate,
)
from app.shared.audit import record_audit, snapshot
from app.shared.pagination import PageParams, paginate
from app.shared.search import all_words, like_pattern
from app.shared.sequences import next_master_code

_CONFIG: dict[type, tuple[str, str, int]] = {
    # model: (entity name, code prefix, code width)
    Customer: ("customer", "CUS", 5),
    Supplier: ("supplier", "SUP", 3),
}


def _ensure_unique_code[P: (Customer, Supplier)](
    session: Session, model: type[P], code: str
) -> None:
    if session.scalar(select(model.id).where(model.code == code)) is not None:
        raise ConflictError(
            "Code already exists.",
            code="duplicate",
            errors=[{"field": "code", "message": "Already exists"}],
        )


def _next_code[P: (Customer, Supplier)](session: Session, model: type[P]) -> str:
    _, prefix, width = _CONFIG[model]
    while True:  # skip codes that were entered manually
        code = next_master_code(session, prefix, width)
        if session.scalar(select(model.id).where(model.code == code)) is None:
            return code


def list_parties[P: (Customer, Supplier)](
    session: Session,
    model: type[P],
    params: PageParams,
    *,
    q: str | None,
    is_active: bool | None,
    area: str | None = None,
) -> tuple[Sequence[P], int]:
    stmt = select(model).order_by(model.name)
    if q:
        columns = [model.code, model.name, model.phone]
        if model is Customer:
            columns.append(Customer.shop_name)
        stmt = stmt.where(all_words(q, *columns))
    if is_active is not None:
        stmt = stmt.where(model.is_active == is_active)
    if area and model is Customer:
        stmt = stmt.where(Customer.area.ilike(like_pattern(area)))
    return paginate(session, stmt, params)


def get_party[P: (Customer, Supplier)](session: Session, model: type[P], party_id: int) -> P:
    obj = session.get(model, party_id)
    if obj is None:
        raise NotFoundError(f"{_CONFIG[model][0].capitalize()} not found.")
    return obj


def create_party[P: (Customer, Supplier)](
    session: Session,
    model: type[P],
    data: CustomerCreate | SupplierCreate,
    actor: CurrentUser,
) -> P:
    values = data.model_dump()
    if values.get("code"):
        _ensure_unique_code(session, model, values["code"])
    else:
        values["code"] = _next_code(session, model)
    obj = model(**values, created_by_id=actor.id)
    session.add(obj)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type=_CONFIG[model][0],
        entity_id=obj.id,
        after=snapshot(obj),
    )
    return obj


def update_party[P: (Customer, Supplier)](
    session: Session,
    model: type[P],
    party_id: int,
    data: CustomerUpdate | SupplierUpdate,
    actor: CurrentUser,
) -> P:
    obj = get_party(session, model, party_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise BusinessRuleError(
            "Name cannot be empty.", errors=[{"field": "name", "message": "Required"}]
        )
    if isinstance(obj, Customer):
        if obj.is_walk_in and changes.get("is_active") is False:
            raise BusinessRuleError("The walk-in customer cannot be deactivated.")
        for field in ("credit_limit", "payment_terms_days"):
            if field in changes and changes[field] is None:
                changes.pop(field)
    before = snapshot(obj)
    for field, value in changes.items():
        setattr(obj, field, value)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type=_CONFIG[model][0],
        entity_id=obj.id,
        before=before,
        after=snapshot(obj),
    )
    return obj
