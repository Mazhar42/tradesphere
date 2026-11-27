from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError
from app.modules.accounting.journal import JLine, post_journal, reverse_entries
from app.modules.accounting.models import JournalEntry
from tests.helpers import D, Shop, today


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def _post(db: Session, lines: list[JLine], **kw) -> JournalEntry:
    return post_journal(
        db,
        business_date=kw.pop("business_date", today()),
        source_doc_type="TEST",
        source_doc_id=1,
        source_doc_no="T-1",
        memo="test",
        lines=lines,
        actor_id=None,
        **kw,
    )


def test_balanced_entry_is_posted_with_a_gapless_number(db: Session) -> None:
    entry = _post(db, [JLine("1000", debit=D("100")), JLine("4000", credit=D("100"))])
    assert entry.entry_no.startswith("JV-")
    assert [(line.account.code, line.debit, line.credit) for line in entry.lines] == [
        ("1000", D(100), D(0)),
        ("4000", D(0), D(100)),
    ]


def test_unbalanced_entry_is_rejected(db: Session) -> None:
    with pytest.raises(BusinessRuleError) as error:
        _post(db, [JLine("1000", debit=D("100")), JLine("4000", credit=D("99.99"))])
    assert error.value.code == "unbalanced_entry"


@pytest.mark.parametrize(
    "line",
    [
        JLine("1000", debit=D("5"), credit=D("5")),  # both sides
        JLine("1000"),  # neither side
        JLine("1000", debit=D("5.001")),  # sub-paisa
        JLine("6000", debit=D("5")),  # non-postable parent account
        JLine("9999", debit=D("5")),  # unknown account
    ],
)
def test_invalid_lines_are_rejected(db: Session, line: JLine) -> None:
    with pytest.raises(BusinessRuleError):
        _post(db, [line, JLine("4000", credit=D("5"))])


def test_party_accounts_require_a_party_and_others_forbid_one(db: Session) -> None:
    with pytest.raises(BusinessRuleError) as error:
        _post(db, [JLine("2000", debit=D("5")), JLine("1000", credit=D("5"))])
    assert error.value.code == "party_required"
    with pytest.raises(BusinessRuleError, match="does not carry a party"):
        _post(
            db,
            [
                JLine("1000", debit=D("5"), party_type="supplier", party_id=1),
                JLine("4000", credit=D("5")),
            ],
        )


def test_period_lock_blocks_posting_and_reversal(db: Session, client, owner) -> None:
    entry = _post(db, [JLine("1000", debit=D("10")), JLine("4000", credit=D("10"))])
    db.flush()
    lock = client.put(
        "/api/v1/settings/period-lock", headers=owner, json={"lock_date": today().isoformat()}
    )
    assert lock.status_code == 200
    with pytest.raises(ConflictError) as error:
        _post(db, [JLine("1000", debit=D("10")), JLine("4000", credit=D("10"))])
    assert error.value.code == "period_locked"
    with pytest.raises(ConflictError):  # the original is in a closed period too
        reverse_entries(
            db,
            source_doc_type="TEST",
            source_doc_id=entry.source_doc_id or 0,
            business_date=today(),
            actor_id=None,
        )


def test_reversal_mirrors_the_entry_and_can_only_happen_once(db: Session) -> None:
    original = _post(db, [JLine("1000", debit=D("40")), JLine("4000", credit=D("40"))])
    first = reverse_entries(
        db, source_doc_type="TEST", source_doc_id=1, business_date=today(), actor_id=None
    )
    assert len(first) == 1 and first[0].reversal_of_id == original.id
    assert [(line.debit, line.credit) for line in first[0].lines] == [(D(0), D(40)), (D(40), D(0))]
    assert (
        reverse_entries(
            db, source_doc_type="TEST", source_doc_id=1, business_date=today(), actor_id=None
        )
        == []
    )


# --- database-level guarantees -------------------------------------------------------------
def test_database_rejects_an_unbalanced_entry_at_commit(db: Session) -> None:
    db.execute(text("SET CONSTRAINTS journal_lines_balanced IMMEDIATE"))
    entry = JournalEntry(
        entry_no="JV-RAW-1", business_date=today(), source_doc_type="RAW", memo="x"
    )
    db.add(entry)
    db.flush()
    cash = db.execute(text("SELECT id FROM accounts WHERE code = '1000'")).scalar_one()
    with pytest.raises(DBAPIError, match="not balanced"):
        db.execute(
            text(
                "INSERT INTO journal_lines (entry_id, account_id, debit, credit) "
                "VALUES (:e, :a, 10, 0)"
            ),
            {"e": entry.id, "a": cash},
        )


def test_journal_and_stock_ledgers_are_append_only(db: Session) -> None:
    _post(db, [JLine("1000", debit=D("1")), JLine("4000", credit=D("1"))])
    for statement in (
        "UPDATE journal_lines SET debit = 2 WHERE debit = 1",
        "DELETE FROM journal_lines",
        "UPDATE journal_entries SET memo = 'x'",
        "DELETE FROM journal_entries",
    ):
        with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
            db.execute(text(statement))


# --- API: payment accounts, journal, trial balance -------------------------------------------
def test_payment_accounts_are_seeded_and_can_be_added(shop: Shop) -> None:
    names = {a["name"]: a for a in shop.get("/api/v1/payment-accounts")}
    assert {"Cash in Hand", "bKash", "Nagad"} <= set(names)
    assert names["Cash in Hand"]["gl_account_code"] == "1000"
    assert names["bKash"]["kind"] == "mfs"

    bank = shop.post(
        "/api/v1/payment-accounts",
        {
            "name": "Islami Bank 123",
            "kind": "bank",
            "bank_name": "Islami Bank",
            "account_no": "123",
        },
        expect=201,
    )
    assert bank["gl_account_code"] == "1010" and bank["balance"] == "0.00"

    duplicate = shop.client.post(
        "/api/v1/payment-accounts",
        headers=shop.headers,
        json={"name": "islami bank 123", "kind": "bank"},
    )
    assert duplicate.status_code == 409
    wrong_gl = shop.client.post(
        "/api/v1/payment-accounts",
        headers=shop.headers,
        json={"name": "Odd", "kind": "bank", "gl_account_code": "1000"},
    )
    assert wrong_gl.status_code == 422


def test_system_payment_account_cannot_be_deactivated(shop: Shop) -> None:
    cash = next(a for a in shop.get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand")
    response = shop.client.patch(
        f"/api/v1/payment-accounts/{cash['id']}", headers=shop.headers, json={"is_active": False}
    )
    assert response.status_code == 422


def test_trial_balance_balances_and_lists_activity(shop: Shop, db: Session) -> None:
    _post(db, [JLine("1000", debit=D("250")), JLine("4000", credit=D("250"))])
    db.flush()
    report = shop.get("/api/v1/reports/trial-balance")
    assert report["balanced"] is True
    assert Decimal(report["total_debit"]) == Decimal(report["total_credit"])
    codes = {r["account_code"] for r in report["rows"]}
    assert {"1000", "4000"} <= codes


def test_finance_endpoints_require_permission(client: TestClient, cashier: dict[str, str]) -> None:
    assert client.get("/api/v1/journal", headers=cashier).status_code == 403
    assert client.get("/api/v1/reports/trial-balance", headers=cashier).status_code == 403
    assert client.get("/api/v1/payment-accounts", headers=cashier).status_code == 403
