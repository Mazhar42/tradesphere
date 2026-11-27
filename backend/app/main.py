import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from sqlalchemy import text

import app.models as _models  # noqa: F401  (register all tables)
from app.core.config import settings
from app.core.context import client_ip_var, request_id_var
from app.core.db import DbSession
from app.core.errors import RetryTransientConflicts, register_exception_handlers
from app.core.observability import access_log, configure_logging
from app.modules.accounting.router import router as accounting_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.claims.router import router as claims_router
from app.modules.finance.router import router as finance_router
from app.modules.inventory.doc_router import router as inventory_docs_router
from app.modules.inventory.router import router as inventory_router
from app.modules.parties.router import router as parties_router
from app.modules.purchasing.return_router import router as purchase_returns_router
from app.modules.purchasing.router import router as purchasing_router
from app.modules.sales.router import router as sales_router
from app.modules.settings.router import router as settings_router
from app.modules.users.router import router as users_router

API_PREFIX = "/api/v1"


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="TradeSphere API",
        version="0.1.0",
        description="Dealership inventory, sales, purchasing, claims and accounts.",
        # Interactive docs and the schema are for development; production does not publish them.
        openapi_url=None if settings.is_production else f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )

    # Added first, so it is the innermost: CORS headers and the access log wrap its answer.
    app.add_middleware(RetryTransientConflicts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        ip = request.client.host if request.client else None
        rid_token = request_id_var.set(request_id[:64])
        ip_token = client_ip_var.set(ip)
        started = time.perf_counter()
        status = 500  # stays 500 when an exception escapes every handler
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            access_log(
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=(time.perf_counter() - started) * 1000,
                ip=ip,
            )
            request_id_var.reset(rid_token)
            client_ip_var.reset(ip_token)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    for router in (
        auth_router,
        users_router,
        catalog_router,
        parties_router,
        purchasing_router,
        sales_router,
        purchase_returns_router,
        inventory_docs_router,
        claims_router,
        finance_router,
        inventory_router,
        accounting_router,
        settings_router,
        audit_router,
    ):
        api.include_router(router)
    app.include_router(api)

    @app.get("/api/health", tags=["health"])
    def health(db: DbSession) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
