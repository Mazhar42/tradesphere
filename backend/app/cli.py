"""Operational commands: `python -m app.cli --help`."""

import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models as _models  # noqa: F401  (register all tables)
from app.bootstrap import ensure_owner, ensure_tenant
from app.core import provision
from app.core.config import settings
from app.core.db import SessionLocal, check_database_role
from app.core.observability import configure_logging, init_error_tracking
from app.core.tenant_context import set_context
from app.modules.integrity.reconcile import reconcile as run_reconcile
from app.modules.inventory.rebuild import rebuild_stock
from app.modules.platform.service import add_operator
from app.modules.sales.rebuild import rebuild_sales_summary
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.service import all_tenants as all_dealerships
from app.modules.tenancy.service import business_config
from app.modules.users.schemas import UserCreate
from app.modules.users.service import count_active_owners, create_user, get_by_username
from app.seed import provision_tenant, run_seed
from app.shared.business import BusinessConfig, business_scope, use_business
from app.worker import Worker

cli = typer.Typer(no_args_is_help=True, add_completion=False)

TenantOption = Annotated[str | None, typer.Option("--tenant", help="Dealership slug")]
AllTenants = Annotated[bool, typer.Option("--all-tenants", help="Every dealership in turn")]


def _platform_session() -> Session:
    return Session(create_engine(settings.platform_url))


def _selected(slug: str | None, all_tenants: bool = False) -> list[tuple[int, str]]:
    """The dealerships a command works on. Nothing runs without one."""
    with _platform_session() as session:
        found = [(t.id, t.slug, business_config(t)) for t in all_dealerships(session)]
    if slug:
        found = [t for t in found if t[1] == slug]
        if not found:
            typer.echo(f"No dealership '{slug}'.", err=True)
            raise typer.Exit(2)
    elif not all_tenants and len(found) != 1:
        typer.echo("Name a dealership with --tenant, or use --all-tenants.", err=True)
        raise typer.Exit(2)
    return [(tenant_id, name) for tenant_id, name, _ in found]


@contextmanager
def _in_tenant(tenant_id: int) -> Iterator[Session]:
    """A session and business context for one dealership."""
    with business_scope(), SessionLocal() as session:
        set_context(session, tenant_id=tenant_id)
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None
        use_business(business_config(tenant))
        yield session


@cli.command()
def seed() -> None:
    """Create or update reference data (permissions, roles, units, accounts...)."""
    owner = create_engine(settings.owner_database_url)
    with Session(owner, autoflush=False, expire_on_commit=False) as session:
        run_seed(session)
        session.commit()
    typer.echo("Seed complete.")


@cli.command("create-owner")
def create_owner(
    username: Annotated[str, typer.Option(prompt=True)],
    full_name: Annotated[str, typer.Option(prompt=True)],
    password: Annotated[str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)],
    tenant: TenantOption = None,
) -> None:
    """Create a user with the owner role (first-time setup)."""
    with SessionLocal() as session:
        if tenant:
            set_context(session, tenant_id=_selected(tenant)[0][0])
        else:
            with _platform_session() as platform:
                ensure_tenant(platform, session)
        if get_by_username(session, username):
            typer.echo(f"User '{username}' already exists.", err=True)
            raise typer.Exit(1)
        user = create_user(
            session,
            UserCreate(username=username, full_name=full_name, password=password, roles=["owner"]),
            actor=None,
        )
        session.commit()
        typer.echo(
            f"Owner '{user.username}' created. Active owners: {count_active_owners(session)}."
        )


@cli.command()
def reconcile(tenant: TenantOption = None, all_tenants: AllTenants = False) -> None:
    """Check stock/cost projections and the books against the ledgers (exit 1 on drift)."""
    drift = False
    for tenant_id, slug in _selected(tenant, all_tenants):
        with _in_tenant(tenant_id) as session:
            result = run_reconcile(session)
        if result.ok:
            typer.echo(
                f"{slug}: Reconcile OK: stock, cost, inventory account and trial balance agree."
            )
        for issue in result.issues:
            drift = True
            typer.echo(f"{slug}: [{issue.check}] {issue.detail}", err=True)
    if drift:
        raise typer.Exit(1)


