"""Service errors with an HTTP status and a message (03 "Errors")."""
from __future__ import annotations


class MemError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message}


class BadRequest(MemError):
    status = 400
    code = "bad_request"


class Unauthorized(MemError):
    status = 401
    code = "unauthorized"


class Forbidden(MemError):
    status = 403
    code = "forbidden"


class NotFound(MemError):
    status = 404
    code = "not_found"


class Conflict(MemError):
    status = 409
    code = "conflict"


class EmbedderUnavailable(MemError):
    status = 503
    code = "embedder_unavailable"
