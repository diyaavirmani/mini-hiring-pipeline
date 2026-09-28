"""FastAPI application and authenticated candidate API routes."""

from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    authenticate_recruiter,
    issue_session,
    recruiter_id_for_session,
)
from .config import ConfigurationError, get_settings
from .pipeline import (
    CandidateNotFound,
    DuplicateCandidate,
    FinalStageError,
    InvalidStage,
    InvalidTransition,
    PipelineIntegrityError,
    StageConflict,
    advance_candidate,
    create_candidate,
    get_candidate,
    list_candidates_grouped,
    reject_candidate,
)
from .schemas import CandidateCreateRequest, LoginRequest, RejectRequest, StageRequest


def create_app(
    database_path: Optional[Path] = None,
    secret_key: Optional[str] = None,
    secure_cookie: Optional[bool] = None,
) -> FastAPI:
    if database_path is None or secret_key is None or secure_cookie is None:
        try:
            settings = get_settings()
        except ConfigurationError as exc:
            raise RuntimeError(f"Invalid application configuration: {exc}") from exc
        database_path = database_path or settings.database_path
        secret_key = secret_key or settings.app_secret_key
        if secure_cookie is None:
            secure_cookie = settings.app_env == "production"

    application = FastAPI(
        title="Mini Hiring Pipeline",
        description="Authenticated candidate management and stage history for one recruiter.",
        version="0.2.0",
    )
    application.state.database_path = Path(database_path)
    application.state.secret_key = secret_key
    application.state.secure_cookie = secure_cookie

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ())[1:]),
                "message": error.get("msg", "Invalid value."),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Check the request fields and try again.",
                    "details": details,
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            error = exc.detail
        else:
            error = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error},
            headers=exc.headers,
        )

    @application.exception_handler(CandidateNotFound)
    async def candidate_not_found_handler(request: Request, exc: CandidateNotFound):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "candidate_not_found", "message": str(exc)}},
        )

    @application.exception_handler(DuplicateCandidate)
    async def duplicate_candidate_handler(request: Request, exc: DuplicateCandidate):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "candidate_conflict", "message": str(exc)}},
        )

    @application.exception_handler(StageConflict)
    @application.exception_handler(FinalStageError)
    @application.exception_handler(InvalidTransition)
    async def transition_conflict_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "transition_conflict", "message": str(exc)}},
        )

    @application.exception_handler(InvalidStage)
    async def invalid_stage_handler(request: Request, exc: InvalidStage):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_stage", "message": str(exc)}},
        )

    @application.exception_handler(PipelineIntegrityError)
    async def pipeline_integrity_handler(request: Request, exc: PipelineIntegrityError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "candidate_conflict", "message": str(exc)}},
        )

    def current_recruiter(request: Request) -> int:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(
                status_code=401,
                detail={"code": "authentication_required", "message": "Log in to continue."},
                headers={"WWW-Authenticate": "Cookie"},
            )
        recruiter_id = recruiter_id_for_session(
            application.state.secret_key,
            token,
            application.state.database_path,
        )
        if recruiter_id is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_session", "message": "Log in again to continue."},
                headers={"WWW-Authenticate": "Cookie"},
            )
        return recruiter_id

    @application.get("/healthz", tags=["system"])
    def health_check():
        return {"status": "ok"}

    @application.post("/api/auth/login", tags=["auth"])
    def login(body: LoginRequest, response: Response):
        recruiter = authenticate_recruiter(
            body.email,
            body.password,
            application.state.database_path,
        )
        if recruiter is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_credentials", "message": "Email or password is incorrect."},
                headers={"WWW-Authenticate": "Cookie"},
            )
        response.set_cookie(
            key=SESSION_COOKIE,
            value=issue_session(application.state.secret_key, recruiter["id"]),
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=application.state.secure_cookie,
            samesite="lax",
            path="/",
        )
        return {"recruiter": {"id": recruiter["id"], "email": recruiter["email"]}}

    @application.post("/api/auth/logout", status_code=204, tags=["auth"])
    def logout(response: Response):
        response.delete_cookie(
            key=SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=application.state.secure_cookie,
            samesite="lax",
        )

    @application.get("/api/auth/me", tags=["auth"])
    def get_current_user(recruiter_id: int = Depends(current_recruiter)):
        from .database import connect

        connection = connect(application.state.database_path)
        try:
            recruiter = connection.execute(
                "SELECT id, email, created_at FROM recruiters WHERE id = ?",
                (recruiter_id,),
            ).fetchone()
            return {"recruiter": dict(recruiter)}
        finally:
            connection.close()

    @application.post(
        "/api/candidates", status_code=status.HTTP_201_CREATED, tags=["candidates"]
    )
    def add_candidate(
        body: CandidateCreateRequest,
        recruiter_id: int = Depends(current_recruiter),
    ):
        candidate = create_candidate(
            recruiter_id,
            body.full_name,
            database_path=application.state.database_path,
            email=body.email,
            phone=body.phone,
        )
        return get_candidate(candidate["id"], application.state.database_path)

    @application.get("/api/candidates", tags=["candidates"])
    def list_candidates(recruiter_id: int = Depends(current_recruiter)):
        grouped = list_candidates_grouped(application.state.database_path)
        return {
            "groups": [
                {"stage": stage, "candidates": candidates}
                for stage, candidates in grouped.items()
            ]
        }

    @application.get("/api/candidates/{candidate_id}", tags=["candidates"])
    def open_candidate(
        candidate_id: int,
        recruiter_id: int = Depends(current_recruiter),
    ):
        return get_candidate(candidate_id, application.state.database_path)

    @application.post("/api/candidates/{candidate_id}/advance", tags=["candidates"])
    def advance(
        candidate_id: int,
        body: StageRequest,
        recruiter_id: int = Depends(current_recruiter),
    ):
        advance_candidate(
            candidate_id,
            recruiter_id,
            body.expected_stage,
            database_path=application.state.database_path,
        )
        return get_candidate(candidate_id, application.state.database_path)

    @application.post("/api/candidates/{candidate_id}/reject", tags=["candidates"])
    def reject(
        candidate_id: int,
        body: RejectRequest,
        recruiter_id: int = Depends(current_recruiter),
    ):
        reject_candidate(
            candidate_id,
            recruiter_id,
            body.expected_stage,
            reason=body.reason,
            database_path=application.state.database_path,
        )
        return get_candidate(candidate_id, application.state.database_path)

    return application


app = create_app()
