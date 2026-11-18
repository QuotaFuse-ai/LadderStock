"""FinanceCoffer 增强包（DEC-086）。

按六条决策落地：embedding 由用户勾选、Redis 社区移植版三端各一份 + 单一入口、
体积明细必确认、数据只落安装目录内（C 盘强提示）、卸载整目录删除、不做轻量模式。

对外入口：

```python
from ladderstock.modules import financecoffer
financecoffer.plan(with_embedding=True)      # 向导第一步：体积明细 + 盘位检查
financecoffer.install(confirm=True, ...)     # 完整包一条路径
financecoffer.uninstall(confirm=True)        # 整目录删除
financecoffer.redis.status()                 # Redis 单一入口
```
"""

from __future__ import annotations

from ladderstock.modules.financecoffer import installer, layout, manifest, redis_service, state

install = installer.install
plan = installer.plan
uninstall = installer.uninstall
read_state = state.read_state
redis = redis_service

__all__ = [
    "install",
    "installer",
    "layout",
    "manifest",
    "plan",
    "read_state",
    "redis",
    "redis_service",
    "state",
    "uninstall",
]
