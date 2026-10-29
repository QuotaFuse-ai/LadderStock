"""控制面 `/api/control/*`（DEC-014）：看板从「只读展示」升级为「能装、能起、能配」。

与数据面（`/api/review` 等 7 个只读 GET）**完全分层**：控制面单独成前缀，
默认开启、可用 `LDST_CONTROL_ENABLED=0` 整体关掉（关掉后一律 404，数据面不受影响）。

DEC-014 的五条硬约束在这里逐条落地：

1. **只监听回环**：`_guard()` 对每个请求比对 `scope["client"]`，非回环来源 403；
   `Host` 头按白名单校验（挡住 DNS rebinding），写方法再校验 `Origin` /
   `Sec-Fetch-Site`（挡住跨站「简单请求」CSRF，QUOT-123 B5/B6）。
   绑定地址仍由 `core/listener.require_binding_policy()` 把关，不可配 `0.0.0.0`。
2. **不拼 shell**：所有子进程都是参数数组 + `shell=False` + 显式 `timeout`；
   命令一律来自 `core/boundary.PEER_CONTROL` 白名单，peer 只能取枚举 id。
3. **不动系统环境**：每个 peer 用自己的 `.venv`；不写 PATH、不装全局包、不改代理；
   **从不终止非本控制面启动的进程**（端口占用只报 PID，不杀）。
4. **凭据只进 env**：密钥只写 peer 真正加载的那个 dotenv（`PeerControl.env_filename`，
   DACL 收口为「仅当前用户 + SYSTEM + Administrators」，QUOT-194 P1-3）；
   GET 只回 `{"set": true, "preview": "sk-****abcd"}`，日志与错误信息统一过 `mask_secrets()`。
5. **失败即停**：每步最多重试 1 次，仍失败即 `failed` 并把最后 20 行输出写进任务日志；
   端口被占直接失败，**不自动换端口**（DEC-004）。

任务模型：`{id, peer, action, status, step, progress, started_at, ended_at, log_tail, error}`，
由模块级 `ThreadPoolExecutor` 持有，页面关掉任务继续跑，重进页面按 `GET /jobs/{id}` 查。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ladderstock.cli.paths import config_dir
from ladderstock.core.boundary import PEER_CONTROL, PeerControl
from ladderstock.core.fsguard import (
    AclError,
    harden_file_acl,
    require_local_peer_directories,
)
from ladderstock.core.validation import load_validation, validation_path, write_document

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

CONTROL_PREFIX = "/api/control"

#: 整体开关（DEC-014 §1：默认开启，可一键禁用；禁用后全部 404）
CONTROL_ENABLED_ENV = "LDST_CONTROL_ENABLED"

#: 需要做 CSRF 判据的写方法（GET/HEAD/OPTIONS 是安全方法，不做来源校验）
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: `Sec-Fetch-Site` 里算「本机页面 / 直接访问」的两种取值（其余一律拒绝）
SAME_ORIGIN_SITES = frozenset({"same-origin", "none"})

#: 已存密钥可回落的主机白名单（逗号分隔；默认只有自有 AI 中继，QUOT-123 C10）
AI_ALLOWED_HOSTS_ENV = "LDST_AI_ALLOWED_HOSTS"
DEFAULT_AI_ALLOWED_HOST = "ai.1k0.com"

#: 终止前查可执行文件路径（CIM/wmic）的兜底超时
PROCESS_QUERY_TIMEOUT_SEC = 10.0

#: 进程创建时间与启动记录允许的偏差（秒）。`_start` 先 spawn 再落盘 `started_at`，
#: 两者天然有子秒级差；5s 足够覆盖落盘/取时开销，又小于常见的 PID 复用窗口（QUOT-177 P1-2）
PROCESS_START_TOLERANCE_SEC = 5.0

#: 任何情况下都不终止的进程（PID 复用时的最后一道闸，QUOT-123 P0-2）
DENY_PROCESS_NAMES = frozenset(
    {
        "multica",
        "multica.exe",
        "system",
        "system idle process",
        "explorer.exe",
        "lsass.exe",
        "services.exe",
        "svchost.exe",
        "winlogon.exe",
        "csrss.exe",
        "wininit.exe",
        "smss.exe",
    }
)

#: 状态码 → 中文标签（唯一真源；前端只做展示，不自己猜）
STATUS_UNCONFIGURED = "unconfigured"
STATUS_NOT_INSTALLED = "not_installed"
STATUS_INSTALLED = "installed"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_UNCONFIGURED: "未配置",
    STATUS_NOT_INSTALLED: "未安装",
    STATUS_INSTALLED: "已安装",
    STATUS_STARTING: "启动中",
    STATUS_RUNNING: "运行中",
    STATUS_STOPPED: "已停止",
    STATUS_FAILED: "异常",
}

#: 任务日志里向前端暴露的尾部行数（PRD §4）
JOB_TAIL_LINES = 20

#: `import <pkg>` 探测超时（PRD §3.1）
IMPORT_PROBE_TIMEOUT_SEC = 15.0
#: 健康检查超时（PRD §3.1：2s 内成功才算运行中）
HEALTH_TIMEOUT_SEC = 2.0
#: 启动后等待端口监听的窗口
START_GRACE_SEC = 30.0
#: 安装各步骤的超时（pip 首次解析依赖可能较慢，但必须有上限）
INSTALL_STEP_TIMEOUT_SEC = 900.0

#: 端口占用探测的超时
PORT_PROBE_TIMEOUT_SEC = 10.0

#: 控制面日志目录（相对 data_root）
CONTROL_LOG_SUBDIR = Path("logs") / "control"
#: 控制面进程状态（记录「我们启动过谁」——只对这些 PID 负责）
CONTROL_STATE_SUBDIR = Path("control")
CONTROL_STATE_FILE = "peer_state.json"

#: 历史默认端口里**已经确认踩过坑**的那些：只报告占用者，不改端口（DEC-013 / DEC-014 §3）
KNOWN_AVOIDED_PORTS: dict[str, tuple[tuple[int, str], ...]] = {
    "tickhub_us": (
        (8001, "TickHub-US 默认 MCP 端口：被 WSL 转发服务占用，改用 8002"),
        (8787, "TickHub-US 默认管理端端口：与 TickHub-CN 撞车，改用 8789"),
    ),
}


# --------------------------------------------------------------------------- #
# 开关与来源判定
# --------------------------------------------------------------------------- #

def control_enabled() -> bool:
    """`LDST_CONTROL_ENABLED` 默认开启；显式写 `0/false/no/off` 才关闭。"""
    raw = os.environ.get(CONTROL_ENABLED_ENV, "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_loopback_host(host: str) -> bool:
    """主机名是否回环：`127.0.0.0/8` / `::1` / IPv4-mapped / `localhost`。"""
    text = (host or "").strip().strip("[]").split("%", 1)[0].lower()
    if not text:
        return False
    if text in {"localhost", "localhost.localdomain"}:
        return True
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped.is_loopback
    return addr.is_loopback


#: 除 stdlib 自带判定外，额外拒绝的网段（QUOT-123 C10）
_EXTRA_BLOCKED_NETWORKS: tuple[
    ipaddress.IPv4Network | ipaddress.IPv6Network, ...
] = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / 运营商级 NAT
    ipaddress.ip_network("fc00::/7"),  # IPv6 唯一本地地址
    ipaddress.ip_network("fe80::/10"),  # IPv6 链路本地（stdlib 也判，双保险）
)


def parse_ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """把文本解析成 IP 地址；IPv4-mapped IPv6 归一成 IPv4；非法输入返回 None。"""
    try:
        addr = ipaddress.ip_address(str(text or "").strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_forbidden_target_ip(text: str) -> bool:
    """目标地址是否属于内网 / 回环 / 链路本地 / 保留段（QUOT-123 C10 的 SSRF 判据）。

    标准库 `ipaddress` 负责区间判断，这里只补三张网段（CGNAT、ULA、v6 链路本地）。
    """
    addr = parse_ip(text)
    if addr is None:
        return True  # 解析不了的字符串一律不当公网地址用
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_reserved or addr.is_unspecified or addr.is_multicast:
        return True
    return any(addr in net for net in _EXTRA_BLOCKED_NETWORKS if net.version == addr.version)


def resolve_host_ips(host: str) -> list[str]:
    """把主机名解析成全部 IP（去重）；解析失败抛 `socket.gaierror`（调用方转 400）。"""
    text = (host or "").strip().strip("[]")
    if not text:
        return []
    infos = socket.getaddrinfo(text, None, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        if addr and addr not in seen:
            seen.append(addr)
    return seen


def ai_allowed_hosts() -> set[str]:
    """已存密钥可以回落的主机集合（`LDST_AI_ALLOWED_HOSTS`，逗号分隔）。"""
    raw = os.environ.get(AI_ALLOWED_HOSTS_ENV, "")
    items = {part.strip().lower().rstrip(".") for part in raw.split(",") if part.strip()}
    return items or {DEFAULT_AI_ALLOWED_HOST}


def host_in_allowlist(host: str, allowed: Iterable[str]) -> bool:
    """主机名命中白名单（含子域）；大小写、结尾点不敏感。"""
    text = (host or "").strip().lower().rstrip(".")
    return any(text == item or text.endswith("." + item) for item in allowed)


def is_control_path(path: str) -> bool:
    """路径是否落在 `/api/control` 前缀下（用于开关关闭时的中间件短路）。"""
    text = (path or "").rstrip("/")
    return text == CONTROL_PREFIX or text.startswith(CONTROL_PREFIX + "/")


def client_host(client: Any) -> str:
    """从 ASGI/Starlette 的 `client`（`(host, port)` 或 `Address`）取来源主机。"""
    if client is None:
        return ""
    if isinstance(client, str):
        return client
    host = getattr(client, "host", None)
    if isinstance(host, str):
        return host
    try:
        return str(client[0])
    except (IndexError, KeyError, TypeError):  # pragma: no cover - 形状异常
        return ""


def _server_identity(request: Request) -> tuple[str, int | None]:
    """服务端实际监听的 `(host, port)`（ASGI `scope["server"]`）。"""
    server = request.scope.get("server") or ()
    host = str(server[0]).strip().lower() if len(server) >= 1 and server[0] else ""
    try:
        port = int(server[1]) if len(server) >= 2 and server[1] else None
    except (TypeError, ValueError):  # pragma: no cover - 形状异常
        port = None
    return host, port


def _split_host_header(raw: str) -> tuple[str, int | None] | None:
    """解析 `Host` 头 → `(host, port)`；非法端口返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = urllib.parse.urlsplit(f"//{text}")
        return (parsed.hostname or "").lower(), parsed.port
    except ValueError:
        return None


