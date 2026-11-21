class DomainError(Exception):
    """Safe, machine-readable errors; raw upstream responses must never reach API clients."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        retryable: bool = False,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details
        super().__init__(message)
