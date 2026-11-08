"""supervisor 生命周期与自愈（QUOT-482 系列）。

本包由 **QUOT-491（482-C）** 落地「崩溃自愈 + 通知 + needs_human 失败态」。

依赖现状（写码时的真实状态）：QUOT-482-A（账本 + 顺序启停引擎 + 归属判定）与
QUOT-482-B（模块适配器）两次运行失败、**尚未合并**。因此本包自带一个**最小账本承载**
（`ledger.py`）与一个由外部命令表驱动的重拉通道（`restart.py`），并严格守住边界：

- 不实现启停顺序、不做归属判定（482-A 的职责）——账本的 `origin` 字段只存不判；
- 不写任何模块启动命令（482-B 的职责）——重拉只认注入的命令表/适配器，表空即如实报「无通道」；
- 不改 `frontend/`、不改任何 peer 仓库。

482-A/482-B 落地后，把 `SelfHealEngine` 的 `ledger` 与 `restarter` 换成其实现即可，
自愈内核无需改动。
"""

from __future__ import annotations

from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    HEALTH_STOPPED,
    Ledger,
    LedgerEntry,
)
from ladderstock.supervisor.selfheal import SelfHealEngine

__all__ = [
    "HEALTH_NEEDS_HUMAN",
    "HEALTH_OK",
    "HEALTH_RESTARTING",
    "HEALTH_STOPPED",
    "Ledger",
    "LedgerEntry",
    "SelfHealEngine",
]
