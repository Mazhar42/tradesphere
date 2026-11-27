from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.core.provision import apply_grants
from app.core.tables import TENANT_TABLES
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# Tests pass an explicit URL; otherwise use the application's DATABASE_URL.
url = config.attributes.get("database_url") or settings.owner_database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = Base.metadata


def include_object(obj: object, name: str | None, type_: str, reflected: bool, _: object) -> bool:
    """What `alembic check` compares. Left out, and covered by tests/test_schema_isolation.py:
    foreign keys of dealership tables (composite in the database, single-column in the models, so
    relationships stay simple), their `(tenant_id, id)` uniques, and the generated party columns.
    """
    table = getattr(getattr(obj, "table", None), "name", None)
    if type_ == "foreign_key_constraint" and table in TENANT_TABLES:
        return False
    if name and name.startswith("uq_tid_"):
        return False
    return not (
        type_ == "column"
        and table == "journal_lines"
        and str(name).startswith("party_")
        and str(name).endswith(("customer_id", "supplier_id"))
    )


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
            # New tables get the grants their classification allows (core/tables.py).
            apply_grants(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
