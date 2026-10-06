"""Public errors contain fixed messages; never upstream text or user arguments."""

MESSAGES = {
    "INPUT_INVALID": "命令或参数无效，请查看 --help",
    "PROFILE_NOT_FOUND": "指定 profile 不存在",
    "AUTHOR_NOT_FOUND": "作者尚未登记",
    "PLATFORM_UNSUPPORTED": "不支持该平台",
    "SOURCE_LINK_INVALID": "分享链接无效或不受支持",
    "CONFIG_INVALID": "本地配置无法读取或格式无效",
    "CREDENTIAL_MISSING": "未配置 TikHub 凭据",
    "PROFILE_BUSY": "该 profile 正在执行其他操作",
    "MAINTENANCE_BUSY": "安装正在维护或被业务占用",
    "REQUEST_TIMEOUT": "供应商请求超时",
    "NETWORK_ERROR": "网络请求失败",
    "RATE_LIMITED": "供应商限流，已停止后续请求",
    "AUTH_FAILED": "供应商认证或权限不足",
    "QUOTA_UNAVAILABLE": "供应商账户额度不可用",
    "PAGE_INVALID": "供应商未返回有效页面结构",
    "PAGE_IDENTITY_INVALID": "页面身份缺失或冲突",
    "CURSOR_INVALID": "分页游标或页面重复、无效",
    "DB_ERROR": "数据库身份、版本或操作无效",
    "UPGRADE_FAILED": "软件包、升级或恢复验证失败",
    "INTERRUPTED": "调用已中断",
    "OUTPUT_CLOSED": "输出已关闭",
}
GLOBAL_CODES = {"AUTH_FAILED", "QUOTA_UNAVAILABLE", "RATE_LIMITED"}


class LurkerError(Exception):
    def __init__(self, code, *, exit_code=None):
        self.code = code
        self.exit_code = (
            exit_code
            if exit_code is not None
            else {
                "INPUT_INVALID": 2,
                "PROFILE_NOT_FOUND": 2,
                "SOURCE_LINK_INVALID": 2,
                "PLATFORM_UNSUPPORTED": 2,
                "PROFILE_BUSY": 4,
                "MAINTENANCE_BUSY": 4,
                "CONFIG_INVALID": 5,
                "CREDENTIAL_MISSING": 5,
                "OUTPUT_CLOSED": 141,
            }.get(code, 1)
        )
        super().__init__(MESSAGES.get(code, "调用失败"))

    @property
    def global_stop(self):
        return self.code in GLOBAL_CODES

    def public(self):
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.code
            in {"REQUEST_TIMEOUT", "NETWORK_ERROR", "RATE_LIMITED", "PROFILE_BUSY", "MAINTENANCE_BUSY"},
        }


def require(condition, code="PAGE_INVALID"):
    if not condition:
        raise LurkerError(code)
