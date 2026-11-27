"""Subscriptions: every transition, what each state allows, limits, and money records.

Uses a throwaway database, because only the platform role may write billing data. The plan
here is test data: real plans and prices are a business decision.
"""

import re
import threading
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, get_platform_db
from app.core.errors import AppError, ConflictError
from app.core.tenant_context import set_context
from app.main import app
from app.modules.billing import service as billing
from app.modules.billing.entitlements import SubscriptionRequiredError
from app.modules.billing.models import BillingInvoice, Subscription, SubscriptionEvent
from app.modules.platform.service import add_operator
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.seed import provision_tenant
from app.shared.dates import business_date
from tests.conftest import DEFAULT_PASSWORD, TEST_TENANT, Databases, login

D = Decimal


class Shop:
    def __init__(self, databases: Databases) -> None:
        self.databases = databases
        with Session(databases.app, info={"tenant_id": TEST_TENANT}, autoflush=False) as session:
            create_user(session, _user("owner1", "owner"), actor=None)
            create_user(session, _user("staff1", "cashier"), actor=None)
            session.commit()
        with self.platform() as session:
            add_operator(session, "staff1")  # any account can be made the operator
            session.commit()
        with self.platform() as session:
            self.operator = int(session.scalar(text("SELECT user_id FROM platform_operators")))
            plan = billing.publish_plan(
                session,
                code="test",
                name="Test plan",
                interval="month",
                price=D("1000.00"),
                currency="BDT",
                trial_days=14,
                is_public=False,
                grants=[billing.Grant("max_users", 3), billing.Grant("max_products", 1)],
            )
            self.plan_id = plan.id
            billing.start_trial(session, TEST_TENANT, plan.id, self.operator)
            session.commit()
        self.today = business_date()

    def platform(self) -> Session:
        return Session(self.databases.platform, autoflush=False, expire_on_commit=False)

    def status(self) -> str:
        with self.platform() as session:
            subscription = billing.subscription_of(session, TEST_TENANT)
            assert subscription is not None
            return subscription.status

    def advance(self, days: int) -> str:
        with self.platform() as session:
            billing.advance(session, TEST_TENANT, self.today + timedelta(days=days))
            session.commit()
        return self.status()

    def invoice(self) -> BillingInvoice:
        with self.platform() as session:
            invoice = billing.issue_invoice(session, TEST_TENANT)
            session.commit()
            return invoice

    def pay(self, invoice_id: int, amount: str, reference: str) -> int:
        with self.platform() as session:
            payment = billing.record_payment(
                session,
                invoice_id,
                amount=D(amount),
                method="bank",
                reference=reference,
                received_on=self.today,
                actor_id=self.operator,
            )
            session.commit()
            return payment.id


def _user(username: str, role: str) -> UserCreate:
    return UserCreate(
        username=username, full_name=username, password=DEFAULT_PASSWORD, roles=[role]
    )


@pytest.fixture
def shop(scratch: Databases) -> Shop:
    return Shop(scratch)


@pytest.fixture
def api(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "billing_enforcement", True)

    def session_for(engine):  # type: ignore[no-untyped-def]
        def provide() -> Iterator[Session]:
            with Session(engine, autoflush=False, expire_on_commit=False) as session:
                yield session

        return provide

    app.dependency_overrides[get_db] = session_for(shop.databases.app)
    app.dependency_overrides[get_platform_db] = session_for(shop.databases.platform)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_the_whole_life_of_a_subscription(shop: Shop) -> None:
    assert shop.status() == "trialing"
    assert shop.advance(14) == "trialing"  # the last trial day
    assert shop.advance(15) == "read_only"

    invoice = shop.invoice()
    shop.pay(invoice.id, "400", "TRX-1")
    assert shop.status() == "read_only"  # part payment changes nothing
    shop.pay(invoice.id, "600", "TRX-2")
    assert shop.status() == "active"

    end = (invoice.period_end - shop.today).days
    assert shop.advance(end - 6) == "active"  # and the renewal invoice is issued
    with shop.platform() as session:
        renewals = [i for i in billing.invoices(session, TEST_TENANT) if i.status == "open"]
        assert [i.period_start for i in renewals] == [invoice.period_end + timedelta(days=1)]
    shop.advance(end - 6)  # running the job again issues nothing more
    with shop.platform() as session:
        assert len(billing.invoices(session, TEST_TENANT)) == 2

    assert shop.advance(end + 1) == "past_due"
    assert shop.advance(end + 7) == "past_due"
    assert shop.advance(end + 8) == "read_only"
    assert shop.advance(end + 8 + 90) == "read_only"
    assert shop.advance(end + 8 + 91) == "suspended"
    shop.pay(renewals[0].id, "1000", "TRX-3")
    assert shop.status() == "active"
    with shop.platform() as session:
        trail = [
            e.to_status
            for e in session.scalars(select(SubscriptionEvent).order_by(SubscriptionEvent.id))
        ]
    assert trail == [
        "trialing",
        "read_only",
        "active",
        "past_due",
        "read_only",
        "suspended",
        "active",
    ]


