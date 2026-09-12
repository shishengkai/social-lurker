"""Stable public errors; raw upstream responses and credentials never cross the CLI."""


class LurkerError(Exception):
    def __init__(
        self, code, message="操作未完成，请查看状态并按提示处理", *, retryable=False, next_action=None
    ):
        super().__init__(code)
        self.code, self.message = code, message
        self.retryable, self.next_action = retryable, next_action

    def public(self):
        return dict(
            code=self.code, message=self.message, retryable=self.retryable, next_action=self.next_action
        )


def require(condition, code, message="输入、实例或状态不符合要求"):
    if not condition:
        raise LurkerError(code, message)
