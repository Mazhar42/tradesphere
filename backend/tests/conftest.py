"""Test harness.

Tests run against a real PostgreSQL database (`TEST_DATABASE_URL`) because
locking, NUMERIC, constraints and triggers are part of correctness.
Each test runs inside an outer transaction that is rolled back afterwards;
application commits only release SAVEPOINTs.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError("TEST_DATABASE_URL must be set to run the tests.")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"

from collections.abc import Callable, Iterator  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.db import engine as app_engine  # noqa: E402
from app.core.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.users.models import User  # noqa: E402
from app.modules.users.schemas import UserCreate  # noqa: E402
from app.modules.users.service import create_user  # noqa: E402
from app.seed import run_seed  # noqa: E402

DEFAULT_PASSWORD = "Str0ng-Passw0rd"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    with app_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    with Session(app_engine) as session:
        run_seed(session)
        session.commit()
    yield app_engine


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        # Same as production get_db: a failed request rolls back its uncommitted work.
        try:
            yield db
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    def _make(username: str, roles: list[str], password: str = DEFAULT_PASSWORD) -> User:
        user = create_user(
            db,
            UserCreate(
                username=username, full_name=username.title(), password=password, roles=roles
            ),
            actor=None,
        )
        db.flush()
        return user

    return _make


def login(client: TestClient, username: str, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def owner(make_user: Callable[..., User], client: TestClient) -> dict[str, str]:
    make_user("owner1", ["owner"])
    return login(client, "owner1")


@pytest.fixture
def cashier(make_user: Callable[..., User], client: TestClient) -> dict[str, str]:
    make_user("cashier1", ["cashier"])
    return login(client, "cashier1")


@pytest.fixture
def storekeeper(make_user: Callable[..., User], client: TestClient) -> dict[str, str]:
    make_user("store1", ["storekeeper"])
    return login(client, "store1")