def host_header_allowed(request: Request) -> bool:
    """`Host` 白名单（DNS rebinding 防线，QUOT-123 B5）。

    只接受：回环主机名/IP（端口须与本机监听端口一致），或与服务端监听地址完全
    一致的 Host（`TestClient` 的 `testserver` 落在这一支）。`Host` 缺席时放行——
    非浏览器客户端不会带它，浏览器一定带（HTTP/1.1）。
    """
    raw = request.headers.get("host")
    if raw is None:
        return True
    split = _split_host_header(raw)
    if split is None:
        return False
    host, port = split
    if not host:
        return False
    server_host, server_port = _server_identity(request)
    if is_loopback_host(host):
        if port is not None and server_port and is_loopback_host(server_host):
            return port == server_port
        return True
    return bool(server_host) and host == server_host


def origin_allowed(request: Request) -> bool:
    """写方法来源校验：`Origin` 必须是本机页面（`http(s)` + 回环主机 + 监听端口）。

    没有 `Origin` 头时返回 True（非浏览器客户端）；浏览器跨站请求必带该头。
    """
    raw = request.headers.get("origin")
    if raw is None:
        return True
    text = raw.strip()
    if not text:
        return False
    if text.lower() == "null":
        return False  # 沙箱 iframe / data: 页面
    try:
        parsed = urllib.parse.urlsplit(text)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not is_loopback_host(host):
        return False
    server_host, server_port = _server_identity(request)
    if port is not None and server_port and is_loopback_host(server_host):
        return port == server_port
    return True


async def _guard(request: Request) -> None:
    """控制面统一闸门：总开关 → 来源回环 → Host 白名单 → 写方法来源（QUOT-123）。

    - **开关**：`LDST_CONTROL_ENABLED=0` 时 404（与 SPA 兜底同形，不暴露控制面存在）；
      路由层之外还有一层中间件短路（见 `server.create_app`），保证 HEAD/OPTIONS
      不会先被路由层回 405 + `Allow`。
    - **Host**：只认回环主机名/IP（或与服务端监听地址完全一致，`TestClient` 的
      `testserver` 即属此类）；其余 403——挡住 DNS rebinding 把攻击者域名解析到
      127.0.0.1 的玩法。
    - **写方法来源**：`POST/PUT/PATCH/DELETE` 若带 `Origin`，必须是本机页面；
      若带 `Sec-Fetch-Site`，只接受 `same-origin` / `none`。两个头都缺席时放行
      （curl / TestClient 这类非浏览器客户端本来就不会被跨站利用）。
    """
    if not control_enabled():
        # 与 SPA 兜底的 404 保持同一形状，不暴露「这里本来有个控制面」
        raise HTTPException(status_code=404, detail="Not Found")
    if not is_loopback_host(client_host(request.client)):
        raise HTTPException(status_code=403, detail="控制面只接受回环来源")
    if not host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )
    if request.method.upper() in WRITE_METHODS:
        if not origin_allowed(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"跨站来源被拒绝：Origin={request.headers.get('origin')}"
                    "（控制面只接受本机页面发起的写请求）"
                ),
            )
        site = request.headers.get("sec-fetch-site")
        if site is not None and site.strip().lower() not in SAME_ORIGIN_SITES:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"跨站来源被拒绝：Sec-Fetch-Site={site}"
                    "（只接受 same-origin / none）"
                ),
            )


# --------------------------------------------------------------------------- #
# 验证条件写路径（QUOT-197 / DEC-016）
# --------------------------------------------------------------------------- #
#
# `/validate` 屏上「明日条件」有两种存法：本地（IndexedDB）与后端（本接口）。
# 本接口只碰 `data/state/validation/<trade_date>.json` 的 `conditions` 与
# `updated_at`——同一文件里的 `reconcile` 等对账结果原样保留（硬约束）。
#
# 请求体与盘上 schema 不是一套（见 `core/validation.py:build_conditions()` 与
# `web/server.py:_validation_conditions()`），落盘必须做三件映射：
#   1. `trigger` → `text`（读投影把 `text` 反向映射回 `trigger`）；
#   2. 由 `sha1(code|trigger)[:12]` 生成稳定 `id`（`reconcile()` 靠 `id` 对账）；
#   3. `note` 是读投影由 `reconcile` 推出来的，这里的 `note` 只作盘上自由备注。

#: 请求体顶层白名单：多一个键就 400，**不静默丢弃**
VALIDATION_REQUEST_FIELDS = frozenset({"trade_date", "conditions", "mode"})
#: 单条条件白名单
VALIDATION_CONDITION_FIELDS = frozenset({"code", "trigger", "note"})
#: 合并方式：`merge` 按 `code+trigger` 去重后追加；`replace` 整段覆盖
VALIDATION_MODES = frozenset({"merge", "replace"})
#: 单次请求最多条数 / 单条文案最大长度
MAX_CONDITIONS = 50
MAX_TRIGGER_CHARS = 200
#: `trade_date` 必须严格 `YYYY-MM-DD`（`date.fromisoformat` 会接受 `20261029`）
TRADE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _invalid(detail: str) -> HTTPException:
    """请求体校验失败的统一出口：400 + 可读中文原因。"""
    return HTTPException(status_code=400, detail=detail)


def condition_id(code: str | None, trigger: str) -> str:
    """盘上条件的**稳定键**：`sha1(code|trigger)[:12]`。

    `reconcile()` 按 `conditions[].id` 与 `reconcile.results[].id` 对齐，`id` 缺失
    就永远对不上账；由内容确定性生成，`replace` 之后同一条件的 id 不漂移。
    """
    return hashlib.sha1(f"{code or ''}|{trigger}".encode()).hexdigest()[:12]


def condition_key(item: Mapping[str, Any]) -> tuple[str, str]:
    """去重键 `(code, 文案)`：请求体（`trigger`）与盘上（`text`）同一口径。"""
    code = item.get("code")
    text = item.get("text")
    if not isinstance(text, str):
        text = item.get("trigger")
    return (
        code.strip() if isinstance(code, str) else "",
        text.strip() if isinstance(text, str) else "",
    )


def _parse_trade_date(value: Any) -> date:
    text = value.strip() if isinstance(value, str) else ""
    if not text or not TRADE_DATE_PATTERN.match(text):
        raise _invalid(f"trade_date 必须是 YYYY-MM-DD：{value!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise _invalid(f"trade_date 不是合法日期：{value!r}") from None


def _parse_condition(raw: Any, index: int) -> dict[str, Any]:
    """单条条件 → 盘上形态 `{id, text, code?, note?}`；白名单外的键 400。"""
    if not isinstance(raw, Mapping):
        raise _invalid(f"conditions[{index}] 必须是对象")
    unknown = sorted({str(key) for key in raw} - VALIDATION_CONDITION_FIELDS)
    if unknown:
        raise _invalid(
            f"conditions[{index}] 含白名单外的字段：{', '.join(unknown)}"
            f"（只接受：{', '.join(sorted(VALIDATION_CONDITION_FIELDS))}）"
        )

    trigger = raw.get("trigger")
    if not isinstance(trigger, str) or not trigger.strip():
        raise _invalid(f"conditions[{index}].trigger 不能为空")
    trigger = trigger.strip()
    if len(trigger) > MAX_TRIGGER_CHARS:
        raise _invalid(
            f"conditions[{index}].trigger 超长（{len(trigger)} > {MAX_TRIGGER_CHARS} 字）"
        )

    code = raw.get("code")
    if code is not None and not isinstance(code, str):
        raise _invalid(f"conditions[{index}].code 必须是字符串或 null")
    code_text = code.strip() if isinstance(code, str) else ""

    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise _invalid(f"conditions[{index}].note 必须是字符串或 null")
    note_text = note.strip() if isinstance(note, str) else ""

    item: dict[str, Any] = {
        "id": condition_id(code_text or None, trigger),
        "text": trigger,  # 请求体 `trigger` → 盘上 `text`
    }
    if code_text:
        item["code"] = code_text
    if note_text:
        item["note"] = note_text
    return item


def parse_validation_request(
    payload: Any,
) -> tuple[date, list[dict[str, Any]], str]:
    """校验请求体 → `(trade_date, 盘上条件列表, mode)`；任何不合规一律 400。"""
    if not isinstance(payload, Mapping):
        raise _invalid("请求体必须是 JSON 对象")
    unknown = sorted({str(key) for key in payload} - VALIDATION_REQUEST_FIELDS)
    if unknown:
        raise _invalid(
            f"请求体含白名单外的字段：{', '.join(unknown)}"
            f"（只接受：{', '.join(sorted(VALIDATION_REQUEST_FIELDS))}）"
        )

    trade_date = _parse_trade_date(payload.get("trade_date"))

    raw_mode = payload.get("mode", "merge")
    mode = raw_mode.strip().lower() if isinstance(raw_mode, str) else ""
    if mode not in VALIDATION_MODES:
        raise _invalid(
            f"mode 只能是 merge / replace：{raw_mode!r}"
            f"（可用：{', '.join(sorted(VALIDATION_MODES))}）"
        )

    raw_conditions = payload.get("conditions")
    if not isinstance(raw_conditions, list):
        raise _invalid("conditions 必须是数组")
    if len(raw_conditions) > MAX_CONDITIONS:
        raise _invalid(
            f"conditions 超过 {MAX_CONDITIONS} 条（{len(raw_conditions)}）"
        )

    conditions = [
        _parse_condition(raw, index) for index, raw in enumerate(raw_conditions)
    ]
    return trade_date, conditions, mode


def merge_conditions(
    existing: Iterable[Any], incoming: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`mode=merge`：按 `code+trigger` 去重后追加；已有条目一字不动。"""
    merged = [item for item in existing if isinstance(item, dict)]
    seen = {condition_key(item) for item in merged}
    for item in incoming:
        key = condition_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #

_SK_TOKEN = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_KV_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|authorization)\b"
    r"(\s*[=:]\s*)((?:bearer|basic|token)\s+)?(\S+)"
)


def secret_preview(value: str) -> str:
    """密钥打码预览：保留可辨识前缀 + 末 4 位，中间一律 `****`（PRD §5）。"""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    dash = text.find("-", 0, 6)
    head = text[: dash + 1] if dash > 0 else text[:3]
    return f"{head}****{text[-4:]}"


def mask_secrets(text: str, extra: Iterable[str] = ()) -> str:
    """把文本里的密钥打码：已知明文先按值替换，再按 `sk-…` / `key=…` 兜底。"""
    masked = text or ""
    for value in extra:
        candidate = (value or "").strip()
        if len(candidate) >= 6:
            masked = masked.replace(candidate, secret_preview(candidate))
    masked = _SK_TOKEN.sub(lambda m: secret_preview(m.group(0)), masked)
    masked = _KV_SECRET.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}***", masked
    )
    return masked


# --------------------------------------------------------------------------- #
# 探测原语
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class StepResult:
    """一次子进程调用的结果（`output` 是 stdout+stderr 合并后的文本）。"""

    returncode: int
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def tail_lines(text: str, count: int = JOB_TAIL_LINES) -> list[str]:
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    return lines[-count:]


def _clean_env() -> dict[str, str]:
    """子进程环境：只从当前环境派生，**不写 PATH、不装全局包**（DEC-014 硬约束 3）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return env


def run_step(args: list[str], cwd: Path, timeout: float) -> StepResult:
    """参数数组 + `shell=False` + 显式超时（DEC-014 硬约束 2）。"""
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_clean_env(),
        )
    except subprocess.TimeoutExpired:
        return StepResult(-9, f"超时：超过 {timeout:.0f}s 未结束（{' '.join(args)}）")
    except OSError as exc:
        return StepResult(-1, f"无法执行 {args[0] if args else '(空命令)'}：{exc}")
    return StepResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


@dataclass(frozen=True)
class PortOwner:
    pid: int | None
    process: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "process": self.process}


