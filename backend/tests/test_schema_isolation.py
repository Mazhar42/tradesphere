# ruff: noqa: E501  (SQL is clearer unwrapped)
"""The database itself must make cross-dealership references impossible (ADR-003, layer 2)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.tables import TENANT_TABLES
from app.models import Base
from tests.conftest import Databases

# database-transformation.md §9, queries 2 and 3.
KEYS_WITHOUT_TENANT = """
SELECT conrelid::regclass::text, conname FROM pg_constraint k
WHERE contype = 'f'
  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid  AND a.attname = 'tenant_id')
  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.confrelid AND a.attname = 'tenant_id')
  AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid
                  AND a.attname = 'tenant_id' AND a.attnum = ANY (k.conkey))
"""


def test_every_tenant_table_has_a_tenant_column_tied_to_a_tenant(databases: Databases) -> None:
    with databases.admin.connect() as conn:
        with_column = set(
            conn.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND column_name = 'tenant_id' AND is_nullable = 'NO'"
                )
            )
        )
        anchored = set(
            conn.scalars(
                text(
                    "SELECT conrelid::regclass::text FROM pg_constraint WHERE conname LIKE 'fk\\_tid\\_%'"
                )
            )
        )
    assert TENANT_TABLES - with_column == set()
    assert TENANT_TABLES - anchored == set()


def test_no_key_between_tenant_tables_omits_the_tenant(databases: Databases) -> None:
    with databases.admin.connect() as conn:
        assert conn.execute(text(KEYS_WITHOUT_TENANT)).all() == []


def test_every_reference_in_the_models_is_enforced_by_a_composite_key(
    databases: Databases,
) -> None:
    """`alembic check` does not compare these keys, so this does."""
    with databases.admin.connect() as conn:
        enforced = {
            (str(r[0]), tuple(r[1]), str(r[2]))
            for r in conn.execute(
                text(
                    "SELECT k.conrelid::regclass::text, "
                    " (SELECT array_agg(a.attname::text ORDER BY u.n) FROM unnest(k.conkey) "
                    "  WITH ORDINALITY u(num, n) JOIN pg_attribute a "
                    "  ON a.attrelid = k.conrelid AND a.attnum = u.num), "
                    " k.confrelid::regclass::text "
                    "FROM pg_constraint k WHERE k.contype = 'f'"
                )
            )
        }
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in TENANT_TABLES:
            continue
        for key in table.foreign_keys:
            column, parent = key.parent.name, key.column.table.name
            if column == "tenant_id":
                continue
            if parent in TENANT_TABLES:
                expected = (table.name, ("tenant_id", column), parent)
            elif parent == "users":
                expected = (table.name, ("tenant_id", column), "tenant_memberships")
            else:
                expected = (table.name, (column,), parent)
            if expected not in enforced:
                missing.append(expected)
    assert missing == []


def test_business_uniqueness_is_per_dealership(databases: Databases) -> None:
    """A global unique would let one dealership learn another's values from the error."""
    with databases.admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT i.indrelid::regclass::text, i.indexrelid::regclass::text, "
                " pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "WHERE i.indisunique AND NOT i.indisprimary"
            )
        ).all()
    # These name a column that already belongs to one dealership (a row id).
    by_row_id = (
        "(product_id",
        "(receipt_id",
        "(reversal_of_id",
    )
    offenders = [
        name
        for table, name, definition in rows
        if table in TENANT_TABLES
        and "(tenant_id" not in definition
        and not any(part in definition for part in by_row_id)
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "statement",
    [
        # a product of dealership 2 using dealership 1's unit
        "INSERT INTO products (tenant_id, sku, name_en, base_uom_id, dp, tp, mrp, reorder_level,"
        " track_batch, track_expiry, is_active) SELECT 2, 'X', 'X', id, 0, 0, 0, 0, false, false, true"
        " FROM uoms WHERE tenant_id = 1 LIMIT 1",
        # a ledger line of dealership 2 on dealership 1's account
        "INSERT INTO journal_lines (tenant_id, entry_id, account_id, debit, credit)"
        " SELECT 2, 1, id, 1, 0 FROM accounts WHERE tenant_id = 1 LIMIT 1",
        # an audit row of dealership 2 naming a person who is not its member
        "INSERT INTO audit_logs (tenant_id, actor_id, action, entity_type) VALUES (2, 999999, 'x', 'x')",
    ],
)
def test_the_database_refuses_a_cross_dealership_reference(
    databases: Databases, statement: str
) -> None:
    with databases.admin.connect() as conn, conn.begin() as transaction:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, legal_name, timezone, fiscal_year_start_month) "
                "VALUES (2, 'other-dealer', 'Other', 'Asia/Dhaka', 7)"
            )
        )
        with pytest.raises(IntegrityError, match="foreign key"):
            conn.execute(text(statement))
        transaction.rollback()
