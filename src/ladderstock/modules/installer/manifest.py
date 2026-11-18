"""子模块清单：便携包要装哪些 peer、从哪装、装多大、装完怎么验。

与 `financecoffer/manifest.py` 的分工：
- 那边是**增强包内部组件**（PG / Redis / MinIO / API，GB 级二进制）；
- 这边是**同行子模块**（TickHub-CN / TickHub-US / Wire / Senate / ProxyFleet），
  以 Python 包为主，装进 `<安装根>/file/modules/<target>`。

取件主路径是 PyPI 国内镜像。GitHub 只作为开发态兜底——普通用户的机器
不保证能连 GitHub，第三方加速站也不保证长期可用，所以默认路径里不出现它。
"""

from __future__ import annotations

from dataclasses import dataclass

#: PyPI 镜像，按顺序尝试。清华 / 阿里 / 腾讯都在国内，最后一个兜底官方源。
PYPI_MIRRORS: tuple[str, ...] = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple",
    "https://pypi.org/simple",
)


@dataclass(frozen=True)
class PeerManifest:
    """一个子模块的安装声明。"""

    key: str
    """内部键，与 `boundary.PEER_CONTAINER_DIRS` 对应。"""

    label: str
    """向导上显示的名字。"""

    target: str
    """容器内目录名，落在 `<安装根>/file/modules/<target>`。"""

    kind: str
    """取件方式：`pypi`（Python 包） / `bundle`（增强包，交给 financecoffer installer）。"""

    package: str
    """pip 安装串，例如 `tickhub-cn[sources]`。`kind=bundle` 时为空。"""

    version: str
    """版本约束，例如 `>=1.1,<2`。"""

    size_mb: int
    """解压后磁盘占用（MB）。向导按磁盘实际占用报，不按下载包体积报。"""

    required: bool
    """是否默认勾选。当前策略：全部勾选——普通用户不知道该不该装。"""

    note: str
    """向导上的一句话说明，要说清「不装会怎样」。"""

    entry: str
    """装完的验证命令（相对 venv 的 console script），例如 `th-cn version`。"""

    requires_python: str = ">=3.11"


#: 六个子模块。体积按本机容器实测（tickhub-cn 155MB / tickhub-us 60MB / wire 71MB /
#: stock-senate 137MB / proxyfleet 0.5MB），再加 venv 与依赖，向上取整。
PEERS: tuple[PeerManifest, ...] = (
    PeerManifest(
        key="tickhub",
        label="TickHub-CN（A股数据）",
        target="tickhub-cn",
        kind="pypi",
        package="tickhub-cn[sources]",
        version=">=1.1,<2",
        size_mb=190,
        required=True,
        note="行情、K线、财务、资金流、公告、研报，151 个 MCP 工具。不装则 A 股取数不可用。",
        entry="th-cn version",
    ),
    PeerManifest(
        key="tickhub_us",
        label="TickHub-US（全球行情）",
        target="tickhub-us",
        kind="pypi",
        package="tickhub-us",
        version=">=1.0,<2",
        size_mb=90,
        required=True,
        note="美股、港股、全球指数。不装则海外标的取数不可用，A 股不受影响。",
        entry="th-us version",
    ),
    PeerManifest(
        key="wire",
        label="Wire（推理线缆）",
        target="wire",
        kind="pypi",
        package="quota-fuse-wire",
        version=">=0.1",
        size_mb=110,
        required=True,
        note="研究流程的编排与推理通道。不装则深度研究链路缺失。",
        entry="qfw version",
        requires_python=">=3.10",
    ),
    PeerManifest(
        key="senate",
        label="StockSenate（多空辩论）",
        target="stock-senate",
        kind="pypi",
        package="stocksenate",
        version=">=1.0,<2",
        size_mb=180,
        required=True,
        note="多空双方独立论证与裁决。不装则没有辩论式结论。",
        entry="senate version",
    ),
    PeerManifest(
        key="proxyfleet",
        label="ProxyFleet（代理池）",
        target="proxyfleet",
        kind="pypi",
        package="proxyfleet",
        version=">=0.2",
        size_mb=5,
        required=True,
        note="数据源被限流时轮换出口。不装则高频抓取容易被封。",
        entry="",
        requires_python=">=3.10",
    ),
    PeerManifest(
        key="financecoffer",
        label="FinanceCoffer 增强包",
        target="financecoffer",
        kind="bundle",
        package="",
        version="",
        size_mb=1034,
        required=True,
        note="PostgreSQL + Redis + MinIO + API 四件套，约 1GB，安装较慢。不装则公告语义检索与结构化落库不可用。",
        entry="",
    ),
)

#: 按 key 索引
BY_KEY: dict[str, PeerManifest] = {item.key: item for item in PEERS}


def total_mb(keys: tuple[str, ...] | list[str] | None = None) -> int:
    """勾选项的磁盘总占用（MB）。不传 keys 表示全选。"""
    selected = PEERS if keys is None else [BY_KEY[k] for k in keys]
    return sum(item.size_mb for item in selected)


def unchecked_notes(keys: tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
    """没勾的那些「不装会怎样」。"""
    selected = {item.key for item in PEERS} if keys is None else set(keys)
    return tuple(f"未勾「{item.label}」：{item.note}" for item in PEERS if item.key not in selected)


__all__ = [
    "BY_KEY",
    "PEERS",
    "PYPI_MIRRORS",
    "PeerManifest",
    "total_mb",
    "unchecked_notes",
]
