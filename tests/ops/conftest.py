"""tests/ops 共享 fixture：复用 QUOT-514 的 world（真实宿主适配器 + 端口计划）。

QUOT-515 停服断言直接复用 514 的启动 fixture，不另写一套启动逻辑。conftest 里的
fixture 对本目录所有测试模块自动可见，避免跨测试模块导入 fixture 造成的命名冲突。
"""

from ops.test_lifecycle_cold_start import world

__all__ = ["world"]