class LurkerError(Exception):
    """Only curated messages may cross the CLI boundary, never upstream bodies."""

    def __init__(self, code, message, *, retryable=False, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after

    def public(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def require(condition, code, message):
    if not condition:
        raise LurkerError(code, message)
