"""Application-level exceptions."""


class AppError(Exception):
    """Base for all domain errors."""

    http_status: int = 500
    user_message: str = "Internal error."

    def __init__(self, message: str = "", *, user_message: str | None = None) -> None:
        super().__init__(message or self.user_message)
        if user_message is not None:
            self.user_message = user_message


class NotFoundError(AppError):
    http_status = 404
    user_message = "Not found."


class PermissionDeniedError(AppError):
    http_status = 403
    user_message = "Permission denied."


class ValidationError(AppError):
    http_status = 422
    user_message = "Invalid input."


class ConflictError(AppError):
    http_status = 409
    user_message = "Conflict."


class StateMachineError(AppError):
    """Invalid state transition."""

    http_status = 409
    user_message = "Invalid state transition."


class ExternalServiceError(AppError):
    http_status = 502
    user_message = "External service error."


__all__ = [
    "AppError",
    "ConflictError",
    "ExternalServiceError",
    "NotFoundError",
    "PermissionDeniedError",
    "StateMachineError",
    "ValidationError",
]
