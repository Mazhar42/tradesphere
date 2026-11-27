"""One Unicode form for stored text; dates and numbering follow the business, not the server."""

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.modules.users.schemas import UserCreate
from app.shared.business import BusinessConfig, business_scope, current_config, use_business
from app.shared.dates import business_date, fiscal_year_code

# The vowel sign O typed as one code point, and as its two halves. They look identical.
COMPOSED = "কো"
DECOMPOSED = "কো"


def test_text_is_stored_composed(client: TestClient, owner: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/brands", headers=owner, json={"name_en": "Probe", "name_bn": DECOMPOSED}
    )

    assert response.status_code == 201, response.text
    assert response.json()["name_bn"] == COMPOSED


def test_passwords_are_left_exactly_as_typed() -> None:
    user = UserCreate(
        username="someone", full_name=DECOMPOSED, password=DECOMPOSED * 4, roles=["owner"]
    )
    assert user.full_name == COMPOSED
    assert user.password == DECOMPOSED * 4


def test_dates_follow_the_given_business() -> None:
    new_year_eve = datetime(2026, 12, 31, 20, 0, tzinfo=UTC)
    dhaka = BusinessConfig("Asia/Dhaka", 7)
    london = BusinessConfig("Europe/London", 1)

    assert business_date(new_year_eve, dhaka) == date(2027, 1, 1)
    assert business_date(new_year_eve, london) == date(2026, 12, 31)
    assert fiscal_year_code(date(2026, 8, 1), dhaka) == "2627"
    assert fiscal_year_code(date(2026, 8, 1), london) == "2626"


def test_a_scope_does_not_leak_its_business() -> None:
    before = current_config()
    with business_scope():
        use_business(BusinessConfig("Europe/London", 1))
        assert fiscal_year_code(date(2026, 8, 1)) == "2626"
    assert current_config() == before
