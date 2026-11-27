from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.core.config import settings
from app.core.db import DbSession
from app.core.permissions import CurrentUserDep
from app.modules.auth import accounts, service
from app.modules.auth.schemas import (
    EmailChange,
    InvitationAccept,
    InvitationPreview,
    LoginRequest,
    MeRead,
    PasswordChange,
    RefreshRequest,
    ResetConfirm,
    ResetRequest,
    TokenOnly,
    TokenResponse,
)
from app.modules.users.service import get_user

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "ts_refresh"
COOKIE_PATH = "/api/v1/auth"


def _client(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), request.client.host if request.client else None


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path=COOKIE_PATH,
    )


@router.post("/login", response_model=TokenResponse)
def login(db: DbSession, data: LoginRequest, request: Request, response: Response) -> TokenResponse:
    ua, ip = _client(request)
    result = service.login(db, data.username, data.password, ua, ip)
    db.commit()
    if result.refresh_token:
        _set_refresh_cookie(response, result.refresh_token)
    return result.response


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    db: DbSession,
    request: Request,
    response: Response,
    data: RefreshRequest | None = None,
    ts_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    ua, ip = _client(request)
    result = service.refresh(db, ts_refresh, ua, ip, data.tenant_id if data else None)
    db.commit()
    if result.refresh_token:
        _set_refresh_cookie(response, result.refresh_token)
    return result.response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: DbSession, response: Response, ts_refresh: Annotated[str | None, Cookie()] = None
) -> None:
    service.logout(db, ts_refresh)
    db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.get("/me", response_model=MeRead)
def me(db: DbSession, user: CurrentUserDep) -> MeRead:
    assert user.tenant_id is not None
    return service.me_from_user(db, get_user(db, user.id), user.tenant_id)


# --- Invitations, recovery and verification -------------------------------------------------


@router.post("/invitations/inspect", response_model=InvitationPreview)
def inspect_invitation(db: DbSession, data: TokenOnly) -> InvitationPreview:
    invitation, tenant = accounts.open_invitation(db, data.token)
    return InvitationPreview(dealership=tenant.legal_name, email=invitation.email)


@router.post("/invitations/accept", status_code=status.HTTP_204_NO_CONTENT)
def accept_invitation(db: DbSession, data: InvitationAccept) -> None:
    accounts.accept_invitation(
        db,
        token=data.token,
        username=data.username,
        full_name=data.full_name,
        password=data.password,
    )
    db.commit()


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(db: DbSession, data: ResetRequest, request: Request) -> None:
    accounts.request_password_reset(db, data.identifier, _client(request)[1])
    db.commit()


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(db: DbSession, data: ResetConfirm) -> None:
    accounts.reset_password_with_token(db, data.token, data.new_password)
    db.commit()


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
def verify_email(db: DbSession, data: TokenOnly) -> None:
    accounts.verify_email(db, data.token)
    db.commit()


@router.post("/me/email", status_code=status.HTTP_202_ACCEPTED)
def set_email(db: DbSession, user: CurrentUserDep, data: EmailChange) -> None:
    accounts.set_own_email(db, user.id, data.email, data.password)
    db.commit()


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(db: DbSession, user: CurrentUserDep, data: PasswordChange) -> None:
    accounts.change_own_password(db, user.id, data.current_password, data.new_password)
    db.commit()
