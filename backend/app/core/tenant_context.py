"""Which dealership (and person) a database session is working for.

The ids are kept on the session and written to `app.tenant_id` and `app.user_id` at the start
of **every** transaction, with `set_config(..., true)`: transaction-local, so a pooled
connection carries nothing to the next request. Routers commit and then read back, which is why
setting it once per request would not be enough. Row-level security reads these settings.

The ORM also adds `tenant_id = <current>` to every query of a tenant model. That is the first of
three isolation layers (with composite keys and row-level security); each is tested with the
others switched off. `session.info["unfiltered"]` switches this one off, for those tests only.
"""

from typing import Any

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.errors import ForbiddenError
from app.shared.models import TenantScoped

_SETTINGS = {"tenant_id": "app.tenant_id", "user_id": "app.user_id"}


def _write(connection: Connection, key: str, value: int | None) -> None:
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": _SETTINGS[key], "value": "" if value is None else str(value)},
    )


def read_only(session: Session) -> None:
    """Every transaction of this session from now on refuses to write."""
    session.info["read_only"] = True
    if session.in_transaction():
        session.execute(text("SET TRANSACTION READ ONLY"))


@event.listens_for(Session, "after_begin")
def _apply_context(session: Session, _: Any, connection: Connection) -> None:
    if session.info.get("read_only"):
        connection.execute(text("SET TRANSACTION READ ONLY"))
    for key in _SETTINGS:
        if session.info.get(key) is not None:
            _write(connection, key, session.info[key])


@event.listens_for(Session, "do_orm_execute")
def _only_this_tenant(state: ORMExecuteState) -> None:
    tenant_id = state.session.info.get("tenant_id")
    if tenant_id is None or state.session.info.get("unfiltered"):
        return
    # Column and relationship loads inherit the criteria of the query that started them.
    if state.is_column_load or state.is_relationship_load:
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantScoped, lambda cls: cls.tenant_id == tenant_id, include_aliases=True
            )
        )


def set_context(
    session: Session, *, tenant_id: int | None = None, user_id: int | None = None
) -> None:
    """Add to the session's context (None leaves a value as it is)."""
    for key, value in (("tenant_id", tenant_id), ("user_id", user_id)):
        if value is None:
            continue
        session.info[key] = value
        if session.in_transaction():
            _write(session.connection(), key, value)


def clear_context(session: Session) -> None:
    for key in _SETTINGS:
        session.info.pop(key, None)
        if session.in_transaction():
            _write(session.connection(), key, None)


def tenant_id_of(session: Session) -> int:
    """The tenant the session works for. Refuses to guess."""
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        raise ForbiddenError("No dealership is selected.", code="no_tenant_context")
    return int(tenant_id)
