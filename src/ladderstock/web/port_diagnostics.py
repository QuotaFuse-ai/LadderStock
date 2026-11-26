"""端口占用四分类 + MCP 存活判定（QUOT-547）。

控制面 `_port_conflicts` / 诊断 API 共用的**纯判定**：只吃「占用者 PID +
本控制面记录的 PID 集合 + supervisor 账本记录 + 实时命令行」，不碰宿主、
不起子进程。归属口径与 supervisor / page_boot 统一（DEC-078）：

- `own_child` —— 本控制面 / 本 supervisor 亲手拉起并记账的残留子进程，可停可重拉；
- `adopted_peer` —— 外部启动、已被 supervisor 认领的 peer（`adopted_peer=True`），
  只观测、只认领、**绝不终止 / 重启**（QUOT-545 红线）；
- `foreign` —— 与本方无关的进程（含拿不到 PID、PID 复用对不上指纹），只报告不杀；
- `free` —— 端口无监听（在端口层面判定，不进入单占用者分类）。

MCP streamable-HTTP 端口（如 TickHub-CN 的 8000）只暴露 `/mcp`、没有 `/healthz`：
GET 无会话时返回 400 + JSON-RPC「Missing session ID」+ `mcp-session-id` 头，这是
「服务活着」的证据，**不是健康检查失败**。`probe_mcp_alive` 据此判活，404 / 连不上
才算不存活。探测函数注入给控制面，测试给假实现。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from ladderstock.supervisor.ledger import ModuleRecord
from ladderstock.supervisor.proctree import cmdline_hash

#: 占用四分类（QUOT-547 派单正文）
OWN_CHILD = "own_child"
ADOPTED_PEER = "adopted_peer"
FOREIGN = "foreign"
FREE = "free"

#: MCP 存活探测的响应体上限（与 control._HEALTH_BODY_LIMIT 同档）
_MCP_BODY_LIMIT = 65536

#: 账本里「正在服务」的状态：只有这些状态的记录才拿来认领端口持有者
_LIVE_STATES = frozenset({"starting", "running"})


def classify_port_owner(
    *,
    pid: int | None,
    mine_pids: Collection[int],
    record: ModuleRecord | None,
    live_cmdline: Sequence[str] | None = None,
    own_tree_pids: Collection[int] | None = None,
) -> tuple[str, str]:
    """单个端口占用者的归属分类 + 人类可读理由（纯函数，不碰宿主）。

    判定顺序（失败关闭：证明不了是本方亲手拉的一律不自动处置）：
    1. PID 在本控制面状态账里 → own_child（本控制面亲手启动的最强证据）；
    2. supervisor 账本认得它（pid 相符 + 正在服务）：实时命令行可得且与账本
       指纹不符 → foreign（PID 复用）；否则 `adopted_peer=True` → adopted_peer，
       其余（含 `origin="adopted"`）→ own_child（可停可重拉）；
    3. 其余（含拿不到 PID）→ foreign。

    为什么 `origin="adopted"` 也归 own_child：`adopt_module` / `page_boot._record_peer`
    只在命令行命中本模块识别规则时才写这条记录，即「端口上跑的是本平台的程序」。
    把它永久归为 adopted_peer 会让上一代没退干净的残留永远关不掉，形成
    「停不住 → 端口冲突 → 起不来」的死循环。命令行不匹配的外部程序仍走 foreign，
    绝不终止（DEC-014）。

    `live_cmdline=None` 表示调用方没查实时命令行（快照路径求快）：此时不做
    复用判定，只按账本来源分类；诊断 API 会带上实时命令行做精确判定。
    """
    if pid is not None and pid in mine_pids:
        return OWN_CHILD, "本控制面启动并记录的进程（own_child），可停止 / 重拉"
    # 单进程多代形态（ProxyFleet 的 `proxyfleet.exe serve` → 真正的 serve 子进程、
    # venv 启动器 → 解释器）：账本只记根 PID，监听端口的是它的子孙。监听者出现在
    # 账本进程的进程树里就是**同一份我方服务**，不能按「PID 不等」判成外部进程。
    if (
        pid is not None
        and record is not None
        and own_tree_pids
        and pid in own_tree_pids
        and record.state in _LIVE_STATES
    ):
        if record.adopted_peer:
            return ADOPTED_PEER, (
                "监听者是 supervisor 认领态记录的子孙进程；只观测，绝不终止 / 重启"
            )
        return OWN_CHILD, (
            f"监听者是 supervisor 记录的进程 {record.pid} 的子孙进程（同一份服务），可停止 / 重拉"
        )
    if (
        record is not None
        and pid is not None
        and record.pid == pid
        and record.state in _LIVE_STATES
    ):
        if (
            live_cmdline is not None
            and record.cmdline_hash
            and cmdline_hash(live_cmdline) != record.cmdline_hash
        ):
            return FOREIGN, "PID 复用：实时命令行与账本指纹不符，按外部进程处理（不终止）"
        if record.adopted_peer:
            return ADOPTED_PEER, "外部启动、已被 supervisor 认领的 peer；只观测，绝不终止 / 重启"
        # origin 不再区分 spawned / adopted：adopt 只在命令行命中本模块时发生，
        # 认领态同样是本平台的程序。把它当「永不处置」会让上一代残留永远关不掉，
        # 形成「停不住 → 端口冲突 → 起不来」死循环。
        return OWN_CHILD, (
            f"supervisor 账本认得它（origin={record.origin}）且命令行指纹相符，"
            "按自有处置，可停止 / 重拉"
        )
    if pid is None:
        return FOREIGN, "端口有监听但拿不到占用者 PID，无法证明归属（不终止）"
    return FOREIGN, "端口由非本方进程占用（不自动换端口 DEC-004，不终止 DEC-014）"


def mcp_path_for(item: Mapping[str, Any]) -> str:
    """从注册表 `default_url` 推 MCP 路径（…/mcp）；缺省回 `/mcp`。"""
    url = str(item.get("default_url") or "").strip()
    if url:
        path = urllib.parse.urlsplit(url).path
        if path:
            return path
    return "/mcp"


def _mcp_verdict(code: int, headers: Any, body: str) -> tuple[bool, str]:
    """HTTP 状态 + 头 + 体 → MCP 是否存活（2xx/3xx、会话头、JSON-RPC 体皆算活）。"""
    session = None
    if headers is not None:
        try:
            session = headers.get("mcp-session-id")
        except AttributeError:  # pragma: no cover - 假件可能给 dict
            session = headers.get("mcp-session-id") if isinstance(headers, Mapping) else None
    if 200 <= code < 400:
        return True, f"HTTP {code}"
    if session:
        return True, f"HTTP {code} · 带 mcp-session-id（MCP 服务存活）"
    if code in (400, 405, 406):
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and "jsonrpc" in payload:
            return True, f"HTTP {code} · JSON-RPC 响应（MCP 服务存活）"
        if code in (405, 406):
            return True, f"HTTP {code}（MCP 端点存在，方法不允许）"
    return False, f"HTTP {code}"


def probe_mcp_alive(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """MCP streamable-HTTP 存活探测（只探回环，绕开系统代理）。

    GET `/mcp` 无会话：服务活着会回 400 + JSON-RPC 错误 + `mcp-session-id` 头，
    或直接 2xx/3xx；404 / 连不上才算不存活。绝不把「无会话的 400」当成健康失败。
    """
    request = urllib.request.Request(
        url, method="GET", headers={"Accept": "application/json, text/event-stream"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 - 只探回环
            code = int(getattr(response, "status", 200))
            body = response.read(_MCP_BODY_LIMIT).decode("utf-8", "replace")
            return _mcp_verdict(code, response.headers, body)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(_MCP_BODY_LIMIT).decode("utf-8", "replace")
        except (OSError, ValueError):  # pragma: no cover - 读体失败按空体判
            body = ""
        return _mcp_verdict(exc.code, exc.headers, body)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"无法连接：{exc}"


__all__ = [
    "ADOPTED_PEER",
    "FOREIGN",
    "FREE",
    "OWN_CHILD",
    "classify_port_owner",
    "mcp_path_for",
    "probe_mcp_alive",
]