def _parse_netstat_listeners(output: str, port: int) -> list[int]:
    """从 `netstat -ano -p tcp` 输出里挑出监听 `port` 的 PID（去重保序）。"""
    found: list[int] = []
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() not in {"TCP", "TCPV6"}:
            continue
        if parts[3].upper() != "LISTENING":
            continue
        local = parts[1]
        if ":" not in local:
            continue
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid not in found:
            found.append(pid)
    return found


def _process_name(pid: int) -> str | None:
    """`tasklist` 查进程名（查不到返回 None，不影响判定）。"""
    if os.name != "nt":  # pragma: no cover - 本项目以 Windows 为主
        return None
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            shell=False,
            timeout=5,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - 环境异常
        return None
    first = (proc.stdout or "").strip().splitlines()
    if not first:
        return None
    cells = [cell.strip().strip('"') for cell in first[0].split('","')]
    if not cells or not cells[0] or cells[0].upper().startswith("INFO"):
        return None
    return cells[0]


def probe_port(port: int | None) -> list[PortOwner]:
    """端口是否有监听；有则带 PID 与进程名（只报告，**不终止**）。"""
    if not port:
        return []
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                shell=False,
                timeout=PORT_PROBE_TIMEOUT_SEC,
                capture_output=True,
                text=True,
                errors="replace",
            )
            pids = _parse_netstat_listeners(proc.stdout or "", port)
            return [PortOwner(pid=pid, process=_process_name(pid)) for pid in pids]
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - 环境异常
            pass
    # 兜底：能连上就算有人监听（拿不到 PID 就如实给 None）
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return [PortOwner(pid=None, process=None)]
    except OSError:
        return []


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """绕开系统代理的 opener：控制面只探回环地址，走代理必然连不通。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_health(url: str, timeout: float = HEALTH_TIMEOUT_SEC) -> tuple[bool, str]:
    """HTTP 健康探测：2xx/3xx 视为健康；其余（含连不上）如实返回失败原因。"""
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with _no_proxy_opener().open(request, timeout=timeout) as response:  # noqa: S310 - 只探回环
            code = int(getattr(response, "status", 200))
            return 200 <= code < 400, f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"无法连接：{exc}"


# --------------------------------------------------------------------------- #
# `.env.local` 读写（凭据唯一落点，0600）
# --------------------------------------------------------------------------- #

_ENV_KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class EnvFileUnreadable(OSError):
    """dotenv 存在但读不出来（权限 / 被占用 / 编码）。

    这是 fail closed 的边界（QUOT-194 P1-3）：文件里有密钥时，「读不到」绝不能
    退化成「没有密钥」——那会让 `ai_test` 变成无鉴权请求、把未知键覆盖掉。
    """


def read_env_file(path: Path, *, strict: bool = False) -> dict[str, str]:
    """解析 KEY=VALUE（忽略注释/空行；去掉成对引号）。文件不存在返回空字典。

    `strict=True` 时，文件**存在但读不出来**抛 `EnvFileUnreadable`；默认宽松模式
    只用于「拿不到就算了」的场合（例如日志脱敏，调用方另有兜底）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:  # 编码错（如 GBK 手改）也算「读不出来」
        if strict:
            raise EnvFileUnreadable(
                f"无法读取 {path.name}（{exc.__class__.__name__}）：{exc}"
            ) from exc
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_KEY_LINE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def write_env_file(
    path: Path,
    updates: Mapping[str, str],
    removals: Iterable[str] = (),
    *,
    harden: Callable[[Path], None] | None = None,
) -> None:
    """原地更新 `.env.local`：保留无关键与注释，原子替换，并把 DACL 收到 0600 等价。

    顺序固定为「写临时文件 → 收紧临时文件 DACL → 原子替换」：成品路径上永远不会
    出现一个权限未收口的版本（QUOT-194 P1-3）。读不出原文时**不覆盖**（fail closed）。
    """
    harden_acl = harden or harden_file_acl
    drop = {key for key in removals}
    try:
        original = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        original = []
    except (OSError, UnicodeError) as exc:
        raise EnvFileUnreadable(
            f"无法读取现有 {path.name}（{exc.__class__.__name__}）：{exc}；"
            "为免覆盖其中的密钥/其它键，已中止写入"
        ) from exc

    pending = dict(updates)
    lines: list[str] = []
    for line in original:
        match = _ENV_KEY_LINE.match(line)
        key = match.group(1) if match else ""
        if key and key in drop:
            continue
        if key and key in pending:
            lines.append(f"{key}={pending.pop(key)}")
            continue
        lines.append(line)
    for key, value in pending.items():
        lines.append(f"{key}={value}")
    if lines and lines[-1].strip():
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text("\n".join(lines), encoding="utf-8")
        harden_acl(tmp)
        os.replace(tmp, path)
    except Exception:
        # 半成品不能留在磁盘上（含密钥）：收紧 DACL 失败、或替换失败（目标被占用）
        # 都要收拾干净再把失败抛给调用方
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不改变结论
            pass
        raise


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #

class JobFailure(RuntimeError):
    """任务在某个步骤上失败（带步骤名，直接进入 `error` 字段）。"""


class StatePersistError(JobFailure):
    """状态文件落盘或 DACL 收紧失败（QUOT-194 P1-3）。

    继承 `JobFailure`：启动/停止任务直接以干净文案失败，而不是「内部错误」。
    fail closed 的取舍：宁可这次操作不记录，也不留下一个别的账户能改写的
    `peer_state.json`——那等于把 P0-2（盲杀非本控制面进程）的门重新打开。
    """


@dataclass
class Job:
    """一次控制面动作（安装 / 启动 / 停止）。字段与 PRD §4 对齐。"""

    id: str
    peer: str
    action: str
    dev: bool = False
    status: str = "queued"
    step: str = ""
    progress: float = 0.0
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    _log: deque[str] = field(default_factory=lambda: deque(maxlen=200))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, line: str) -> None:
        with self._lock:
            for chunk in str(line).splitlines() or [""]:
                self._log.append(chunk.rstrip())

    def tail(self, count: int = JOB_TAIL_LINES) -> list[str]:
        with self._lock:
            return list(self._log)[-count:]

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "peer": self.peer,
            "action": self.action,
            "status": self.status,
            "step": self.step,
            "progress": round(self.progress, 3),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "log_tail": self.tail(),
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 进程归属（QUOT-123 P0-2：终止前必须证明「这个 PID 是本 peer 的」）
# --------------------------------------------------------------------------- #

def _abs_path(path: Path | str) -> str:
    """绝对路径（`resolve()` 失败时退回 `absolute()`）。"""
    candidate = Path(path)
    try:
        return str(candidate.resolve())
    except OSError:  # pragma: no cover - 盘符不存在等极端情况
        return str(candidate.absolute())


def _is_under(path: str | Path, root: str | Path) -> bool:
    """`path` 是否在 `root` 目录之下（Windows 大小写不敏感）。"""
    left = os.path.normcase(_abs_path(path))
    right = os.path.normcase(_abs_path(root))
    try:
        return os.path.commonpath([left, right]) == right
    except ValueError:  # 不同盘符
        return False


def _norm_exe(path: str | Path) -> str:
    """可执行文件路径归一（绝对 + `normpath` + `normcase`），用于「记录 vs 实时」等值比对。

    与 `_is_under` 同一口径（`_abs_path` 已经过了 `resolve()`），保证
    `D:\\peer\\.venv\\Scripts\\QF_W.exe` 与 `d:\\peer\\.venv\\Scripts\\qfw.exe` 判为同一个文件。
    """
    return os.path.normcase(os.path.normpath(_abs_path(path)))


