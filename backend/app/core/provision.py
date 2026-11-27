"""Database roles (ADR-003). None of them is a superuser.

- tradesphere_owner: owns the schema and runs migrations.
- tradesphere_app: what the API and tenant jobs connect as. Data only, no schema changes.
- tradesphere_platform: platform administration and job claiming. Platform tables only.
- tradesphere_backup: logical dumps. Read-only, and may bypass row-level security.

`provision` needs a superuser once (new database, or one created before roles existed) and is
safe to repeat. `apply_grants` runs after every migration. A host that only offers one
database user (the Render demo) simply has none of these roles, and nothing here applies.
"""

from collections.abc import Mapping

from psycopg import sql
from sqlalchemy import Connection, Engine, text

from app.core.tables import APPEND_ONLY_TABLES, GLOBAL_TABLES, PLATFORM_TABLES, SYSTEM_TABLES

OWNER = "tradesphere_owner"
APP = "tradesphere_app"
PLATFORM = "tradesphere_platform"
BACKUP = "tradesphere_backup"
ROLES = (OWNER, APP, PLATFORM, BACKUP)


def _run(conn: Connection, statement: str, *names: str, literal: str | None = None) -> None:
    """Execute DDL with safely quoted identifiers (`{}`) and, optionally, one literal."""
    parts: list[sql.Composable] = [sql.Identifier(n) for n in names]
    if literal is not None:
        parts.append(sql.Literal(literal))
    raw = conn.connection.driver_connection
    assert raw is not None
    conn.exec_driver_sql(sql.SQL(statement).format(*parts).as_string(raw).replace("%", "%%"))


def _exists(conn: Connection, role: str) -> bool:
    return conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}) is not None


def _tables(conn: Connection) -> list[str]:
    return list(
        conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))
    )


def provision(admin: Engine, passwords: Mapping[str, str]) -> None:
    """Create or repair the four roles in this database and hand the schema to the owner."""
    missing = [role for role in ROLES if not passwords.get(role)]
    if missing:
        raise ValueError(f"No password given for: {', '.join(missing)}")
    with admin.begin() as conn:
        # One at a time: role changes by two processes at once fail.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('tradesphere.provision'))"))
        database = str(conn.scalar(text("SELECT current_database()")))
        for role in ROLES:
            if not _exists(conn, role):
                _run(conn, "CREATE ROLE {}", role)
            bypass = "BYPASSRLS" if role == BACKUP else "NOBYPASSRLS"
            _run(
                conn,
                "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                + bypass
                + " PASSWORD {}",
                role,
                literal=passwords[role],
            )
            _run(conn, "GRANT CONNECT ON DATABASE {} TO {}", database, role)
        _run(conn, "ALTER SCHEMA public OWNER TO {}", OWNER)
        for role in (APP, PLATFORM, BACKUP):
            _run(conn, "GRANT USAGE ON SCHEMA public TO {}", role)
        transfer_ownership(conn, OWNER)
        apply_grants(conn)


def transfer_ownership(conn: Connection, role: str) -> None:
    """Make `role` the owner of every table and function in the public schema (sequences of
    identity columns follow their table). Also the way back: pass the previous owner."""
    for table in _tables(conn):
        _run(conn, "ALTER TABLE {} OWNER TO {}", table, role)
    functions = conn.execute(
        text(
            "SELECT p.oid::regprocedure::text FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND NOT EXISTS ("
            "  SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')"
        )
    ).scalars()
    for signature in list(functions):
        # `signature` comes from the catalog, already quoted by regprocedure.
        quoted = sql.Identifier(role).as_string(conn.connection.driver_connection)
        conn.exec_driver_sql(f"ALTER FUNCTION {signature} OWNER TO {quoted}")


def apply_grants(conn: Connection) -> None:
    """Give each existing role exactly what the table classification allows."""
    tables = _tables(conn)
    if _exists(conn, APP):
        for table in tables:
            _run(conn, "REVOKE ALL ON {} FROM {}", table, APP)
            if table in GLOBAL_TABLES | SYSTEM_TABLES:
                _run(conn, "GRANT SELECT ON {} TO {}", table, APP)
            elif table in APPEND_ONLY_TABLES:
                _run(conn, "GRANT SELECT, INSERT ON {} TO {}", table, APP)
            else:
                _run(conn, "GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}", table, APP)
        _run(conn, "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}", APP)
    if _exists(conn, PLATFORM):
        for table in tables:
            _run(conn, "REVOKE ALL ON {} FROM {}", table, PLATFORM)
            if table in GLOBAL_TABLES | SYSTEM_TABLES:
                _run(conn, "GRANT SELECT ON {} TO {}", table, PLATFORM)
            elif table in PLATFORM_TABLES:
                verbs = (
                    "SELECT, INSERT"
                    if table in APPEND_ONLY_TABLES
                    else ("SELECT, INSERT, UPDATE, DELETE")
                )
                _run(conn, "GRANT " + verbs + " ON {} TO {}", table, PLATFORM)
        _run(conn, "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}", PLATFORM)
    if _exists(conn, BACKUP):
        _run(conn, "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}", BACKUP)
        _run(conn, "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}", BACKUP)


def privileged_reason(conn: Connection) -> str | None:
    """Why the connected role must not serve the application, or None."""
    superuser, bypass = conn.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    if superuser:
        return "it is a superuser"
    if bypass:
        return "it can bypass row-level security"
    return None
