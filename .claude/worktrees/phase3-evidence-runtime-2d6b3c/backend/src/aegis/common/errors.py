"""Typed error hierarchy (DEVELOPMENT_STANDARDS §5)."""


class AegisError(Exception):
    """Base class for expected, typed failures."""


class AegisValidationError(AegisError):
    pass


class PermissionDenied(AegisError):
    pass


class NotFound(AegisError):
    pass


class ConflictError(AegisError):
    pass


class ExternalServiceError(AegisError):
    pass


class BudgetExceeded(AegisError):
    pass


class IntegrityError(AegisError):
    pass
