"""Row-level security (layer 3) and the ORM filter (layer 1), each with the other switched off.

The last test of `test_schema_isolation.py` covers the composite keys (layer 2).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.tables import TENANT_TABLES
from app.modules.catalog.models import Brand
from app.seed import provision_tenant
from tests.conftest import TEST_TENANT, Databases

NOT_FORCED = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:tables)
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity
           AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
"""


def test_every_tenant_table_has_forced_row_level_security(databases: Databases) -> None:
    tables = sorted(TENANT_TABLES | {"tenants", "tenant_memberships"})
    with databases.admin.connect() as conn:
        assert conn.scalars(text(NOT_FORCED), {"tables": tables}).all() == []


@pytest.fixture
def two_dealers(scratch_engine: Engine) -> Iterator[tuple[Engine, int]]:
    """The test dealership and a second one, each with a brand; yields the second one's id."""
    with Session(scratch_engine, info={"tenant_id": TEST_TENANT}) as session:
        session.add(Brand(name_en="Alpha-only"))
        session.commit()
    with Session(scratch_engine, autoflush=False) as session:
        other = provision_tenant(session, slug="other-dealer", legal_name="Other").id
        session.add(Brand(name_en="Beta-only"))
        session.commit()
    yield scratch_engine, other


def _as(conn: Connection, tenant: int | None) -> None:
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant or "")})


def test_without_a_context_nothing_is_visible_or_writable(
    two_dealers: tuple[Engine, int],
) -> None:
    engine, _ = two_dealers
    with engine.connect() as conn:
        for table in sorted(TENANT_TABLES | {"tenants", "tenant_memberships"}):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table  # noqa: S608
        with pytest.raises(DBAPIError, match=r"row-level security|null value"):
            conn.execute(text("INSERT INTO brands (name_en, is_active) VALUES ('x', true)"))


def test_a_dealership_cannot_read_or_touch_another(two_dealers: tuple[Engine, int]) -> None:
    engine, other = two_dealers
    with engine.connect() as conn:
        _as(conn, TEST_TENANT)
        assert conn.scalars(text("SELECT name_en FROM brands")).all() == ["Alpha-only"]
        beta = {"n": "Beta-only"}
        assert (
            conn.execute(text("UPDATE brands SET name_en='z' WHERE name_en=:n"), beta).rowcount == 0
        )
        assert conn.execute(text("DELETE FROM brands WHERE name_en=:n"), beta).rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text("INSERT INTO brands (tenant_id, name_en, is_active) VALUES (:t, 'x', true)"),
                {"t": other},
            )


def test_a_row_cannot_be_moved_to_another_dealership(two_dealers: tuple[Engine, int]) -> None:
    engine, other = two_dealers
    with engine.connect() as conn:
        _as(conn, TEST_TENANT)
        with pytest.raises(DBAPIError, match=r"row-level security|foreign key"):
            conn.execute(text("UPDATE brands SET tenant_id = :t"), {"t": other})


def test_rls_holds_with_the_orm_filter_off(two_dealers: tuple[Engine, int]) -> None:
    engine, _ = two_dealers
    with Session(engine, info={"tenant_id": TEST_TENANT, "unfiltered": True}) as session:
        assert [b.name_en for b in session.scalars(select(Brand))] == ["Alpha-only"]


def test_the_orm_filter_holds_with_rls_off(
    databases: Databases, two_dealers: tuple[Engine, int]
) -> None:
    engine, other = two_dealers
    superuser = databases.admin.url.set(database=engine.url.database)
    bypass = create_engine(superuser)
    try:
        with Session(bypass, info={"tenant_id": other}) as session:
            assert [b.name_en for b in session.scalars(select(Brand))] == ["Beta-only"]
        with Session(bypass) as session:  # control: without the filter, RLS really is off
            assert len(session.scalars(select(Brand)).all()) == 2
    finally:
        bypass.dispose()


def test_context_does_not_survive_into_the_next_transaction_of_a_pooled_connection(
    two_dealers: tuple[Engine, int],
) -> None:
    engine, _ = two_dealers
    with Session(engine, info={"tenant_id": TEST_TENANT}) as session:
        assert session.scalar(text("SELECT count(*) FROM brands")) == 1
        session.commit()
        assert session.scalar(text("SELECT count(*) FROM brands")) == 1  # re-applied
        session.commit()
    with engine.connect() as conn:  # the same pool, no session context
        assert conn.scalar(text("SELECT count(*) FROM brands")) == 0