@cli.command("rebuild-stock")
def rebuild_stock_command(tenant: TenantOption = None, all_tenants: AllTenants = False) -> None:
    """Rewrite stock balances and costs from the movement ledger. Stop posting first."""
    for tenant_id, slug in _selected(tenant, all_tenants):
        with _in_tenant(tenant_id) as session:
            balances, costs = rebuild_stock(session)
            session.commit()
        typer.echo(f"{slug}: rewrote {balances} balance rows and {costs} cost rows.")


@cli.command("rebuild-projections")
def rebuild_projections(
    from_date: Annotated[datetime | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    tenant: TenantOption = None,
    all_tenants: AllTenants = False,
) -> None:
    """Rebuild the daily sales projection from posted invoices and returns."""
    for tenant_id, slug in _selected(tenant, all_tenants):
        with _in_tenant(tenant_id) as session:
            count = rebuild_sales_summary(session, from_date.date() if from_date else None)
            session.commit()
        typer.echo(f"{slug}: rebuilt the daily sales projection from {count} documents.")


@cli.command("bootstrap-owner")
def bootstrap_owner() -> None:
    """Create the first owner from BOOTSTRAP_OWNER_* variables, if no owner exists yet."""
    username = os.environ.get("BOOTSTRAP_OWNER_USERNAME")
    password = os.environ.get("BOOTSTRAP_OWNER_PASSWORD")
    if not username or not password:
        typer.echo("Owner bootstrap skipped: BOOTSTRAP_OWNER_USERNAME/PASSWORD not set.")
        return
    full_name = os.environ.get("BOOTSTRAP_OWNER_FULL_NAME", username)
    with SessionLocal() as session, _platform_session() as platform:
        if len(all_dealerships(platform)) > 1:
            typer.echo("Several dealerships exist; nothing to bootstrap.")
            return
        ensure_tenant(platform, session)
        created = ensure_owner(session, username=username, full_name=full_name, password=password)
        session.commit()
    typer.echo("Owner created." if created else "Owner already exists; nothing to do.")


@cli.command("provision-tenant")
def provision_tenant_command(
    slug: Annotated[str, typer.Option(help="Short name, e.g. rahman-traders")],
    name: Annotated[str, typer.Option(help="Legal name of the dealership")],
    timezone: Annotated[str, typer.Option()] = settings.business_timezone,
    fiscal_year_start_month: Annotated[int, typer.Option()] = settings.fiscal_year_start_month,
) -> None:
    """Create a dealership with its reference data. Then add its owner with `create-owner`."""
    with business_scope(), SessionLocal() as session:
        provision_tenant(
            session,
            slug=slug,
            legal_name=name,
            config=BusinessConfig(timezone, fiscal_year_start_month),
        )
        session.commit()
    typer.echo(f"Dealership '{slug}' created.")


@cli.command("platform-operator")
def platform_operator(
    username: Annotated[str, typer.Option(help="An existing account")],
) -> None:
    """Make a person a platform operator. Shell access is the only way in."""
    with _platform_session() as session:
        uri, codes = add_operator(session, username)
        session.commit()
    typer.echo(f"'{username}' is a platform operator.")
    if uri:
        typer.echo(f"Add to an authenticator app (shown once):\n{uri}")
        typer.echo("Recovery codes (shown once):\n" + "\n".join(codes))


@cli.command("worker")
def worker_command() -> None:
    """Run background jobs until stopped."""
    configure_logging()
    if settings.sentry_dsn and settings.sentry_dsn.get_secret_value():
        init_error_tracking(settings.sentry_dsn.get_secret_value())
    check_database_role()
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    Worker().forever(stop)


@cli.command("provision-db")
def provision_db() -> None:
    """Create the database roles and hand them the schema (needs a superuser; safe to repeat).

    Reads ADMIN_DATABASE_URL and OWNER_DB_PASSWORD, APP_DB_PASSWORD, PLATFORM_DB_PASSWORD,
    BACKUP_DB_PASSWORD.
    """
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        typer.echo("Set ADMIN_DATABASE_URL to a superuser connection.", err=True)
        raise typer.Exit(2)
    passwords = {
        provision.OWNER: os.environ.get("OWNER_DB_PASSWORD", ""),
        provision.APP: os.environ.get("APP_DB_PASSWORD", ""),
        provision.PLATFORM: os.environ.get("PLATFORM_DB_PASSWORD", ""),
        provision.BACKUP: os.environ.get("BACKUP_DB_PASSWORD", ""),
    }
    provision.provision(create_engine(url), passwords)
    typer.echo("Database roles are provisioned.")


if __name__ == "__main__":
    cli()
