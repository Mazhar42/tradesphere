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

from sqlalchemy.engine import URL, make_url  # noqa: E402

from app.core import provision  # noqa: E402  (imports no settings)

# Only for the local and CI test databases.
ROLE_PASSWORDS = {role: f"{role}-pw" for role in provision.ROLES}


def _as(url: URL, role: str) -> URL:
    return url.set(username=role, password=ROLE_PASSWORDS[role])


# The application under test connects as the restricted role, as in production.
os.environ["DATABASE_URL"] = _as(make_url(TEST_DATABASE_URL), provision.APP).render_as_string(False)
os.environ["MIGRATION_DATABASE_URL"] = _as(
    make_url(TEST_DATABASE_URL), provision.OWNER
).render_as_string(False)
os.environ["ENVIRONMENT"] = "test"

import uuid  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.db import engine as app_engine  # noqa: E402
from app.core.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.users.models import User  # noqa: E402
from app.modules.users.schemas import UserCreate  # noqa: E402
from app.modules.users.service import create_user  # noqa: E402
from app.seed import run_seed  # noqa: E402
from app.shared import dates  # noqa: E402

DEFAULT_PASSWORD = "Str0ng-Passw0rd"


@dataclass(frozen=True)
class Databases:
    """One migrated and seeded database, seen by the roles that matter."""

    admin: Engine  # superuser: provisioning, catalog checks, test cleanup
    owner: Engine  # migrations and seed
    app: Engine  # what the application connects as
    passwords: dict[str, str]


def _build(url: URL) -> Databases:
    """Provision the roles, then migrate and seed as the owner, in an empty schema."""
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    provision.provision(admin, ROLE_PASSWORDS)
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = _as(url, provision.OWNER).render_as_string(False)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    owner = create_engine(_as(url, provision.OWNER))
    with Session(owner, autoflush=False, expire_on_commit=False) as session:
        run_seed(session)
        session.commit()
    app_role = create_engine(_as(url, provision.APP), pool_size=10)
    return Databases(admin, owner, app_role, dict(ROLE_PASSWORDS))


def _dispose(databases: Databases) -> None:
    for each in (databases.app, databases.owner, databases.admin):
        each.dispose()


@pytest.fixture(scope="session")
def databases() -> Iterator[Databases]:
    assert TEST_DATABASE_URL
    app_engine.dispose()
    built = _build(make_url(TEST_DATABASE_URL))
    try:
        yield built
    finally:
        _dispose(built)


@pytest.fixture(scope="session")
def engine(databases: Databases) -> Engine:
    return app_engine


@pytest.fixture
def scratch_engine(databases: Databases) -> Iterator[Engine]:
    """A separate, freshly migrated and seeded database for tests that must really commit
    (threads and row locks). It is dropped afterwards, so nothing has to be cleaned up."""
    assert TEST_DATABASE_URL
    name = f"tradesphere_scratch_{uuid.uuid4().hex[:10]}"
    admin = databases.admin.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        scratch = _build(make_url(TEST_DATABASE_URL).set(database=name))
        try:
            yield scratch.app
        finally:
            _dispose(scratch)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


class Clock:
    """Controls `shared.dates.now_utc` (and everything dated from it) for one test.

    Database defaults (`now()`) and token expiry checks inside PyJWT keep real time.
    """

    def set(self, at: datetime) -> None:
        dates.set_clock(lambda: at)

    def advance(self, **delta: float) -> None:
        self.set(dates.now_utc() + timedelta(**delta))


@pytest.fixture
def clock() -> Iterator[Clock]:
    try:
        yield Clock()
    finally:
        dates.set_clock(None)


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
