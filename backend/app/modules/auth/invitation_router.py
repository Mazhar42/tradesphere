from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.db import DbSession
from app.core.permissions import STEP_UP, CurrentUser, require
from app.modules.auth import accounts
from app.modules.users.schemas import InvitationCreate, InvitationCreated, InvitationRead

router = APIRouter(prefix="/invitations", tags=["users"])

CanRead = Annotated[CurrentUser, Depends(require("users:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("users:write"))]


@router.get("", response_model=list[InvitationRead])
def list_invitations(db: DbSession, _: CanRead) -> list[InvitationRead]:
    return [InvitationRead.model_validate(i) for i in accounts.list_invitations(db)]


@router.post(
    "", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED, dependencies=STEP_UP
)
def create_invitation(db: DbSession, actor: CanWrite, data: InvitationCreate) -> InvitationCreated:
    invitation, link = accounts.invite(
        db, email_address=data.email, note=data.note, roles=data.roles, actor=actor
    )
    result = InvitationCreated(invitation=InvitationRead.model_validate(invitation), link=link)
    db.commit()
    return result


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(db: DbSession, actor: CanWrite, invitation_id: int) -> None:
    accounts.revoke_invitation(db, invitation_id, actor)
    db.commit()
