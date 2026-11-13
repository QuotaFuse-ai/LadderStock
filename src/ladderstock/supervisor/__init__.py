"""supervisor 生命周期内核（QUOT-482 系列）。

本包现在由两部分共存：

- **QUOT-482-A（本单）**——进程账本 + 顺序启停引擎 + 归属判定：
  - `adapters` —— 模块注册表（顺序 / 端口白名单，引用 `modules` 单源）与
    `ModuleAdapter` 协议；482-B 的逐模块实现只依赖此协议。
  - `ledger` —— 进程账本：每模块一条记录，原子落盘
    `data/supervisor/ledger.json`，崩溃恢复时对账。归属判定只认账本 +
    cmdline 匹配，不扫端口猜。该文件**平替** QUOT-491 的最小承载账本，
    自愈内核（`selfheal`）零改动继续工作。
  - `proctree` —— 进程树操作（存活 / cmdline / 父子链 / 整树终止）；
    Windows 走 taskkill /T，全部可注入，测试用假实现。
  - `engine` —— 顺序启停引擎：`start_all / stop_all / restart_module / state`，
    并发互斥，停止路径带超时升级强杀并记 `forced_kill`。

- **QUOT-491（482-C）**——崩溃自愈 + 通知 + needs_human 失败态：
  `selfheal` / `notify` / `restart` / `logroll`。其重拉通道只认注入的
  命令表 / 适配器（482-B 的职责），本包不写任何模块启动命令。
"""

from ladderstock.supervisor.adapters import (
    MODULE_ORDER,
    ModuleAdapter,
    SpawnedProcess,
    module_registry,
)
from ladderstock.supervisor.engine import SupervisorEngine
from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    HEALTH_STOPPED,
    Ledger,
    LedgerEntry,
    ModuleRecord,
)
from ladderstock.supervisor.modules import (
    MODULE_PORTS,
    current_env,
    module_ports,
    reset_env,
    select_env,
)
from ladderstock.supervisor.proctree import (
    ProcessOps,
    default_process_ops,
    split_windows_commandline,
)
from ladderstock.supervisor.selfheal import SelfHealEngine

__all__ = [
    "HEALTH_NEEDS_HUMAN",
    "HEALTH_OK",
    "HEALTH_RESTARTING",
    "HEALTH_STOPPED",
    "MODULE_ORDER",
    "MODULE_PORTS",
    "Ledger",
    "LedgerEntry",
    "ModuleAdapter",
    "ModuleRecord",
    "ProcessOps",
    "SelfHealEngine",
    "SpawnedProcess",
    "SupervisorEngine",
    "current_env",
    "module_ports",
    "reset_env",
    "select_env",
    "default_process_ops",
    "module_registry",
    "split_windows_commandline",
]
