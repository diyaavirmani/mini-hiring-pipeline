"""FastAPI application entry point for the project scaffold."""

from fastapi import FastAPI

from .config import ConfigurationError, get_settings


def create_app() -> FastAPI:
    try:
        get_settings()
    except ConfigurationError as exc:
        raise RuntimeError(f"Invalid application configuration: {exc}") from exc

    application = FastAPI(
        title="Mini Hiring Pipeline",
        description="A candidate pipeline for one recruiter and one job.",
        version="0.1.0",
    )

    @application.get("/healthz", tags=["system"])
    def health_check() -> dict:
        return {"status": "ok"}

    return application


app = create_app()
