"""supervisor 的模块注册表与适配器协议（QUOT-482-A 定契约，482-B 出实现）。

**顺序与端口是本单的判过证据口径**（QUOT-482 派单正文实测表）：

| 顺序 | 模块 | 端口 |
|---|---|---|
| 1 | tickhub | 8000 MCP / 8787 管理端 |
| 2 | tickhub_us | 8002 MCP |
| 3 | wire | 8790 HTTP / 8791 MCP |
| 4 | senate | 8765 MCP / 8786 WebUI |
| 5 | financecoffer | 8100（WSL） |
| 6 | proxyfleet | 8788 控制台 / 8789 网关 |

启动按升序，停止按反向。**本模块不写任何模块的具体启动命令**——那是
482-B 的 `adapters.py`；引擎只依赖下面的 `ModuleAdapter` 协议。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from ladderstock.supervisor.modules import MODULE_PORTS

#: 启动顺序（QUOT-482 派单正文）；`stop_all` 严格取反向。
MODULE_ORDER: tuple[str, ...] = (
    "tickhub",
    "tickhub_us",
    "wire",
    "senate",
    "financecoffer",
    "proxyfleet",
)

#: 按需模块（QUOT-506）：在册、实现同一 `ModuleAdapter` 协议，但**不在** `MODULE_ORDER`。
#: `start_all` / `stop_all` 不碰它们——只能经单模块入口（`start_module`）显式拉起，
#: 这是「默认不启动」的结构保证，不是某个开关的默认值。
#: 按需模块的端口由配置决定（`MODULE_PORTS` 登记为空元组），registry 不做等值校验。
ON_DEMAND_MODULES: frozenset[str] = frozenset({"proxypool"})

# MODULE_PORTS（模块 → 端口白名单）唯一来源是 `supervisor.modules`
# （QUOT-491 / 482-C 落地）；这里 import 复用，避免两份清单漂移。


@dataclass(frozen=True)
class SpawnedProcess:
    """一次拉起的直接产物（引擎据此写账本；parent_pid 用于画进程树链）。"""

    pid: int
    #: 被拉起进程的父进程 PID（真实实现即 supervisor 自身进程）
    parent_pid: int | None
    #: 完整命令行（参数数组；引擎归一后算 cmdline_hash 入账本）
    cmdline: tuple[str, ...]


@runtime_checkable
class ModuleAdapter(Protocol):
    """单模块生命周期适配器（482-B 实现的接口契约）。

    约定：
    - `start()` 只负责拉起并**立即返回**，等端口监听由引擎轮询 `probe()`；
    - `stop()` 做该形态的优雅停（venv 发信号 / compose stop / wsl pkill），
      超时与强杀升级由引擎处理，适配器不做重试；
    - `kill()` 是强杀兜底（整树，不留子孙），只在优雅停超时后被调用；
    - `probe()` 回答「本模块端口是否在监听」，引擎用它确认真的起来了/停干净了。
    """

    name: str
    ports: tuple[int, ...]

    def start(self) -> SpawnedProcess: ...

    def stop(self) -> None: ...

    def kill(self) -> None: ...

    def probe(self) -> bool: ...


def module_registry(adapters: dict[str, ModuleAdapter]) -> dict[str, ModuleAdapter]:
    """校验一张适配器表符合注册表口径，不合口径直接拒（施工期契约错误）。

    - `MODULE_ORDER` 里的模块一个都不能少（少一个引擎就没法全停）；
    - 多出来的键只能是 `ON_DEMAND_MODULES` 里登记的按需模块（多一个没户口）；
    - 顺序模块的 `ports` 必须与 `MODULE_PORTS` 一致（账本落盘的 ports 以这里为准）；
      按需模块端口由配置决定，不做等值校验。
    """
    missing = set(MODULE_ORDER) - set(adapters)
    unknown = set(adapters) - set(MODULE_ORDER) - ON_DEMAND_MODULES
    if missing or unknown:
        raise ValueError(
            f"适配器集合与模块注册表不符：缺 {sorted(missing)}，多 {sorted(unknown)}"
        )
    for name, adapter in adapters.items():
        if name in ON_DEMAND_MODULES:
            continue
        if tuple(adapter.ports) != MODULE_PORTS[name]:
            raise ValueError(
                f"模块 {name} 的端口 {tuple(adapter.ports)} 与注册表 {MODULE_PORTS[name]} 不符"
            )
    return adapters


Origin = Literal["spawned", "adopted"]
