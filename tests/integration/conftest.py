"""tests/integration 共享 fixture：复用 QUOT-545 的 world（真实临时进程 + 端口红线）。

QUOT-556 健康事件端到端直接复用 545 的进程世界（临时端口 + 生产端口守卫 +
兜底清理），不另写一套启动逻辑。conftest 里的 fixture 对本目录所有测试模块
自动可见，避免跨测试模块导入 fixture 造成的命名冲突（同 tests/ops 形态）。
"""

from test_stop_all_ownership import world

__all__ = ["world"]