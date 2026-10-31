"""退出码与错误约定（与 docs/CLI.md §F 一致）。"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_SOURCE = 4
EXIT_NOT_FOUND = 6
EXIT_PARTIAL = 8
EXIT_AI_DEGRADED = 9
#: 本机历史库被另一个进程占着（跨文件锁争用，QUOT-234）。
#: 不复用 4/8——那两个码的主语是「生态 peer 数据源」，报它们会把排查方向
#: 引到换源、重试取数上；库锁争用是本机资源串行问题，脚本侧要单独能分流。
#: 不属于对外契约的 6 个码。
EXIT_DB_BUSY = 10
# 施工期专用：命令已注册、能力尚未实现。不属于对外契约的 6 个码。
EXIT_UNIMPLEMENTED = 70


class LadderStockError(Exception):
    """带退出码的业务异常。"""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
