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
| 7 | watch | 5952 看门狗（QUOT-523 收编） |

启动按升序，停止按反向。`watch` 排最后：它探活的是看板 5950 本身，必须等
被守护的模块都就位后再起；停止时它第一个停，避免全停过程中触发「看板挂了」的重启。
**本模块不写任何模块的具体启动命令**——那是 482-B 的 `module_adapters.py`；引擎只
依赖下面的 `ModuleAdapter` 协议。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from ladderstock.supervisor.modules import module_ports

# 端口白名单唯一来源是 `supervisor.modules`（QUOT-491 / 482-C 落地）；QUOT-593
# 起按环境分表，`MODULE_PORTS` 不再是静态常量：模块级 `__getattr__` 按当前
# 环境解析成端口表快照（`from adapters import MODULE_PORTS` 兼容旧引用），
# registry 校验一律走 `module_ports()` 的实时值。


def __getattr__(name: str):
    if name == "MODULE_PORTS":
        return module_ports()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: 启动顺序（QUOT-482 派单正文 + QUOT-523 追加 watch）；`stop_all` 严格取反向。
MODULE_ORDER: tuple[str, ...] = (
    "tickhub",
    "tickhub_us",
    "wire",
    "senate",
    "financecoffer",
    "proxyfleet",
    "watch",
)

#: 按需模块（QUOT-506）：在册、实现同一 `ModuleAdapter` 协议，但**不在** `MODULE_ORDER`。
#: `start_all` / `stop_all` 不碰它们——只能经单模块入口（`start_module`）显式拉起，
#: 这是「默认不启动」的结构保证，不是某个开关的默认值。
#: 按需模块的端口由配置决定（`MODULE_PORTS` 登记为空元组），registry 不做等值校验。
ON_DEMAND_MODULES: frozenset[str] = frozenset({"proxypool"})


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
    - 顺序模块的 `ports` 必须与当前环境的端口表（`module_ports()`，QUOT-593）
      一致（账本落盘的 ports 以这里为准）；按需模块端口由配置决定，不做等值校验。
      若调用方显式覆写了本模块的 `MODULE_PORTS` 属性（monkeypatch 注入口径），
      以覆写值为准——与既有测试注入契约保持兼容。
    """
    explicit_ports = globals().get("MODULE_PORTS")
    env_ports = explicit_ports if explicit_ports is not None else module_ports()
    missing = set(MODULE_ORDER) - set(adapters)
    unknown = set(adapters) - set(MODULE_ORDER) - ON_DEMAND_MODULES
    if missing or unknown:
        raise ValueError(
            f"适配器集合与模块注册表不符：缺 {sorted(missing)}，多 {sorted(unknown)}"
        )
    for name, adapter in adapters.items():
        if name in ON_DEMAND_MODULES:
            continue
        if tuple(adapter.ports) != env_ports[name]:
            raise ValueError(
                f"模块 {name} 的端口 {tuple(adapter.ports)} 与注册表 {env_ports[name]} 不符"
            )
    return adapters


Origin = Literal["spawned", "adopted"]
