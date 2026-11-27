"""The four database roles can do what they are for and nothing more (ADR-003, TS-05)."""

import pytest
from sqlalchemy import text
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
from tests.conftest import Databases


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
