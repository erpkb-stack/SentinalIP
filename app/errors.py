"""Consistent API error envelope.

Every failure the API returns has the same shape:

    {"success": false, "error": {"code": "...", "message": "...", "details": {...}}}

Stack traces and internal messages never reach the client.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import get_logger

logger = get_logger(__name__)


class AppError(HTTPException):
    """Base class for all deliberate application errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"
    message: str = "The request could not be processed."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.code = code or self.code
        self.message = message or self.message
        self.details = details or {}
        super().__init__(
            status_code=status_code or self.status_code,
            detail=self.message,
            headers=headers,
        )

    def to_payload(self) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"success": False, "error": err}


# --------------------------------------------------------------------------- #
class ValidationFailed(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"
    message = "The submitted data is not valid."


class NotAuthenticated(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "NOT_AUTHENTICATED"
    message = "Authentication is required to access this resource."


class InvalidCredentials(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "INVALID_CREDENTIALS"
    message = "Email or password is incorrect."


class SessionExpired(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "SESSION_EXPIRED"
    message = "Your session has expired. Please sign in again."


class AccountLocked(AppError):
    status_code = status.HTTP_423_LOCKED
    code = "ACCOUNT_LOCKED"
    message = "This account is temporarily locked after repeated failed sign-in attempts."


class AccountDisabled(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "ACCOUNT_DISABLED"
    message = "This account is disabled. Contact your administrator."


class PermissionDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"
    message = "You do not have permission to perform this action."


class VendorAccessDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "VENDOR_ACCESS_DENIED"
    message = "You do not have permission to access this record."


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "The request conflicts with the current state of the resource."


class ApprovalRequired(AppError):
    """The human approval gate. Raised whenever an enforcement action is
    attempted without a recorded human approval."""

    status_code = status.HTTP_409_CONFLICT
    code = "HUMAN_APPROVAL_REQUIRED"
    message = (
        "This enforcement action requires a recorded human approval before it "
        "can be filed."
    )


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    message = "Too many requests. Please wait and try again."


class UploadRejected(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "UPLOAD_REJECTED"
    message = "The uploaded file was rejected."


class CsrfFailed(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "CSRF_FAILED"
    message = "Request rejected: invalid or missing CSRF token."


# --------------------------------------------------------------------------- #
def _wants_html(request: Request) -> bool:
    if request.url.path.startswith(("/api", "/ws")):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def register_exception_handlers(app) -> None:
    from fastapi.responses import RedirectResponse

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        if exc.status_code in (401,) and _wants_html(request):
            return RedirectResponse(
                url=f"/login?next={request.url.path}&reason={exc.code.lower()}",
                status_code=302,
            )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 401 and _wants_html(request):
            return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
        code = {
            400: "BAD_REQUEST",
            401: "NOT_AUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "HTTP_ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": code, "message": detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query")]
            fields.append({"field": ".".join(loc) or "body", "message": err.get("msg", "")})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "One or more fields are invalid.",
                    "details": {"fields": fields},
                },
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        cid = getattr(request.state, "correlation_id", None)
        # Full detail goes to the log; the client gets an opaque reference.
        logger.exception("Unhandled error [%s] on %s %s", cid, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred. "
                               "Quote the reference below if you contact support.",
                    "details": {"reference": cid},
                },
            },
        )
