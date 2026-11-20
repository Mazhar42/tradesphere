from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.core.config import settings
from app.core.db import DbSession
from app.core.permissions import CurrentUserDep
from app.modules.auth import service
from app.modules.auth.schemas import LoginRequest, MeRead, TokenResponse
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
    ts_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    ua, ip = _client(request)
    result = service.refresh(db, ts_refresh, ua, ip)
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
    return service.me_from_user(get_user(db, user.id))
