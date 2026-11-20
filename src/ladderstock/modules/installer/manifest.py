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

    extra_requirements: tuple[str, ...] = ()
    """主包 extras 之外的补充依赖（pip 会再装一遍，已装则跳过）。

    只在实测发现缺口时才加：extras 覆盖不到的运行期缺包，会让某个工具静默不可用。
    """


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
        # `[sources]` 的 extras 实测漏 lxml，部分公告/研报源解析会静默失败
        extra_requirements=("lxml>=4.9",),
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
        package="ldst-proxyfleet",
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
        note=(
            "PostgreSQL + Redis + MinIO + API 四件套，约 1GB，安装较慢。"
            "不装则公告语义检索与结构化落库不可用。"
        ),
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


def install_spec(key: str) -> list[str]:
    """一个 peer 完整的 pip 参数串（主包 + 补充依赖）。`bundle` 类返回空列表。"""
    item = BY_KEY[key]
    if not item.package:
        return []
    return [f"{item.package}{item.version}", *item.extra_requirements]


def requirements_text(key: str) -> str:
    """导出给人看的依赖清单（便携包打包、排查缺包时用）。

    头几行是 `#` 注释，其余每行一个 requirement，可直接 `pip install -r`。
    """
    item = BY_KEY[key]
    lines = [
        f"# {item.label}（{item.target}）",
        f"# 落盘位置：<安装根>/file/modules/{item.target}",
        f"# 验活入口：{item.entry or '（无，增强包由服务健康检查判定）'}",
        f"# 磁盘占用：约 {item.size_mb} MB",
    ]
    if not item.package:
        lines.append(
            "# 增强包：PG/Redis/MinIO/API 由 financecoffer installer 装二进制，无 pip 清单"
        )
        return "\n".join(lines) + "\n"
    lines.append(f"# 主包：{item.package}{item.version}")
    lines.append("")
    lines.append(f"{item.package}{item.version}")
    if item.extra_requirements:
        lines.append("")
        lines.append("# extras 未覆盖、实测缺了会让功能静默失效的补充项：")
        lines.extend(item.extra_requirements)
    return "\n".join(lines) + "\n"


__all__ = [
    "BY_KEY",
    "PEERS",
    "PYPI_MIRRORS",
    "PeerManifest",
    "install_spec",
    "requirements_text",
    "total_mb",
    "unchecked_notes",
]
