from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.parties import service
from app.modules.parties.models import Customer, Supplier
from app.modules.parties.schemas import (
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
    SupplierCreate,
    SupplierRead,
    SupplierUpdate,
)
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter()

SearchQ = Annotated[str | None, Query(max_length=100)]
CustomersRead = Annotated[CurrentUser, Depends(require("customers:read"))]
CustomersWrite = Annotated[CurrentUser, Depends(require("customers:write"))]
SuppliersRead = Annotated[CurrentUser, Depends(require("suppliers:read"))]
SuppliersWrite = Annotated[CurrentUser, Depends(require("suppliers:write"))]


# --- Customers --------------------------------------------------------------
@router.get("/customers", response_model=Page[CustomerRead], tags=["customers"])
def list_customers(
    db: DbSession,
    _: CustomersRead,
    page: PageParamsDep,
    q: SearchQ = None,
    area: SearchQ = None,
    is_active: bool | None = None,
) -> Page[CustomerRead]:
    items, total = service.list_parties(db, Customer, page, q=q, is_active=is_active, area=area)
    return Page(
        items=[CustomerRead.model_validate(c) for c in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    tags=["customers"],
)
def create_customer(db: DbSession, actor: CustomersWrite, data: CustomerCreate) -> Customer:
    obj = service.create_party(db, Customer, data, actor)
    db.commit()
    return obj


@router.get("/customers/{customer_id}", response_model=CustomerRead, tags=["customers"])
def get_customer(db: DbSession, _: CustomersRead, customer_id: int) -> Customer:
    return service.get_party(db, Customer, customer_id)


@router.patch("/customers/{customer_id}", response_model=CustomerRead, tags=["customers"])
def update_customer(
    db: DbSession, actor: CustomersWrite, customer_id: int, data: CustomerUpdate
) -> Customer:
    obj = service.update_party(db, Customer, customer_id, data, actor)
    db.commit()
    return obj


# --- Suppliers --------------------------------------------------------------
@router.get("/suppliers", response_model=Page[SupplierRead], tags=["suppliers"])
def list_suppliers(
    db: DbSession,
    _: SuppliersRead,
    page: PageParamsDep,
    q: SearchQ = None,
    is_active: bool | None = None,
) -> Page[SupplierRead]:
    items, total = service.list_parties(db, Supplier, page, q=q, is_active=is_active)
    return Page(
        items=[SupplierRead.model_validate(s) for s in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/suppliers",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
    tags=["suppliers"],
)
def create_supplier(db: DbSession, actor: SuppliersWrite, data: SupplierCreate) -> Supplier:
    obj = service.create_party(db, Supplier, data, actor)
    db.commit()
    return obj


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead, tags=["suppliers"])
def get_supplier(db: DbSession, _: SuppliersRead, supplier_id: int) -> Supplier:
    return service.get_party(db, Supplier, supplier_id)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead, tags=["suppliers"])
def update_supplier(
    db: DbSession, actor: SuppliersWrite, supplier_id: int, data: SupplierUpdate
) -> Supplier:
    obj = service.update_party(db, Supplier, supplier_id, data, actor)
    db.commit()
    return obj