def test_cancellation_applies_at_the_end_of_the_period(shop: Shop) -> None:
    invoice = shop.invoice()
    shop.pay(invoice.id, "1000", "TRX-1")
    with shop.platform() as session:
        billing.set_cancellation(session, TEST_TENANT, True, shop.operator)
        session.commit()
    end = (invoice.period_end - shop.today).days
    assert shop.advance(end - 3) == "active"
    with shop.platform() as session:
        assert len(billing.invoices(session, TEST_TENANT)) == 1  # no renewal
    assert shop.advance(end + 1) == "read_only"


def test_payment_records(shop: Shop) -> None:
    invoice = shop.invoice()
    first = shop.pay(invoice.id, "1000", "TRX-9")
    with pytest.raises(ConflictError):  # already paid
        shop.pay(invoice.id, "1", "TRX-10")
    with shop.platform() as session:
        billing.reverse_payment(session, first, "Bounced", shop.operator)
        session.commit()
    assert shop.status() == "read_only"
    with pytest.raises(ConflictError):  # a reference counts once
        shop.pay(invoice.id, "1000", "TRX-9")
    with pytest.raises(AppError):  # more than is owed
        shop.pay(invoice.id, "1001", "TRX-11")
    # Ledger-like: rows cannot be changed even by the platform.
    with shop.platform() as session, pytest.raises(DBAPIError):
        session.execute(text("UPDATE billing_payments SET amount = 1"))


def test_a_new_price_does_not_change_issued_invoices(shop: Shop) -> None:
    invoice = shop.invoice()
    with shop.platform() as session:
        newer = billing.publish_plan(
            session,
            code="test",
            name="Test plan",
            interval="month",
            price=D("2000.00"),
            currency="BDT",
            trial_days=14,
            is_public=False,
            grants=[],
        )
        billing.change_plan(session, TEST_TENANT, newer.id, shop.operator)
        session.commit()
    with shop.platform() as session:
        stored = session.get(BillingInvoice, invoice.id)
        assert stored is not None and stored.amount == D("1000.00")
        assert newer.version == 2


def test_month_ends_are_clamped() -> None:
    assert billing.period_end(date(2027, 1, 31), "month") == date(2027, 2, 27)
    assert billing.period_end(date(2027, 3, 1), "year") == date(2028, 2, 29)


def test_the_application_cannot_write_its_own_subscription(shop: Shop) -> None:
    for statement in (
        "UPDATE subscriptions SET status = 'active'",
        "INSERT INTO billing_payments DEFAULT VALUES",
        "UPDATE billing_invoices SET status = 'paid'",
    ):
        with (
            Session(shop.databases.app, info={"tenant_id": TEST_TENANT}) as session,
            pytest.raises(DBAPIError, match="permission denied"),
        ):
            session.execute(text(statement))


def _every_write_route() -> list[tuple[str, str]]:
    found = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/v1/") or path.startswith(
            ("/api/v1/auth/", "/api/v1/billing", "/api/v1/platform/")
        ):
            continue
        for method in operations:
            if method.upper() != "GET":
                found.append((method.upper(), re.sub(r"\{[a-z_]+\}", "1", path)))
    return found


