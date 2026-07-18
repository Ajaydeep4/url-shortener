class DomainError(Exception):
    """Base class for expected business errors mapped to HTTP responses."""

    status_code = 500
    error_code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidAliasError(DomainError):
    status_code = 422
    error_code = "invalid_alias"


class InvalidUrlError(DomainError):
    status_code = 422
    error_code = "invalid_url"


class AliasAlreadyExistsError(DomainError):
    status_code = 409
    error_code = "alias_conflict"


class AliasNotFoundError(DomainError):
    status_code = 404
    error_code = "not_found"


class AliasGenerationExhaustedError(DomainError):
    status_code = 503
    error_code = "alias_generation_exhausted"
