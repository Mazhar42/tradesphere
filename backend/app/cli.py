"""Operational commands: `python -m app.cli --help`."""

from datetime import datetime
from typing import Annotated

import typer

import app.models as _models  # noqa: F401  (register all tables)
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
    with SessionLocal() as session:
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


if __name__ == "__main__":
    cli()
