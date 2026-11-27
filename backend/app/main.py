import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from sentry_sdk import set_tag
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.models as _models  # noqa: F401  (register all tables)
from app.core.config import settings
from app.core.context import client_ip_var, request_id_var
from app.core.db import check_database_role
from app.core.errors import RetryTransientConflicts, register_exception_handlers
from app.core.health import router as health_router
from app.core.observability import access_log, configure_logging, init_error_tracking
from app.modules.accounting.router import router as accounting_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.invitation_router import router as invitations_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.catalog.router import router as catalog_router
from app.modules.claims.router import router as claims_router
from app.modules.finance.router import router as finance_router
from app.modules.imports.router import router as imports_router
from app.modules.integrity.router import router as integrity_router
from app.modules.inventory.doc_router import router as inventory_docs_router
from app.modules.inventory.router import router as inventory_router
from app.modules.opening.router import router as opening_router
from app.modules.parties.router import router as parties_router
from app.modules.platform.billing_router import router as platform_billing_router
from app.modules.platform.router import router as platform_router
from app.modules.platform.support_router import router as support_router
from app.modules.purchasing.return_router import router as purchase_returns_router
from app.modules.purchasing.router import router as purchasing_router
from app.modules.sales.router import router as sales_router
from app.modules.settings.router import router as settings_router
from app.modules.users.router import router as users_router
from app.shared.business import business_scope
from app.worker import Worker

API_PREFIX = "/api/v1"


def _operation_id(route: APIRoute) -> str:
    return route.name


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_database_role()
    stop = threading.Event()
    thread = None
    if settings.run_worker_in_process:
        thread = threading.Thread(target=Worker().forever, args=(stop,), daemon=True)
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=10)


def create_app() -> FastAPI:
    configure_logging()
    if settings.sentry_dsn and settings.sentry_dsn.get_secret_value():
        init_error_tracking(settings.sentry_dsn.get_secret_value())
    app = FastAPI(
        lifespan=_lifespan,
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
        set_tag("request_id", request_id[:64])  # does nothing while error tracking is off
        started = time.perf_counter()
        status = 500  # stays 500 when an exception escapes every handler
        try:
            with business_scope():
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

    # Added last, so it is the outermost: everything inside sees the real client address.
    # Uvicorn's own handling is switched off (--no-proxy-headers) so this is the only one.
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=settings.trusted_proxies,
    )

    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    for router in (
        auth_router,
        users_router,
        invitations_router,
        support_router,
        platform_router,
        platform_billing_router,
        billing_router,
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
        opening_router,
        integrity_router,
        imports_router,
        settings_router,
        audit_router,
    ):
        api.include_router(router)
    app.include_router(api)
    app.include_router(health_router)

    return app


app = create_app()
