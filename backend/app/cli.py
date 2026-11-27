"""Operational commands: `python -m app.cli --help`."""

import os
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models as _models  # noqa: F401  (register all tables)
from app.bootstrap import ensure_owner
from app.core import provision
from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.inventory.reconcile import reconcile as run_reconcile
from app.modules.sales.rebuild import rebuild_sales_summary
from app.modules.users.schemas import UserCreate
from app.modules.users.service import count_active_owners, create_user, get_by_username
from app.seed import run_seed

cli = typer.Typer(no_args_is_help=True, add_completion=False)


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
) -> None:
    """Create a user with the owner role (first-time setup)."""
    with SessionLocal() as session:
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
def reconcile() -> None:
    """Check stock/cost projections and the books against the ledgers (exit 1 on drift)."""
    with SessionLocal() as session:
        result = run_reconcile(session)
    if result.ok:
        typer.echo("Reconcile OK: stock, cost, inventory account and trial balance agree.")
        return
    for issue in result.issues:
        typer.echo(f"[{issue.check}] {issue.detail}", err=True)
    raise typer.Exit(1)


@cli.command("rebuild-projections")
def rebuild_projections(
    from_date: Annotated[datetime | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
) -> None:
    """Rebuild the daily sales projection from posted invoices and returns."""
    with SessionLocal() as session:
        count = rebuild_sales_summary(session, from_date.date() if from_date else None)
        session.commit()
    typer.echo(f"Rebuilt the daily sales projection from {count} documents.")


@cli.command("bootstrap-owner")
def bootstrap_owner() -> None:
    """Create the first owner from BOOTSTRAP_OWNER_* variables, if no owner exists yet."""
    username = os.environ.get("BOOTSTRAP_OWNER_USERNAME")
    password = os.environ.get("BOOTSTRAP_OWNER_PASSWORD")
    if not username or not password:
        typer.echo("Owner bootstrap skipped: BOOTSTRAP_OWNER_USERNAME/PASSWORD not set.")
        return
    full_name = os.environ.get("BOOTSTRAP_OWNER_FULL_NAME", username)
    with SessionLocal() as session:
        created = ensure_owner(session, username=username, full_name=full_name, password=password)
        session.commit()
    typer.echo("Owner created." if created else "Owner already exists; nothing to do.")


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
