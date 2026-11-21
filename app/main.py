import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router
from app.chatgpt.routes import router as chatgpt_router
from app.config import get_settings
from app.db import get_factory
from app.errors import DomainError


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = FastAPI(
        title="Brand Intelligence API",
        version="0.1.0",
        description="Phase 1: website onboarding, evidence-backed drafts and approval.",
    )
    app.include_router(router)
    app.include_router(chatgpt_router)
    # OAuth codes/state in callback query strings must not reach access logs.
    logging.getLogger("uvicorn.access").disabled = True

    @app.exception_handler(DomainError)
    async def domain_error(_, error: DomainError):
        return JSONResponse(
            status_code=error.status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    **({"details": error.details} if error.details else {}),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, error: RequestValidationError):
        # Do not echo request bodies (which can contain secrets) or non-JSON error contexts.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "fields": [
                        {"path": list(e["loc"]), "message": e["msg"]} for e in error.errors()
                    ],
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, error: SQLAlchemyError):
        logging.getLogger(__name__).error("database_error type=%s", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Database operation failed; try again",
                }
            },
        )

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        with get_factory(settings.database_url)() as session:
            session.execute(text("SELECT 1 FROM alembic_version"))
        return {"status": "ready"}

    return app


app = create_app()