def test_read_only_refuses_every_write_route_and_keeps_reads(shop: Shop, api: TestClient) -> None:
    headers = login(api, "owner1")
    assert api.post("/api/v1/customers", headers=headers, json={"name": "a"}).status_code == 201
    assert shop.advance(30) == "read_only"

    routes = _every_write_route()
    allowed_through = []
    for method, path in routes:
        response = api.request(
            method, path, headers={**headers, "Idempotency-Key": "k" * 12}, json={}
        )
        if response.status_code != 402:
            allowed_through.append(f"{method} {path}: {response.status_code}")

    assert len(routes) > 60
    assert allowed_through == []
    for path in ("/api/v1/customers", "/api/v1/reports/dashboard", "/api/v1/billing"):
        assert api.get(path, headers=headers).status_code == 200, path

    assert shop.advance(30 + 91) == "suspended"
    refused = api.get("/api/v1/customers", headers=headers)
    assert refused.json()["code"] == "subscription_suspended"
    assert api.get("/api/v1/billing", headers=headers).status_code == 200


def test_an_owner_pays_and_is_active_again(shop: Shop, api: TestClient) -> None:
    headers = login(api, "owner1")
    shop.advance(30)
    invoice = shop.invoice()
    body = {"method": "bkash", "reference": "BK-123", "amount": "1000"}

    claim = api.post(
        f"/api/v1/billing/invoices/{invoice.id}/payment-claims", headers=headers, json=body
    )

    assert claim.status_code == 201, claim.text
    assert shop.status() == "read_only"  # a claim is not a payment
    shop.pay(invoice.id, "1000", "BK-123")
    assert api.post("/api/v1/customers", headers=headers, json={"name": "b"}).status_code == 201
    page = api.get("/api/v1/billing", headers=headers).json()
    assert page["status"] == "active"
    assert [(i["status"], i["paid"]) for i in page["invoices"]] == [("paid", "1000.00")]


def test_a_dealership_sees_and_claims_only_its_own_invoices(shop: Shop, api: TestClient) -> None:
    invoice = shop.invoice()
    with Session(shop.databases.app, autoflush=False) as session:
        other = provision_tenant(session, slug="other-dealer", legal_name="Other").id
        create_user(session, _user("other1", "owner"), actor=None)
        session.commit()
    headers = login(api, "other1")

    assert api.get("/api/v1/billing", headers=headers).json()["invoices"] == []
    claim = api.post(
        f"/api/v1/billing/invoices/{invoice.id}/payment-claims",
        headers=headers,
        json={"method": "bank", "reference": "STEAL-1", "amount": "1"},
    )
    assert claim.status_code == 404
    with Session(shop.databases.app, info={"tenant_id": other}) as session:
        assert session.scalars(select(Subscription)).all() == []


def test_limits_hold_when_requests_arrive_together(shop: Shop, api: TestClient) -> None:
    headers = login(api, "owner1")  # two members already, the plan allows three
    results: list[int] = []

    def add(name: str) -> None:
        body = _user(name, "cashier").model_dump()
        results.append(api.post("/api/v1/users", headers=headers, json=body).status_code)

    threads = [threading.Thread(target=add, args=(f"extra{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(results) == [201, 402, 402, 402]
    unit = next(u["id"] for u in api.get("/api/v1/uoms", headers=headers).json())
    product = {"sku": "P1", "name_en": "One", "base_uom_id": unit}
    assert api.post("/api/v1/products", headers=headers, json=product).status_code == 201
    second = api.post("/api/v1/products", headers=headers, json={**product, "sku": "P2"})
    assert second.json()["code"] == "plan_limit_reached"


def test_an_override_replaces_the_plan_limit(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "billing_enforcement", True)
    with shop.platform() as session:
        billing.set_override(
            session,
            TEST_TENANT,
            key="max_users",
            limit=2,
            enabled=None,
            reason="Agreed exception",
            expires_on=None,
            actor_id=shop.operator,
        )
        session.commit()
    with (
        Session(shop.databases.app, autoflush=False) as session,
        pytest.raises(SubscriptionRequiredError),
    ):
        set_context(session, tenant_id=TEST_TENANT)
        create_user(session, _user("third", "cashier"), actor=None)


def test_without_enforcement_nothing_is_limited(shop: Shop) -> None:
    shop.advance(30)
    with Session(shop.databases.app, info={"tenant_id": TEST_TENANT}, autoflush=False) as session:
        create_user(session, _user("free1", "cashier"), actor=None)
        create_user(session, _user("free2", "cashier"), actor=None)
        session.commit()
