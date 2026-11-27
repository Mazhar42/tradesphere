"""The four database roles can do what they are for and nothing more (ADR-003, TS-05)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core import provision
from app.core.config import settings
from app.core.db import check_database_role
from app.core.tables import (
    APPEND_ONLY_TABLES,
    GLOBAL_TABLES,
    PLATFORM_TABLES,
    SYSTEM_TABLES,
    TENANT_TABLES,
)
from app.shared.dates import business_date
from tests.conftest import Clock, Databases


def test_every_table_is_classified_exactly_once(databases: Databases) -> None:
    with databases.admin.connect() as conn:
        actual = set(
            conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        )
    groups = [PLATFORM_TABLES, GLOBAL_TABLES, TENANT_TABLES, SYSTEM_TABLES]
    assert sum(len(g) for g in groups) == len(frozenset().union(*groups)), "listed twice"
    assert actual == frozenset().union(*groups)


def test_no_role_is_a_superuser_and_only_backup_bypasses_rls(databases: Databases) -> None:
    with databases.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname LIKE 'tradesphere\\_%'"
            )
        ).all()
        owners = set(
            conn.scalars(text("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"))
        )
    attributes = {r[0]: tuple(r[1:]) for r in rows}
    assert set(attributes) >= set(provision.ROLES)
    for role in provision.ROLES:
        assert attributes[role] == (False, role == provision.BACKUP, False, False), role
    assert owners == {provision.OWNER}


def test_the_app_role_cannot_change_the_schema_or_reference_data(databases: Databases) -> None:
    for statement in (
        "CREATE TABLE sneaky (id int)",
        "ALTER TABLE products ADD COLUMN sneaky int",
        "DELETE FROM permissions",
        "UPDATE alembic_version SET version_num = 'x'",
    ):
        with databases.app.connect() as conn, pytest.raises(DBAPIError) as refused:
            conn.execute(text(statement))
        assert getattr(refused.value.orig, "sqlstate", None) == "42501", statement


@pytest.mark.parametrize("table", sorted(APPEND_ONLY_TABLES))
def test_ledgers_refuse_changes_by_grant_and_by_trigger(databases: Databases, table: str) -> None:
    with databases.admin.connect() as conn:
        for role in (provision.APP, provision.PLATFORM):
            for verb in ("UPDATE", "DELETE", "TRUNCATE"):
                allowed = conn.scalar(
                    text("SELECT has_table_privilege(:r, :t, :v)"),
                    {"r": role, "t": table, "v": verb},
                )
                assert not allowed, (role, verb)
        trigger = conn.scalar(
            text("SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass(:t) AND tgname = :n"),
            {"t": table, "n": f"{table}_append_only"},
        )
    assert trigger == 1


def test_the_platform_role_has_no_access_to_business_tables(databases: Databases) -> None:
    with databases.admin.connect() as conn:
        for table in sorted(TENANT_TABLES):
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                allowed = conn.scalar(
                    text("SELECT has_table_privilege(:r, :t, :v)"),
                    {"r": provision.PLATFORM, "t": table, "v": verb},
                )
                assert not allowed, (table, verb)


def test_production_refuses_a_privileged_role(
    databases: Databases, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="superuser"):
        check_database_role(databases.admin)
    check_database_role(databases.app)  # restricted: no error


def test_provisioning_again_changes_nothing_and_can_be_reversed(databases: Databases) -> None:
    provision.provision(databases.admin, databases.passwords)
    with databases.admin.connect() as conn, conn.begin() as transaction:
        superuser = str(conn.scalar(text("SELECT current_user")))
        provision.transfer_ownership(conn, superuser)
        owners = set(
            conn.scalars(text("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"))
        )
        assert owners == {superuser}
        transaction.rollback()


def test_the_application_under_test_is_the_restricted_role(engine: Engine) -> None:
    """Otherwise every isolation test would pass while proving nothing."""
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_user")) == provision.APP
        assert provision.privileged_reason(conn) is None


def test_the_clock_can_be_controlled(clock: Clock) -> None:
    clock.set(datetime(2026, 6, 30, 20, 0, tzinfo=UTC))  # already 1 July in Dhaka
    assert business_date().isoformat() == "2026-07-01"
    clock.advance(days=1)
    assert business_date().isoformat() == "2026-07-02"


def test_the_trigger_stops_even_the_owner(databases: Databases) -> None:
    with databases.owner.connect() as conn, conn.begin() as transaction:
        conn.execute(text("INSERT INTO audit_logs (action, entity_type) VALUES ('probe', 'test')"))
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("UPDATE audit_logs SET action = 'x' WHERE action = 'probe'"))
        transaction.rollback()
