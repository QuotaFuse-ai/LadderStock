"""子模块自述卡（本地服务发现）。

每个子模块在自己的安装目录里写一张卡：`.runtime/peer.json`，内容是「我是谁、
我在哪、我还活着」。LadderStock 不再靠「基础端口 + 偏移」推算对方地址，而是按
安装目录去读这张卡：有卡且服务可连 → 用卡上的真实端口；没卡 / 卡失效 → 回退旧口径。

为什么是「目录里的卡」而不是集中注册中心：

- 卡片跟着安装目录走。换盘符、换机器、换端口、换安装路径，配置一个字都不用改；
- 零依赖。模块只需要写一个 json 文件，不需要认识 LadderStock 的任何代码；
- 进程重启即重写，pid 换绑天然成立；陈旧卡片靠「端口连不上」自动失效，
  不需要额外的 TTL 心跳和清理线程。

约定的卡片格式（模块侧照此写，缺字段按缺省处理）::

    {
      "module": "tickhub",              # 必填：与 sources.yaml peers 的 key 一致
      "product": "TickHub-CN",          # 可选，展示用
      "env": "test",                    # 可选：prod / test
      "ports": {"mcp": 18000, "web": 18787, "extra": []},
      "urls": {"mcp": "http://127.0.0.1:18000/mcp", "web": "http://127.0.0.1:18787"},
      "pid": 6432,
      "version": "1.1.0",
      "started_at": "2026-11-21T07:30:00+08:00",
      "updated_at": "2026-11-21T07:30:00+08:00"
    }
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

CARD_DIR = ".runtime"
CARD_NAME = "peer.json"
PROBE_TIMEOUT_SEC = 0.4
#: 解析结果短缓存：面板一次刷新会问同一批模块好几次，每次重探 0.4s 会拖成超时。
#: 2 秒足够挡住重复探测，又不会让「刚起来的服务」等太久才被看见。
CACHE_TTL_SEC = 2.0
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def card_path(module_dir: str | os.PathLike[str]) -> Path:
    """该模块安装目录下的自述卡路径（不管文件在不在）。"""
    return Path(module_dir) / CARD_DIR / CARD_NAME


def _port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None


def port_open(port: int | None, timeout: float = PROBE_TIMEOUT_SEC) -> bool:
    """回环端口是否有监听（只读探测，连上即真）。"""
    if not port or port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def read_card(module_dir: str | os.PathLike[str]) -> dict[str, Any] | None:
    """读卡并做最小规范化；文件缺失 / JSON 坏了 / 没有端口，一律当没有卡。"""
    path = card_path(module_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    ports = raw.get("ports")
    if not isinstance(ports, Mapping):
        return None
    normalized: dict[str, Any] = {"mcp": _port(ports.get("mcp")), "web": _port(ports.get("web"))}
    extra = ports.get("extra") or ()
    normalized["extra"] = tuple(
        port for port in (_port(item) for item in extra) if port is not None
    )
    if not any((normalized["mcp"], normalized["web"], normalized["extra"])):
        return None
    for field in ("module", "product", "env", "version", "started_at", "updated_at"):
        if raw.get(field) is not None:
            normalized[field] = raw.get(field)
    normalized["pid"] = raw.get("pid")
    urls = raw.get("urls")
    if isinstance(urls, Mapping):
        normalized["urls"] = dict(urls)
    normalized["path"] = str(path)
    return normalized


def alive(card: Mapping[str, Any], *, timeout: float = PROBE_TIMEOUT_SEC) -> bool:
    """卡片是否仍然成立：清单里的端口至少有一个能连上。

    只认「端口连通」这条硬证据：pid 存活要跨权限查进程创建时间，代价高且易误判；
    端口连不上就是没在服务，卡片再新也不该被采信。
    """
    candidates = [card.get("mcp"), card.get("web"), *(card.get("extra") or ())]
    return any(port_open(_port(item), timeout) for item in candidates)


def resolve(module_dir: str | os.PathLike[str], *, use_cache: bool = True) -> dict[str, Any] | None:
    """取该模块「当前有效」的自述卡；没有 / 已失效返回 None（调用方回退旧口径）。"""
    key = str(Path(module_dir))
    now = time.monotonic()
    if use_cache:
        hit = _CACHE.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    card = read_card(module_dir)
    result = card if card is not None and alive(card) else None
    _CACHE[key] = (now, result)
    return result


def clear_cache() -> None:
    """丢掉解析缓存（测试与「刚点了启动」后立刻重读时用）。"""
    _CACHE.clear()


def write_card(
    module_dir: str | os.PathLike[str],
    *,
    module: str,
    ports: Mapping[str, Any],
    product: str | None = None,
    env: str | None = None,
    version: str | None = None,
    urls: Mapping[str, Any] | None = None,
    pid: int | None = None,
    started_at: str | None = None,
) -> Path:
    """写卡（LadderStock 侧与模块侧共用同一份契约）。

    先写临时文件再 `os.replace`，避免面板读到写了一半的 json。
    """
    payload: dict[str, Any] = {
        "module": module,
        "ports": {
            "mcp": _port(ports.get("mcp")),
            "web": _port(ports.get("web")),
            "extra": [port for port in ports.get("extra") or () if _port(port)],
        },
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if product:
        payload["product"] = product
    if env:
        payload["env"] = env
    if version:
        payload["version"] = version
    if urls:
        payload["urls"] = {key: value for key, value in urls.items() if value}
    if pid is not None:
        payload["pid"] = int(pid)
    payload["started_at"] = started_at or payload["updated_at"]

    path = card_path(module_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件按 pid 分开：同一模块两个进程（如 financecoffer 的 MCP 面与 API 面）
    # 会各自写卡，共用同一个 tmp 会有一个拿到 Permission denied。
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def describe(card: Mapping[str, Any] | None) -> str:
    """给页面/日志用的一句话说明；无卡时明确说是「按旧口径推算」。"""
    if not card:
        return "无自述卡（按端口口径推算）"
    parts = []
    for face in ("mcp", "web"):
        if card.get(face):
            parts.append(f"{face} {card[face]}")
    for port in card.get("extra") or ():
        parts.append(f"extra {port}")
    return "自述卡 · " + " / ".join(parts) if parts else "自述卡"


__all__ = [
    "CARD_DIR",
    "CARD_NAME",
    "alive",
    "card_path",
    "clear_cache",
    "describe",
    "port_open",
    "read_card",
    "resolve",
    "write_card",
]
