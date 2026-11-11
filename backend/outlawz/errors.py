"""Errors that are safe to show to the user.

Every error the API returns has a short machine code and a plain-language message. Messages never
contain file system paths outside the library, keys or stack traces.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """An error with a code, a plain-language message and an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        if request_id:
            body["request_id"] = request_id
        return {"error": body}


class NotFound(AppError):
    def __init__(self, what: str = "That item") -> None:
        super().__init__("not_found", f"{what} doesn't exist (or was deleted).", 404)


class LibraryLocked(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("library_locked", message, 409)


class DocumentError(Exception):
    """A document can't be read. Not retried: the file itself is the problem."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TransientError(Exception):
    """A temporary failure (file busy, model server not answering). Retried with backoff."""
