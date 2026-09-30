"""可区分的领域错误 -> HTTP 状态码 / 错误码。"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    status_code: int = 400
    code: str = "error"

    def __init__(self, message: str, details: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidRequest(APIError):
    status_code = 400
    code = "invalid_request"


class InvalidPeriodError(APIError):
    status_code = 422
    code = "invalid_period"


class UnknownTimeZoneError(APIError):
    status_code = 422
    code = "unknown_timezone"


class PointNotFound(APIError):
    status_code = 404
    code = "point_not_found"


class NoPublishedRules(APIError):
    status_code = 409
    code = "no_published_rules"


class VersionConflict(APIError):
    status_code = 409
    code = "version_conflict"


def _envelope(code: str, message: str, details: object | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.details),
    )


async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=_envelope(
            "invalid_request", "Request payload failed validation", exc.errors()
        ),
    )