def pid_records(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 state 里的 `pids` 统一成 `{pid, exe, started_at, legacy}` 列表。

    新格式（`_start` 落盘）带 `exe` / `started_at`；旧格式只有裸 PID，这类记录
    `legacy=True`，`_stop` 一律拒绝终止（无法证明归属，宁可不杀）。
    """
    raw = entry.get("pids") if isinstance(entry, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return []
    records: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            try:
                pid = int(item.get("pid"))
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "pid": pid,
                    "exe": str(item.get("exe") or ""),
                    "started_at": item.get("started_at"),
                    "legacy": False,
                }
            )
            continue
        if isinstance(item, bool):
            continue
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        records.append({"pid": pid, "exe": "", "started_at": None, "legacy": True})
    return records


def state_pids(entry: Mapping[str, Any]) -> list[int]:
    """state 里记录的 PID 列表（新旧格式通吃，只读判定用）。"""
    return [record["pid"] for record in pid_records(entry)]


def _parse_image_output(output: str) -> tuple[str | None, str | None]:
    """解析 CIM / wmic 的输出 → `(executable_path, process_name)`。"""
    exe: str | None = None
    name: str | None = None
    for line in (output or "").splitlines():
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith("executablepath="):
            exe = text.split("=", 1)[1].strip() or None
        elif lowered.startswith("name="):
            name = text.split("=", 1)[1].strip() or None
        elif exe is None and ("\\" in text or "/" in text):
            exe = text
        elif name is None and text.lower().endswith(".exe"):
            name = text
    if exe and not name:
        name = os.path.basename(exe)
    return exe, name


def process_image(pid: int, cwd: Path) -> tuple[str | None, str | None]:
    """查 PID 的可执行文件路径与进程名（CIM 优先、wmic 兜底；查不到 `(None, None)`）。

    只读查询，绝不终止任何东西。**查不到就返回 None**——调用方据此拒绝终止，
    不降级成盲杀（QUOT-123 P0-2）。
    """
    number = int(pid)
    if os.name != "nt":  # pragma: no cover - 本项目以 Windows 为主
        try:
            exe = os.readlink(f"/proc/{number}/exe")
        except OSError:
            return None, None
        return exe, os.path.basename(exe)

    queries = (
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={number}\").ExecutablePath",
        ],
        [
            "wmic",
            "process",
            "where",
            f"ProcessId={number}",
            "get",
            "ExecutablePath,Name",
            "/format:list",
        ],
    )
    for args in queries:
        result = run_step(args, Path(cwd), PROCESS_QUERY_TIMEOUT_SEC)
        if not result.ok:
            continue
        exe, name = _parse_image_output(result.output)
        if exe:
            return exe, name
    return None, None


def _as_utc(moment: datetime) -> datetime:
    """统一成带时区的 UTC；无时区的按本机时区解释（CIM_DATETIME 不带偏移时的既有口径）。"""
    if moment.tzinfo is None:
        return moment.astimezone(UTC)
    return moment.astimezone(UTC)


def _parse_state_time(value: Any) -> datetime | None:
    """解析状态文件里的 `started_at`（ISO 8601）→ UTC datetime；解析不了返回 None。

    `_now_iso()` 写的是 `datetime.now(UTC).isoformat(timespec="seconds")`，这里额外兼容
    带 `Z` 后缀、超过 6 位小数、以及不带偏移（按本机时区理解）的手改数据。**解析不了
    就返回 None**——调用方据此拒绝终止，不拿「解析失败」当「时间对得上」。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_utc(parsed)


#: wmic 原样输出的 `CIM_DATETIME`：`yyyyMMddHHmmss.ffffff±zzz`（偏移是**分钟**，`+480` = UTC+8）
_CIM_DATETIME_RE = re.compile(
    r"^(?P<date>\d{8})(?P<time>\d{6})\.(?P<frac>\d{1,6})(?P<sign>[+-])(?P<offset>\d{3,4})$"
)


def _parse_process_time(value: Any) -> datetime | None:
    """解析进程创建时间 → UTC datetime；解析不了返回 None。

    支持两种形态：ISO 8601（PowerShell `ToString("o")`），以及 `CIM_DATETIME`
    （wmic `/format:list` 的原样输出）。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().strip('"')
    iso = _parse_state_time(text)
    if iso is not None:
        return iso
    match = _CIM_DATETIME_RE.match(text)
    if match is None:
        return None
    try:
        base = datetime.strptime(match.group("date") + match.group("time"), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    micro = int(match.group("frac").ljust(6, "0"))
    minutes = int(match.group("offset"))
    sign = 1 if match.group("sign") == "+" else -1
    offset = timezone(sign * timedelta(minutes=minutes))
    return base.replace(microsecond=micro, tzinfo=offset).astimezone(UTC)


def _parse_creation_output(output: str) -> datetime | None:
    """解析 CIM / wmic 的创建时间输出（`CreationDate=...` 或裸时间串）。"""
    for line in (output or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if text.lower().startswith("creationdate="):
            text = text.split("=", 1)[1].strip()
        moment = _parse_process_time(text)
        if moment is not None:
            return moment
    return None


def _linux_process_created_at(pid: int) -> datetime | None:  # pragma: no cover - Windows 为主
    """`/proc/<pid>/stat` 第 22 个字段（starttime，单位 clock tick）+ `/proc/stat` 的 btime。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        fields = stat[stat.rindex(")") + 2 :].split()
        start_ticks = int(fields[19])  # 扣掉 pid / comm 后从 0 数，starttime 是第 22 个（索引 19）
        btime = None
        for row in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if row.startswith("btime "):
                btime = int(row.split()[1])
                break
        hertz = int(os.sysconf("SC_CLK_TCK"))  # 仅 POSIX：调用方已按 os.name 分流，Windows 不走这里
    except (OSError, ValueError, IndexError):
        return None
    if btime is None or hertz <= 0:
        return None
    return datetime.fromtimestamp(btime + start_ticks / hertz, tz=UTC)


def process_created_at(pid: int) -> datetime | None:
    """查 PID 的进程创建时间（CIM 优先、wmic 兜底；查不到返回 None）。

    只读查询，绝不终止任何东西。**查不到就返回 None**——调用方据此拒绝终止，
    绝不因为「查不到时间」放行（QUOT-177 P1-2：堵住 PID 复用窗口）。

    单独成一个函数而不是往 `process_image` 里塞第三个返回值（QUOT-177 方案 A）：
    `process_image(pid, cwd) -> (exe, name)` 的签名与注入点 `ControlPlane(process_info=...)`
    保持不变，既有测试替身一条都不用改；新依赖走独立的 `process_started` 注入点。
    """
    number = int(pid)
    if os.name != "nt":  # pragma: no cover - 本项目以 Windows 为主
        return _linux_process_created_at(number)

    queries = (
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={number}\"; "
                'if ($p) { $p.CreationDate.ToUniversalTime().ToString("o") }'
            ),
        ],
        [
            "wmic",
            "process",
            "where",
            f"ProcessId={number}",
            "get",
            "CreationDate",
            "/format:list",
        ],
    )
    for args in queries:
        result = run_step(args, Path.cwd(), PROCESS_QUERY_TIMEOUT_SEC)
        if not result.ok:
            continue
        created_at = _parse_creation_output(result.output)
        if created_at is not None:
            return created_at
    return None


# --------------------------------------------------------------------------- #
# 控制面
# --------------------------------------------------------------------------- #

