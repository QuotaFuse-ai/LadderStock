"""退出码与错误约定（与 docs/CLI.md §F 一致）。"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_SOURCE = 4
EXIT_NOT_FOUND = 6
EXIT_PARTIAL = 8
EXIT_AI_DEGRADED = 9
# 施工期专用：命令已注册、能力尚未实现。不属于对外契约的 6 个码。
EXIT_UNIMPLEMENTED = 70


class LadderStockError(Exception):
    """带退出码的业务异常。"""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
