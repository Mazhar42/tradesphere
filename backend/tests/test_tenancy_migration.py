"""Upgrading a database that already holds a business (database-transformation.md §8-9).

Runs as a real installation would: roles take over the old schema, then the owner migrates.
The money and stock totals must be identical before, after, and after rolling back.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url, text

from alembic import command
from app.core import provision
from tests.conftest import ROLE_PASSWORDS, TEST_DATABASE_URL, Databases

BEFORE_TENANCY = "f93ec8729359"

OLD_DATA = """
INSERT INTO uoms(code,name_en,is_active) VALUES ('PCS','Piece',true);
INSERT INTO accounts(code,name_en,type,is_system,is_postable,is_active,party_type) VALUES
  ('1000','Cash','asset',true,true,true,NULL),
  ('1100','Receivable','asset',true,true,true,'customer');
INSERT INTO users(username,full_name,password_hash,is_active) VALUES ('old','Old Owner','x',true);
INSERT INTO roles(code,name) VALUES ('owner','Owner');
INSERT INTO user_roles(user_id,role_id) SELECT u.id, r.id FROM users u, roles r;
INSERT INTO app_settings(key,value) VALUES ('business_profile','{"name":"Old Shop"}');
INSERT INTO document_sequences(doc_type,period,last_value) VALUES ('JV','2627',1);
INSERT INTO customers(code,name,is_walk_in,credit_limit,payment_terms_days,is_active)
  VALUES ('C1','Buyer',false,0,0,true);
INSERT INTO journal_entries(entry_no,business_date,source_doc_type,memo,created_by_id)
  SELECT 'JV-2627-000001', current_date, 'old', 'old entry', id FROM users;
INSERT INTO journal_lines(entry_id,account_id,debit,credit,party_type,party_id)
  SELECT e.id, a.id,
         CASE WHEN a.code='1100' THEN 5 ELSE 0 END, CASE WHEN a.code='1000' THEN 5 ELSE 0 END,
         a.party_type, CASE WHEN a.code='1100' THEN c.id END
  FROM journal_entries e, accounts a, customers c;
INSERT INTO audit_logs(actor_id,action,entity_type) SELECT id,'login','auth' FROM users;
"""

TOTALS = (
    "SELECT sum(debit) FROM journal_lines",
    "SELECT sum(credit) FROM journal_lines",
    "SELECT string_agg(account_id || ':' || debit || ':' || credit, ',' ORDER BY id) "
    "FROM journal_lines",
    "SELECT count(*) FROM audit_logs",
    "SELECT max(last_value) FROM document_sequences",
)


@pytest.fixture
def old_database(databases: Databases) -> Iterator[tuple[URL, Engine]]:
    assert TEST_DATABASE_URL
    name = f"tradesphere_old_{uuid.uuid4().hex[:10]}"
    admin = databases.admin.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(TEST_DATABASE_URL).set(database=name)
    superuser = create_engine(url)
    try:
        yield url, superuser
    finally:
        superuser.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _config(url: URL) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url.render_as_string(False)
    cfg.attributes["configure_logger"] = False
    return cfg


def _totals(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [str(conn.scalar(text(q))) for q in TOTALS]


def test_an_existing_business_becomes_the_first_dealership_unchanged(
    old_database: tuple[URL, Engine],
) -> None:
    url, superuser = old_database
    command.upgrade(_config(url), BEFORE_TENANCY)
    with superuser.begin() as conn:
        conn.exec_driver_sql(OLD_DATA)
    before = _totals(superuser)

    provision.provision(superuser, ROLE_PASSWORDS)
    owner = _config(url.set(username=provision.OWNER, password=ROLE_PASSWORDS[provision.OWNER]))
    command.upgrade(owner, "head")
    command.check(owner)  # the models describe the result

    assert _totals(superuser) == before
    with superuser.connect() as conn:
        members = conn.execute(
            text(
                "SELECT t.slug, t.legal_name, m.status FROM tenant_memberships m "
                "JOIN tenants t ON t.id = m.tenant_id"
            )
        ).all()
        lines = conn.scalars(text("SELECT DISTINCT tenant_id FROM journal_lines")).all()
    assert [tuple(m) for m in members] == [("default", "Old Shop", "active")]
    assert len(lines) == 1

    command.downgrade(owner, BEFORE_TENANCY)
    assert _totals(superuser) == before
    command.upgrade(owner, "head")

    with superuser.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (slug, legal_name, timezone, fiscal_year_start_month) "
                "VALUES ('second', 'Second', 'Asia/Dhaka', 7)"
            )
        )
    with pytest.raises(RuntimeError, match="More than one dealership"):
        command.downgrade(owner, BEFORE_TENANCY)