class ControlPlane:
    """控制面的全部实现；依赖全部可注入，测试用 mock 顶掉子进程/端口/HTTP。"""

    def __init__(
        self,
        *,
        data_root: Path,
        peers: Mapping[str, PeerControl] | None = None,
        sources_loader: Callable[[], Mapping[str, Any]] | None = None,
        run: Callable[[list[str], Path, float], StepResult] | None = None,
        port_probe: Callable[[int | None], list[PortOwner]] | None = None,
        health_probe: Callable[[str, float], tuple[bool, str]] | None = None,
        http_post: Callable[[str, dict[str, Any], dict[str, str], float], tuple[int, str]]
        | None = None,
        spawn: Callable[..., Any] | None = None,
        process_info: Callable[[int, Path], tuple[str | None, str | None]] | None = None,
        process_started: Callable[[int], datetime | None] | None = None,
        resolve: Callable[[str], list[str]] | None = None,
        harden_acl: Callable[[Path], None] | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.peers: dict[str, PeerControl] = dict(peers or PEER_CONTROL)
        # 启动即校验 peer 目录（QUOT-194 P1-3）：共享盘上的 peer 等于把执行权
        # 交给别人，命中即抛 SharedPeerDirectoryError，不进入任何动作。
        require_local_peer_directories(
            {key: ctl.directory for key, ctl in self.peers.items()}
        )
        self._sources_loader = sources_loader or load_sources_peers
        self._run = run or run_step
        self._port_probe = port_probe or probe_port
        self._health = health_probe or probe_health
        self._http_post = http_post or _post_json
        self._spawn = spawn or _spawn_process
        self._process_info = process_info or process_image
        # 进程创建时间单独注入（QUOT-177 P1-2）：测试给假时间，生产走 CIM/wmic
        self._process_started = process_started or process_created_at
        self._resolve = resolve or resolve_host_ips
        # 文件权限收口可注入（QUOT-194 P1-3）：测试用它构造「icacls 失败」路径
        self._harden_acl = harden_acl or harden_file_acl

        self._state_path = self.data_root / CONTROL_STATE_SUBDIR / CONTROL_STATE_FILE
        self._logs_dir = self.data_root / CONTROL_LOG_SUBDIR
        self._lock = threading.RLock()
        # 验证条件的「读-改-写」串行化：merge 必须先读到盘上现状，不能让两个请求交错
        self._validation_lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._last_job: dict[str, Job] = {}
        self._handles: dict[str, list[Any]] = {}
        self._state: dict[str, Any] = self._load_state()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ldst-control")

    # ---------------------------------------------------------------- 状态文件

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"peers": {}}
        if not isinstance(data, dict) or not isinstance(data.get("peers"), dict):
            return {"peers": {}}
        return data

    def _save_state(self) -> None:
        """原子落盘并收紧 DACL（QUOT-194 P1-3）。

        顺序：写 `<name>.tmp` → `icacls` 收紧 tmp 的 DACL → `os.replace` 原子替换。
        这样成品路径上永远不会出现「权限还没收口」的窗口（含首次创建）。

        失败抛 `StatePersistError`（fail closed）：状态文件是「只终止自己启动的
        进程」的唯一证据，宁可这次不记录，也不留一个别人能改写的版本。
        """
        tmp = self._state_path.with_name(f"{self._state_path.name}.tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._harden_acl(tmp)
            os.replace(tmp, self._state_path)
        except AclError as exc:
            self._discard_tmp(tmp)
            raise StatePersistError(
                f"无法保存控制面状态文件：{exc}。为避免留下可被其他账户篡改的 "
                f"{CONTROL_STATE_FILE}（谁改了它就能诱导控制面终止别的进程），"
                "本次操作已中止；若刚启动过 peer，请人工核对端口占用与进程后再处理"
            ) from exc
        except OSError as exc:
            self._discard_tmp(tmp)
            raise StatePersistError(
                f"无法写入控制面状态文件 {self._state_path}：{exc}；本次操作已中止"
            ) from exc

    @staticmethod
    def _discard_tmp(tmp: Path) -> None:
        """清掉半成品，不留未收口权限的文件。"""
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不改变结论
            pass

    def _peer_state(self, key: str) -> dict[str, Any]:
        entry = self._state.setdefault("peers", {}).setdefault(key, {})
        if not isinstance(entry, dict):  # pragma: no cover - 手改坏的 state
            entry = {}
            self._state["peers"][key] = entry
        return entry

    # ---------------------------------------------------------------- 基础探测

    def _sources_peers(self) -> Mapping[str, Any]:
        try:
            peers = self._sources_loader() or {}
        except Exception:  # noqa: BLE001 - 配置坏了不能让控制面 500
            return {}
        return peers if isinstance(peers, Mapping) else {}

    @staticmethod
    def _venv_dir(ctl: PeerControl) -> Path:
        return Path(ctl.directory) / ".venv"

    def _venv_python(self, ctl: PeerControl) -> Path:
        venv = self._venv_dir(ctl)
        return venv / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )

    def _venv_script(self, ctl: PeerControl, name: str) -> Path:
        venv = self._venv_dir(ctl)
        suffix = ".exe" if os.name == "nt" else ""
        return venv / ("Scripts" if os.name == "nt" else "bin") / f"{name}{suffix}"

    def import_version(self, ctl: PeerControl) -> str | None:
        """`import <pkg>; print(<pkg>.__version__)` 探测（15s 超时，PRD §3.1）。"""
        python = self._venv_python(ctl)
        if not python.exists():
            return None
        package = ctl.import_package
        result = self._run(
            [str(python), "-c", f"import {package}; print(getattr({package}, '__version__', ''))"],
            Path(ctl.directory),
            IMPORT_PROBE_TIMEOUT_SEC,
        )
        if not result.ok:
            return None
        lines = tail_lines(result.output, 1)
        return lines[0] if lines else ""

    def _dist_wheel(self, ctl: PeerControl) -> Path | None:
        dist = Path(ctl.directory) / "dist"
        if not dist.is_dir():
            return None
        wheels = sorted(dist.glob("*.whl"))
        return wheels[-1] if wheels else None

    def _log_path(self, key: str) -> Path:
        return self._logs_dir / f"{key}.log"

    # ---------------------------------------------------------------- 快照

    def snapshot(self, key: str) -> dict[str, Any]:
        """单个 peer 的探测结果（状态判定顺序严格按 PRD §3.1）。"""
        ctl = self.peers[key]
        directory = Path(ctl.directory)
        dir_exists = directory.is_dir()
        registered = key in self._sources_peers()

        version: str | None = None
        installed = False
        if dir_exists and self._venv_python(ctl).exists():
            version = self.import_version(ctl)
            installed = version is not None

        mcp_owners = self._port_probe(ctl.mcp_port)
        web_owners = self._port_probe(ctl.web_port) if ctl.web_port else []
        listening = bool(mcp_owners or web_owners)
        conflicts = self._port_conflicts(key, ctl, mcp_owners, web_owners)

        health: dict[str, Any] = {"ok": None, "detail": ""}
        if listening:  # 有监听就探一次：占用者到底是不是本方 peer，靠健康检查说话
            health = self._health_snapshot(ctl)

        status, detail = self._decide_status(
            key=key,
            registered=registered,
            dir_exists=dir_exists,
            installed=installed,
            listening=listening,
            health_ok=health["ok"],
            health_detail=health["detail"],
            foreign_ports=[c["port"] for c in conflicts if c.get("foreign")],
        )

        last_job = self._last_job.get(key)
        running_with_web = status == STATUS_RUNNING and bool(ctl.web_port)
        open_url = f"http://127.0.0.1:{ctl.web_port}" if running_with_web else None
        return {
            "id": key,
            "product": self._product_name(key),
            "role": self._peer_role(key),
            "directory": str(directory),
            "directory_exists": dir_exists,
            "registered": registered,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "detail": detail,
            "installed": installed,
            "version": version,
            "venv": str(self._venv_dir(ctl)) if self._venv_dir(ctl).is_dir() else None,
            "ports": {"mcp": ctl.mcp_port, "web": ctl.web_port, "web_label": ctl.web_port_label},
            "listening": {
                "mcp": [owner.as_dict() for owner in mcp_owners],
                "web": [owner.as_dict() for owner in web_owners],
            },
            "health": health,
            "dist": {
                "wheel": (wheel.name if (wheel := self._dist_wheel(ctl)) else None),
                "editable": not ctl.has_wheel_dist,
                "env_file": str(directory / ctl.env_filename),
            },
            "port_conflicts": conflicts,
            "last_job": last_job.view() if last_job is not None else None,
            "open_url": open_url,
            "actions": {
                "can_install": dir_exists and registered and not installed,
                "can_start": installed and not listening and not self._job_running(key),
                "can_stop": bool(state_pids(self._peer_state(key))) or listening,
                "can_open": open_url is not None,
            },
        }

    def _health_snapshot(self, ctl: PeerControl) -> dict[str, Any]:
        """健康探测优先打 Web 端口，其次 MCP 端口（两者都记进 detail）。"""
        attempts: list[tuple[str, str]] = []
        if ctl.web_port:
            attempts.append((f"http://127.0.0.1:{ctl.web_port}{ctl.health_path}", "web"))
        attempts.append((f"http://127.0.0.1:{ctl.mcp_port}{ctl.health_path}", "mcp"))
        details: list[str] = []
        for url, label in attempts:
            ok, detail = self._health(url, HEALTH_TIMEOUT_SEC)
            details.append(f"{label}: {detail}")
            if ok:
                return {"ok": True, "detail": "；".join(details)}
        return {"ok": False, "detail": "；".join(details)}

    def _decide_status(
        self,
        *,
        key: str,
        registered: bool,
        dir_exists: bool,
        installed: bool,
        listening: bool,
        health_ok: bool | None,
        health_detail: str,
        foreign_ports: list[int] | None = None,
    ) -> tuple[str, str]:
        if not dir_exists:
            return STATUS_UNCONFIGURED, f"目录不存在：{self.peers[key].directory}"
        if not registered:
            return STATUS_UNCONFIGURED, "未在 config/sources.yaml 的 peers 登记"

        last = self._last_job.get(key)
        active_job = last is not None and last.status in {"queued", "running"}
        foreign = sorted(set(foreign_ports or []))

        if listening:
            if health_ok:
                return STATUS_RUNNING, f"端口已监听，健康检查通过（{health_detail}）"
            if foreign and not installed:
                ports = "、".join(str(port) for port in foreign)
                return (
                    STATUS_FAILED,
                    f"端口 {ports} 被其他进程占用（不是本控制面启动的），且健康检查失败："
                    f"{health_detail or '未知'}；不会终止占用者",
                )
            return STATUS_FAILED, f"端口已监听但健康检查失败（{health_detail or '未知'}）"
        if active_job and last is not None and last.action == "start":
            return STATUS_STARTING, f"启动中：{last.step or '准备中'}"
        if last is not None and last.status == "failed":
            return STATUS_FAILED, f"{last.action} 失败：{last.error or '未知错误'}"
        pids = state_pids(self._peer_state(key))
        if pids and not any(_pid_alive(pid) for pid in pids):
            return STATUS_STOPPED, "进程已退出，端口无监听"
        if pids:
            return STATUS_FAILED, "进程仍在但端口无监听（保留进程日志）"
        if not installed:
            return STATUS_NOT_INSTALLED, self._not_installed_detail(key)
        if self._peer_state(key).get("last_stop_at"):
            return STATUS_STOPPED, "已按控制面停止"
        return STATUS_INSTALLED, "已安装，未启动"

    def _not_installed_detail(self, key: str) -> str:
        ctl = self.peers[key]
        venv = self._venv_dir(ctl)
        if not venv.is_dir():
            return f".venv 不存在：{venv}"
        return f"import {ctl.import_package} 失败（{venv} 存在但包不可用）"

    def _port_conflicts(
        self,
        key: str,
        ctl: PeerControl,
        mcp_owners: list[PortOwner],
        web_owners: list[PortOwner],
    ) -> list[dict[str, Any]]:
        """两类冲突：历史默认端口被占（只报告）+ 本 peer 端口被非本控制面进程占用。"""
        conflicts: list[dict[str, Any]] = []
        mine = set(state_pids(self._peer_state(key)))
        for port, note in KNOWN_AVOIDED_PORTS.get(key, ()):  # 历史坑位
            for owner in self._port_probe(port):
                conflicts.append(
                    {"port": port, "pid": owner.pid, "process": owner.process, "note": note}
                )
        for port, owners in ((ctl.mcp_port, mcp_owners), (ctl.web_port, web_owners)):
            if not port:
                continue
            for owner in owners:
                if owner.pid is not None and owner.pid in mine:
                    continue
                conflicts.append(
                    {
                        "port": port,
                        "pid": owner.pid,
                        "process": owner.process,
                        "note": "端口已被其他进程占用（不自动换端口，DEC-004）",
                        "foreign": True,
                    }
                )
        return conflicts

    def _product_name(self, key: str) -> str:
        from ladderstock.core.boundary import PEERS

        peer = PEERS.get(key)
        return peer.product if peer else self.peers[key].key

    def _peer_role(self, key: str) -> str:
        from ladderstock.core.boundary import PEERS

        peer = PEERS.get(key)
        return peer.role if peer else ""

    def board(self) -> dict[str, Any]:
        """四个 peer + 端口占用汇总（`GET /api/control/peers`）。"""
        snapshots = [self.snapshot(key) for key in self.peers]
        conflicts: list[dict[str, Any]] = []
        for snapshot in snapshots:
            for conflict in snapshot["port_conflicts"]:
                conflicts.append({"peer": snapshot["id"], **conflict})
        return {
            "ok": True,
            "control_enabled": control_enabled(),
            "peers": snapshots,
            "port_conflicts": conflicts,
            "ports": {
                "board": 5950,
                "mcp": 5951,
                **{
                    key: {"mcp": ctl.mcp_port, "web": ctl.web_port}
                    for key, ctl in self.peers.items()
                },
            },
        }

    # ---------------------------------------------------------------- 任务编排

    def _job_running(self, key: str) -> bool:
        job = self._last_job.get(key)
        return job is not None and job.status in {"queued", "running"}

    def create_job(self, peer: str, action: str, *, dev: bool = False) -> Job:
        if peer not in self.peers:
            raise KeyError(peer)
        if action not in {"install", "start", "stop"}:
            raise ValueError(f"未知动作：{action}")
        if self._job_running(peer):
            raise RuntimeError(f"{peer} 已有任务在执行，请等待结束")
        job = Job(id=uuid.uuid4().hex, peer=peer, action=action, dev=dev)
        with self._lock:
            self._jobs[job.id] = job
            self._last_job[peer] = job
        self._executor.submit(self._execute, job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _execute(self, job: Job) -> None:
        job.status = "running"
        job.started_at = _now_iso()
        job.log(f"{job.action} {job.peer} 开始")
        try:
            if job.action == "install":
                self._install(job)
            elif job.action == "start":
                self._start(job)
            else:
                self._stop(job)
        except JobFailure as exc:
            job.error = str(exc)
            job.status = "failed"
            job.log(f"失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 任务线程不能把异常吞成静默
            job.error = f"内部错误：{exc}"
            job.status = "failed"
            job.log(f"内部错误：{exc!r}")
        else:
            job.status = "ok"
            job.progress = 1.0
            job.log("完成")
        finally:
            job.ended_at = _now_iso()
            if job.status == "ok":
                job.progress = 1.0

    def _step(
        self,
        job: Job,
        name: str,
        args: list[str] | None,
        *,
        cwd: Path,
        timeout: float,
        retries: int = 1,
        progress: float | None = None,
    ) -> StepResult:
        """跑一步：最多 `retries + 1` 次（DEC-014 硬约束 5：只重试 1 次）。"""
        job.step = name
        if progress is not None:
            job.progress = progress
        job.log(f"[{name}]")
        if args is None:
            return StepResult(0)
        result = StepResult(0)
        for attempt in range(retries + 1):
            if attempt:
                job.log(f"  重试（第 {attempt} 次）")
            job.log(f"  $ {' '.join(args)}")
            result = self._run(args, cwd, timeout)
            for line in tail_lines(result.output):
                job.log(f"  {line}")
            if result.ok:
                return result
            job.log(f"  退出码 {result.returncode}")
        raise JobFailure(f"{name} 失败（退出码 {result.returncode}）")

    def _install(self, job: Job) -> None:
        """五步安装，动作写死在 PRD §4，不允许扩展（DEC-014 §4）。"""
        ctl = self.peers[job.peer]
        directory = Path(ctl.directory)
        if not directory.is_dir():
            raise JobFailure(f"目录不存在：{directory}")
        python = sys.executable
        venv_python = self._venv_python(ctl)

        self._step(job, "检查目录", None, cwd=directory, timeout=1, progress=0.05)
        if self._venv_dir(ctl).is_dir():
            job.log(f"  .venv 已存在，跳过创建：{self._venv_dir(ctl)}")
        else:
            self._step(
                job,
                "创建虚拟环境",
                [python, "-m", "venv", str(self._venv_dir(ctl))],
                cwd=directory,
                timeout=INSTALL_STEP_TIMEOUT_SEC,
                progress=0.2,
            )
        self._step(
            job,
            "升级 pip",
            [str(venv_python), "-m", "pip", "install", "-U", "pip"],
            cwd=directory,
            timeout=INSTALL_STEP_TIMEOUT_SEC,
            progress=0.4,
        )
        wheel = self._dist_wheel(ctl)
        if wheel is not None:
            target = [str(wheel)]
            job.log(f"  使用本地产物：{wheel.name}")
        else:
            target = ["-e", "."]
            job.log("  无 dist/*.whl，退化为 pip install -e .")
        self._step(
            job,
            "安装项目",
            [str(venv_python), "-m", "pip", "install", *target],
            cwd=directory,
            timeout=INSTALL_STEP_TIMEOUT_SEC,
            progress=0.6,
        )
        if job.dev:
            self._step(
                job,
                "安装开发依赖",
                [str(venv_python), "-m", "pip", "install", "-e", ".[dev]"],
                cwd=directory,
                timeout=INSTALL_STEP_TIMEOUT_SEC,
                progress=0.8,
            )
        self._step(
            job,
            "校验导入",
            [
                str(venv_python),
                "-c",
                f"import {ctl.import_package}; print({ctl.import_package}.__version__)",
            ],
            cwd=directory,
            timeout=IMPORT_PROBE_TIMEOUT_SEC,
            progress=0.95,
        )

    def _start(self, job: Job) -> None:
        ctl = self.peers[job.peer]
        directory = Path(ctl.directory)

        # 1. 端口预检：已被占用直接失败，**不自动换端口**（DEC-004 / DEC-014 §3）
        occupied = self._occupied_ports(ctl)
        if occupied:
            port, owner = occupied[0]
            raise JobFailure(
                f"端口 {port} 已被占用（PID {owner.pid or '未知'}，"
                f"{owner.process or '未知进程'}）；按 DEC-004 不自动换端口"
            )

        # 2. 安装检查
        if self.import_version(ctl) is None:
            raise JobFailure(f"未安装：import {ctl.import_package} 失败，先执行安装")

        # 3. 逐个拉起启动命令（参数数组，不拼 shell）
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path(job.peer)
        records: list[dict[str, Any]] = []
        for spec in ctl.start_commands:
            exe = self._venv_script(ctl, spec[0])
            if not exe.exists():
                raise JobFailure(f"入口不存在：{exe}（重新安装该 peer）")
            job.step = f"启动 {spec[0]}"
            job.log(f"  $ {exe.name} {' '.join(spec[1:])}")
            try:
                handle = self._spawn(
                    [str(exe), *spec[1:]],
                    cwd=directory,
                    log_path=log_path,
                )
            except OSError as exc:
                raise JobFailure(f"拉起 {spec[0]} 失败：{exc}") from exc
            # 落盘「我们启动过谁」：PID + 可执行文件绝对路径，停止前据此证明归属
            records.append(
                {
                    "pid": int(handle.pid),
                    "exe": _abs_path(exe),
                    "started_at": _now_iso(),
                }
            )
            self._handles.setdefault(job.peer, []).append(handle)
            job.log(f"  PID {handle.pid}（{_abs_path(exe)}）")

        pids = [record["pid"] for record in records]
        entry = self._peer_state(job.peer)
        entry["pids"] = records
        entry["last_start_at"] = _now_iso()
        entry.pop("last_stop_at", None)
        self._save_state()

        # 4. 等端口（最多 30s），起不来就是「异常」，日志留着给人看
        job.step = "等待端口监听"
        deadline = time.monotonic() + START_GRACE_SEC
        while time.monotonic() < deadline:
            if self._port_probe(ctl.mcp_port) or (ctl.web_port and self._port_probe(ctl.web_port)):
                job.log(f"  端口已监听（{time.monotonic() - (deadline - START_GRACE_SEC):.1f}s）")
                return
            time.sleep(0.5)
        raise JobFailure(
            f"启动后 {START_GRACE_SEC:.0f} 秒内端口无监听（进程 PID {pids} 仍在，"
            f"日志见 {log_path}）"
        )

    def _ownership_refusal(self, ctl: PeerControl, record: Mapping[str, Any]) -> str | None:
        """终止前的归属校验：不通过就返回可读原因（**绝不降级成盲杀**，QUOT-123 P0-2）。

        判据（全部满足才允许 `taskkill`）：
        1. 状态是新格式（带可执行文件路径），否则无法证明归属；
        2. PID 仍存活；
        3. CIM / wmic 能查到 `ExecutablePath`（查不到就拒绝，不做无证据的杀）；
        4. 进程名不在系统/关键进程黑名单里；
        5. 可执行文件在该 peer 的 `ctl.directory` 之下；
        6. 实时 `ExecutablePath` 与启动记录 `record["exe"]` **归一后逐字相等**
           （缺记录同样拒绝，QUOT-177 P1-2：「同目录不同 exe」不能再穿过去）；
        7. 进程创建时间与 `record["started_at"]` 相差不超过
           `PROCESS_START_TOLERANCE_SEC`（取不到时间同样拒绝，堵 PID 复用）。

        拒绝一律**失败关闭**：查不到证据就是不放行，绝不拿「在目录下」当替代证据。
        """
        pid = int(record["pid"])
        if record.get("legacy"):
            return (
                f"无法确认该 PID 属于本 peer：PID {pid} 来自旧格式状态文件（只有 PID、"
                "没有可执行文件路径）。请重新启动该 peer，或人工核对后清理 "
                "data/control/peer_state.json 里的 pids"
            )
        if not _pid_alive(pid):
            return (
                f"无法确认该 PID 属于本 peer：PID {pid} 已不存在（可能已退出或已被复用），"
                "未执行任何终止"
            )
        exe, name = self._process_info(pid, Path(ctl.directory))
        if not exe:
            return (
                f"无法确认该 PID 属于本 peer：查不到 PID {pid} 的可执行文件路径"
                "（Get-CimInstance / wmic 均不可用），为安全起见不终止"
            )
        label = (name or os.path.basename(exe)).strip().lower()
        if label in DENY_PROCESS_NAMES:
            return (
                f"拒绝终止 PID {pid}：{name or exe} 属于受保护的系统/关键进程"
            )
        if not _is_under(exe, ctl.directory):
            return (
                f"拒绝终止 PID {pid}：可执行文件不在本 peer 目录内"
                f"（{_abs_path(exe)} 不在 {_abs_path(ctl.directory)} 之下），"
                "不属于本控制面启动的进程"
            )

        # 6. 与启动记录里的 `record["exe"]` 逐字比对（QUOT-177 P1-2）：
        #    「同目录 + 同 PID 不同 exe」以前能穿过上面所有判据，这里补上硬证据
        recorded_exe = str(record.get("exe") or "").strip()
        if not recorded_exe:
            return (
                f"拒绝终止 PID {pid}：状态文件里没有该 PID 的可执行文件路径，"
                "无法与实时进程比对（请重新启动该 peer，或人工核对后清理 "
                "data/control/peer_state.json 里的 pids）"
            )
        if _norm_exe(exe) != _norm_exe(recorded_exe):
            return (
                f"拒绝终止 PID {pid}：可执行文件与启动记录不符："
                f"记录 {_abs_path(recorded_exe)}，实际 {_abs_path(exe)}"
            )

        # 7. 创建时间比对（QUOT-177 P1-2）：同 exe 路径被 PID 复用时，只有时间能区分
        recorded_started = _parse_state_time(record.get("started_at"))
        if recorded_started is None:
            return (
                f"拒绝终止 PID {pid}：状态文件里的启动时间不可用（"
                f"{record.get('started_at')!r}），无法排除 PID 复用"
            )
        started_at = self._process_started(pid)
        if started_at is None:
            return (
                f"无法确认该 PID 属于本 peer：查不到 PID {pid} 的进程创建时间"
                "（Get-CimInstance / wmic 均不可用），为安全起见不终止"
            )
        drift = abs((_as_utc(started_at) - recorded_started).total_seconds())
        if drift > PROCESS_START_TOLERANCE_SEC:
            # 一位小数 + 写明容差（QUOT-194 P2-8）：`:.0f` 会把 5.4s 印成「5 秒」，
            # 看着像「明明在 5s 容差内却被拒」，会诱导下游去放宽判据
            return (
                f"拒绝终止 PID {pid}：进程创建时间与启动记录不符"
                f"（记录 {recorded_started.isoformat()}，实际 "
                f"{_as_utc(started_at).isoformat()}，相差 {drift:.1f} 秒，"
                f"容差 {PROCESS_START_TOLERANCE_SEC:.0f} 秒），疑似 PID 复用"
            )
        return None

    def _stop(self, job: Job) -> None:
        ctl = self.peers[job.peer]
        records = pid_records(self._peer_state(job.peer))

        # 只终止「本控制面启动过」的进程（DEC-014 硬约束 3）
        if not records:
            occupied = self._occupied_ports(ctl)
            if occupied:
                port, owner = occupied[0]
                raise JobFailure(
                    f"未记录到本控制面启动的进程；端口 {port} 被 PID "
                    f"{owner.pid or '未知'}（{owner.process or '未知进程'}）占用，"
                    "不终止非本控制面进程"
                )
            raise JobFailure("未记录到该 peer 的进程，未执行任何终止")

        # 先逐个校验归属，全部通过才开始终止：任一不过整单拒绝，绝不部分盲杀
        refusals: list[str] = []
        for record in records:
            pid = int(record["pid"])
            job.step = f"校验 PID {pid} 归属"
            job.log(f"  校验 PID {pid} 的可执行文件是否在 {_abs_path(ctl.directory)} 之下")
            reason = self._ownership_refusal(ctl, record)
            if reason:
                refusals.append(reason)
        if refusals:
            # 拒绝终止：不清空 pids、不写 last_stop_at（把证据和状态原样留给人看）
            raise JobFailure("；".join(refusals) + "。未执行任何终止，状态文件保持不变")

        for record in records:
            pid = int(record["pid"])
            job.step = f"终止 PID {pid}"
            job.log(f"  $ taskkill /PID {pid} /T /F")
            if os.name == "nt":
                self._run(["taskkill", "/PID", str(pid), "/T", "/F"], Path(ctl.directory), 30.0)
            else:  # pragma: no cover - 本项目以 Windows 为主
                self._run(["kill", "-TERM", str(pid)], Path(ctl.directory), 30.0)

        job.step = "等待端口释放"
        deadline = time.monotonic() + START_GRACE_SEC
        while time.monotonic() < deadline:
            if not self._occupied_ports(ctl):
                break
            time.sleep(0.5)

        entry = self._peer_state(job.peer)
        entry["pids"] = []
        entry["last_stop_at"] = _now_iso()
        self._save_state()
        for handle in self._handles.pop(job.peer, []):
            try:
                handle.wait(timeout=1)
            except Exception:  # noqa: BLE001 - 收尾失败不影响结论
                pass

    def _occupied_ports(self, ctl: PeerControl) -> list[tuple[int, PortOwner]]:
        occupied: list[tuple[int, PortOwner]] = []
        for port in (ctl.mcp_port, ctl.web_port):
            if not port:
                continue
            for owner in self._port_probe(port):
                occupied.append((port, owner))
        return occupied

    # ---------------------------------------------------------------- 验证条件

    def write_validation_conditions(self, payload: Any) -> dict[str, Any]:
        """`PUT /api/control/validation/conditions`（QUOT-197）。

        落盘目标 `<data_root>/state/validation/<trade_date>.json`，**只改**
        `conditions` 与 `updated_at`：文件里已有的 `reconcile` / `date` /
        `generated_at` 等一概原样保留。写前把原文件复制成 `<file>.bak`，
        真正写入走 `core/validation.py:write_document()`（临时文件 + `os.replace`）。
        """
        trade_date, incoming, mode = parse_validation_request(payload)
        path = validation_path(trade_date, self.data_root)

        with self._validation_lock:
            document = load_validation(trade_date, root=self.data_root)
            if document is None:
                if path.is_file():
                    # 文件在但读不出来：宁可 500 也不拿骨架覆盖掉可能还有价值的旧内容
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"已有验证文件不可读，未覆盖：{path}"
                            "（请先人工修复或移走该文件后重试）"
                        ),
                    )
                document = {
                    "trade_date": trade_date.isoformat(),
                    "conditions": [],
                    "reconcile": None,
                }

            existing = document.get("conditions")
            if mode == "replace":
                conditions = list(incoming)
            else:
                conditions = merge_conditions(
                    existing if isinstance(existing, list) else [], incoming
                )

            document["conditions"] = conditions
            document["updated_at"] = _now_iso()

            try:
                if path.is_file():
                    shutil.copy2(path, path.with_name(path.name + ".bak"))
                write_document(path, document)
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"写入验证条件失败：{exc}"
                ) from None

        return {
            "trade_date": trade_date.isoformat(),
            "conditions": conditions,
            "updated_at": document["updated_at"],
            "saved": len(conditions),
        }

    # ---------------------------------------------------------------- 配置读写

    def read_config(self, key: str) -> dict[str, Any]:
        ctl = self.peers[key]
        path = Path(ctl.directory) / ctl.env_filename
        try:
            values = read_env_file(path, strict=True)
        except EnvFileUnreadable as exc:
            # fail closed（QUOT-194 P1-3）：读不到就当「不可读」报错，
            # 不能退化成「全部未配置」骗下游去重填密钥
            raise HTTPException(
                status_code=500,
                detail=f"{exc}；为避免误报「未配置」并覆盖现有密钥，已中止（fail closed）",
            ) from None
        rows: dict[str, Any] = {}
        for name in ctl.config_keys:
            raw = values.get(name, "")
            if name in ctl.secret_keys:
                rows[name] = {
                    "set": bool(raw),
                    "secret": True,
                    "preview": secret_preview(raw) if raw else "",
                }
            else:
                rows[name] = {"set": bool(raw), "secret": False, "value": raw or None}
        return {
            "peer": key,
            "env_file": str(path),
            "exists": path.is_file(),
            "keys": list(ctl.config_keys),
            "values": rows,
            "notes": list(ctl.config_notes),
        }

    def update_config(
        self, key: str, values: Mapping[str, str], clear: Iterable[str]
    ) -> dict[str, Any]:
        ctl = self.peers[key]
        allowed = set(ctl.config_keys)
        # 白名单之外一律拒绝（DEC-014 硬约束 2：不接受自由字符串）
        unknown = sorted(set(values) - allowed)
        unknown += sorted(set(clear) - allowed)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"不允许的配置键：{', '.join(sorted(set(unknown)))}"
                f"（白名单：{', '.join(ctl.config_keys)}）",
            )
        updates: dict[str, str] = {}
        for name, value in values.items():
            text = "" if value is None else str(value)
            if text == "":
                continue  # PRD §5：空字符串 = 不修改
            if "\n" in text or "\r" in text:
                raise HTTPException(status_code=400, detail=f"{name} 不允许包含换行")
            updates[name] = text
        removals = [name for name in clear if name not in updates]

        path = Path(ctl.directory) / ctl.env_filename
        if not path.parent.is_dir():
            raise HTTPException(status_code=400, detail=f"peer 目录不存在：{path.parent}")
        try:
            write_env_file(path, updates, removals, harden=self._harden_acl)
        except EnvFileUnreadable as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        except (AclError, OSError) as exc:
            # fail closed：权限收不口就不写成品文件，也不假装成功
            raise HTTPException(
                status_code=500,
                detail=f"写入 {path.name} 失败或收紧 DACL 失败：{exc}；已中止，未留下半成品文件",
            ) from None
        payload = self.read_config(key)
        payload["updated"] = sorted(updates)
        payload["cleared"] = sorted(removals)
        return payload

    # ---------------------------------------------------------------- AI 连通性

    def ai_test(
        self,
        *,
        peer: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        if peer not in self.peers:
            raise HTTPException(status_code=400, detail=f"未知 peer：{peer}")
        base = (base_url or "").strip().rstrip("/")
        parsed_url = urllib.parse.urlsplit(base)
        if parsed_url.scheme not in {"http", "https"}:
            raise HTTPException(status_code=400, detail="base_url 必须是 http(s) 地址")
        target_host = (parsed_url.hostname or "").strip()
        if not target_host:
            raise HTTPException(status_code=400, detail="base_url 缺少主机名")
        if not (model or "").strip():
            raise HTTPException(status_code=400, detail="model 不能为空")

        ctl = self.peers[peer]
        key = (api_key or "").strip()
        if not key:
            # 已存密钥只在白名单主机上回落，否则要求显式提供（QUOT-123 C10）
            try:
                stored = read_env_file(Path(ctl.directory) / ctl.env_filename, strict=True)
            except EnvFileUnreadable as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"{exc}；无法确认是否已存密钥，为避免退化成无鉴权请求，已中止",
                ) from None
            stored_key = next(
                (stored[name] for name in ctl.secret_keys if stored.get(name)), ""
            )
            if stored_key:
                allowed = ai_allowed_hosts()
                if not host_in_allowlist(target_host, allowed):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "该地址不在密钥可用白名单内，请显式提供 api_key"
                            f"（已存密钥只允许发往：{', '.join(sorted(allowed))}）"
                        ),
                    )
                key = stored_key

        # SSRF 判据：解析后的每一个地址都必须是公网（QUOT-123 C10）。
        # 唯一例外是「不带任何凭据」时探测本机 provider（Ollama 等）——此时没有
        # 可外泄的密钥，且回环探测是既有功能（见 PRD §5 / 现有验收用例）。
        try:
            targets = [str(ip) for ip in self._resolve(target_host)]
        except (OSError, UnicodeError) as exc:
            raise HTTPException(
                status_code=400, detail=f"base_url 主机无法解析：{target_host}（{exc}）"
            ) from None
        if not targets:
            raise HTTPException(status_code=400, detail=f"base_url 主机无法解析：{target_host}")
        loopback_only = all(is_loopback_host(ip) for ip in targets)
        if loopback_only:
            if key:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"base_url 指向本机地址（{target_host}）时不允许携带密钥；"
                        "请改用外部地址，或去掉 api_key 做无凭据探测"
                    ),
                )
        else:
            for ip in targets:
                if is_forbidden_target_ip(ip):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"base_url 解析到内网/链路本地/保留地址（{ip}），已拒绝"
                            "（防止 SSRF 探测本机与云元数据服务）"
                        ),
                    )

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        payload = {
            "model": model.strip(),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
        started = time.monotonic()
        try:
            status, body = self._http_post(f"{base}/chat/completions", payload, headers, 10.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model_seen": None,
                "error": mask_secrets(f"请求失败：{exc}", [key]),
            }
        latency = int((time.monotonic() - started) * 1000)
        seen: str | None = None
        error: str | None = None
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            seen = parsed.get("model") if isinstance(parsed.get("model"), str) else None
        if 300 <= status < 400:
            # 不跟随重定向：3xx 一律算失败，不把重定向响应当成功解析
            error = f"重定向被拒绝（HTTP {status}）：控制面不跟随重定向"
        elif status >= 400:
            error = (
                str(parsed.get("error") or f"HTTP {status}")
                if isinstance(parsed, dict)
                else f"HTTP {status}"
            )
        return {
            "ok": 200 <= status < 300,
            "latency_ms": latency,
            "model_seen": seen,
            "error": mask_secrets(error, [key]) if error else None,
        }

    # ---------------------------------------------------------------- 日志

    def read_logs(self, key: str, tail: int = 200) -> dict[str, Any]:
        ctl = self.peers[key]
        path = self._log_path(key)
        bound = max(1, min(int(tail), 1000))
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        try:
            stored = read_env_file(Path(ctl.directory) / ctl.env_filename, strict=True)
        except EnvFileUnreadable as exc:
            # 读不到密钥就脱不了敏（QUOT-194 P1-3）：宁可不给日志，也不能漏明文
            return {
                "peer": key,
                "path": str(path),
                "exists": path.is_file(),
                "lines": [],
                "error": f"{exc}；无法对日志脱敏，已隐去全部日志行（fail closed）",
            }
        secrets = [stored.get(name, "") for name in ctl.secret_keys]
        return {
            "peer": key,
            "path": str(path),
            "exists": path.is_file(),
            "lines": [mask_secrets(line, secrets) for line in lines[-bound:]],
        }


def _pid_alive(pid: Any) -> bool:
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(number, 0)
        except OSError:
            return False
        return True
    proc = subprocess.run(
        ["tasklist", "/FI", f"PID eq {number}", "/FO", "CSV", "/NH"],
        shell=False,
        timeout=5,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return "No tasks" not in (proc.stdout or "")


def _spawn_process(
    args: list[str], *, cwd: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    """在独立控制台里拉起 peer 进程，stdout/stderr 落到日志文件（不拼 shell）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - 生命周期跟随子进程
    flags = 0
    if os.name == "nt":  # pragma: no cover - Windows 专用
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        shell=False,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=_clean_env(),
        creationflags=flags,
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """不跟随任何 3xx（QUOT-123 C10：重定向可把密钥带去攻击者地址）。"""

    def redirect_request(  # noqa: ANN001 - urllib 回调签名
        self, req, fp, code, msg, headers, newurl
    ) -> None:
        return None


def _build_opener(*, no_proxy: bool = False) -> urllib.request.OpenerDirector:
    """构造 opener：一定包含「不跟随重定向」，可选绕开系统代理。"""
    handlers: list[Any] = [_NoRedirectHandler()]
    if no_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    host = urllib.parse.urlsplit(url).hostname or ""
    # 本机 provider（Ollama 等）不能走系统代理；外网地址保留系统代理
    opener = _build_opener(no_proxy=is_loopback_host(host))
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8", "replace")
            return int(getattr(response, "status", 200)), body
    except urllib.error.HTTPError as exc:
        # 3xx 也会走到这里（redirect_request 返回 None），原样交给调用方判失败
        return exc.code, exc.read().decode("utf-8", "replace")


def load_sources_peers() -> Mapping[str, Any]:
    """读 `config/sources.yaml` 的 peers 块（只用来判「是否登记」，不读 URL）。"""
    import yaml

    path = config_dir() / "sources.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    peers = document.get("peers") if isinstance(document, dict) else None
    return peers if isinstance(peers, Mapping) else {}


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

class InstallRequest(BaseModel):
    dev: bool = False


class ConfigUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class AiTestRequest(BaseModel):
    peer: str
    base_url: str
    model: str
    api_key: str | None = None


async def _json_body(request: Request) -> Any:
    """读请求体 JSON；解析失败一律 400（不把 422 / 500 抛给前端）。"""
    try:
        return await request.json()
    except ValueError:  # json.JSONDecodeError / UnicodeDecodeError 都是 ValueError
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from None


def build_router(plane: ControlPlane) -> APIRouter:
    """构造 `/api/control/*` 路由；所有路由都过 `_guard`（开关 + 回环来源）。"""
    router = APIRouter(prefix=CONTROL_PREFIX, dependencies=[Depends(_guard)])

    def _peer_or_404(peer_id: str) -> str:
        if peer_id not in plane.peers:
            raise HTTPException(
                status_code=404,
                detail=f"未知 peer：{peer_id}（可选：{', '.join(plane.peers)}）",
            )
        return peer_id

    @router.get("/peers")
    def list_peers() -> dict[str, Any]:
        return plane.board()

    @router.get("/peers/{peer_id}")
    def get_peer(peer_id: str) -> dict[str, Any]:
        return plane.snapshot(_peer_or_404(peer_id))

    @router.post("/peers/{peer_id}/install")
    def install(peer_id: str, body: InstallRequest | None = None) -> dict[str, Any]:
        key = _peer_or_404(peer_id)
        try:
            job = plane.create_job(key, "install", dev=bool(body.dev) if body else False)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"job_id": job.id, "status": job.status}

    @router.post("/peers/{peer_id}/start")
    def start(peer_id: str) -> dict[str, Any]:
        key = _peer_or_404(peer_id)
        try:
            job = plane.create_job(key, "start")
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"job_id": job.id, "status": job.status}

    @router.post("/peers/{peer_id}/stop")
    def stop(peer_id: str) -> dict[str, Any]:
        key = _peer_or_404(peer_id)
        try:
            job = plane.create_job(key, "stop")
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"job_id": job.id, "status": job.status}

    @router.get("/peers/{peer_id}/logs")
    def logs(
        peer_id: str,
        tail: int = Query(200, ge=1, le=1000, description="返回最后 N 行"),
    ) -> dict[str, Any]:
        return plane.read_logs(_peer_or_404(peer_id), tail=tail)

    @router.get("/peers/{peer_id}/config")
    def read_config(peer_id: str) -> dict[str, Any]:
        return plane.read_config(_peer_or_404(peer_id))

    @router.put("/peers/{peer_id}/config")
    def write_config(peer_id: str, body: ConfigUpdate) -> dict[str, Any]:
        key = _peer_or_404(peer_id)
        return plane.update_config(key, body.values, body.clear)

    @router.post("/ai/test")
    def ai_test(body: AiTestRequest) -> dict[str, Any]:
        _peer_or_404(body.peer)
        return plane.ai_test(
            peer=body.peer,
            base_url=body.base_url,
            model=body.model,
            api_key=body.api_key,
        )

    @router.put("/validation/conditions")
    async def write_validation_conditions(request: Request) -> dict[str, Any]:
        """保存「明日验证条件」到后端（QUOT-197）：`merge` 追加去重 / `replace` 覆盖。

        闸门与整个控制面一致（路由级 `_guard`：开关 → 回环来源 → Host 白名单 →
        写方法 Origin / `Sec-Fetch-Site`），这里不另起一套。
        """
        return plane.write_validation_conditions(await _json_body(request))

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        found = plane.get_job(job_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"未知任务：{job_id}")
        return found.view()

    return router


__all__ = [
    "AI_ALLOWED_HOSTS_ENV",
    "CONTROL_ENABLED_ENV",
    "CONTROL_PREFIX",
    "ControlPlane",
    "EnvFileUnreadable",
    "JOB_TAIL_LINES",
    "Job",
    "JobFailure",
    "KNOWN_AVOIDED_PORTS",
    "MAX_CONDITIONS",
    "MAX_TRIGGER_CHARS",
    "STATUS_LABELS",
    "StatePersistError",
    "VALIDATION_CONDITION_FIELDS",
    "VALIDATION_MODES",
    "VALIDATION_REQUEST_FIELDS",
    "ai_allowed_hosts",
    "build_router",
    "client_host",
    "condition_id",
    "condition_key",
    "control_enabled",
    "host_header_allowed",
    "host_in_allowlist",
    "is_control_path",
    "is_forbidden_target_ip",
    "is_loopback_host",
    "mask_secrets",
    "merge_conditions",
    "origin_allowed",
    "parse_ip",
    "parse_validation_request",
    "pid_records",
    "probe_health",
    "probe_port",
    "process_image",
    "read_env_file",
    "resolve_host_ips",
    "run_step",
    "secret_preview",
    "state_pids",
    "write_env_file",
]
