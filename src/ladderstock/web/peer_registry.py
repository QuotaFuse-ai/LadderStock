"""peer 注册表写入（QUOT-420）：往 `config/sources.yaml` 的 `peers:` 块追加入口。

设置页的「导入模块」只做**可回滚的配置写入**，不引入任何命令执行通道：

1. 字段白名单 + 形状校验：只接受 id / product / package / default_url /
   health_path / default_port / role / repo；`command` / `script` / `path` 之类
   的未知字段直接拒绝（Pydantic 层也 `extra="forbid"`）。
2. 只做文本插入，不整文件重写：原有注释、条目顺序、缩进都保留。
3. 写前备份 `sources.yaml.bak.<时间戳>`，写后重新解析校验；任一步失败都不落盘。
4. id 必须唯一：已登记的 id 一律 409，绝不覆盖。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

#: 导入允许写入的字段（顺序即落盘顺序）。`id` 单独作为键名，不在值里。
IMPORT_FIELDS: tuple[str, ...] = (
    "product",
    "package",
    "default_url",
    "default_port",
    "health_path",
    "role",
    "repo",
)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PEERS_LINE_RE = re.compile(r"^peers:\s*(?:#.*)?$")


class PeerImportError(RuntimeError):
    """导入被拒绝；`status_code` 直接映射到 HTTP 状态码。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _text(name: str, value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        raise PeerImportError(400, f"{name} 必须是字符串")
    text = value.strip()
    if not text:
        raise PeerImportError(400, f"{name} 不能为空")
    if len(text) > limit:
        raise PeerImportError(400, f"{name} 超长（上限 {limit} 字符）")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise PeerImportError(400, f"{name} 不能包含换行或控制字符")
    return text


def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验一条导入定义并归一化；任何不合格都抛 `PeerImportError(400)`。"""
    allowed = {"id", *IMPORT_FIELDS}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise PeerImportError(
            400,
            f"不允许的字段：{', '.join(unknown)}（只接受 {', '.join(sorted(allowed))}；"
            "导入不允许携带命令或可执行路径字段）",
        )
    missing = [name for name in ("id", *IMPORT_FIELDS) if payload.get(name) in (None, "")]
    if missing:
        raise PeerImportError(400, f"缺少必填字段：{', '.join(missing)}")

    peer_id = _text("id", payload["id"], limit=41)
    if not _ID_RE.fullmatch(peer_id):
        raise PeerImportError(400, "id 只能是小写字母开头、由小写字母/数字/下划线组成（≤41 字符）")

    product = _text("product", payload["product"], limit=80)
    package = _text("package", payload["package"], limit=80)
    if not _PACKAGE_RE.fullmatch(package):
        raise PeerImportError(400, "package 只能包含字母、数字、点、下划线和连字符")

    default_url = _text("default_url", payload["default_url"], limit=300)
    parsed = urlsplit(default_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PeerImportError(400, "default_url 必须是 http(s) 且带主机名")
    if "@" in parsed.netloc:
        raise PeerImportError(400, "default_url 不允许携带用户名/密码")

    health_path = _text("health_path", payload["health_path"], limit=120)
    if not health_path.startswith("/") or any(ch.isspace() for ch in health_path):
        raise PeerImportError(400, "health_path 必须以 / 开头且不含空白")

    port = payload["default_port"]
    if isinstance(port, bool) or not isinstance(port, int):
        if isinstance(port, str) and port.strip().isdigit():
            port = int(port.strip())
        else:
            raise PeerImportError(400, "default_port 必须是 1-65535 的整数")
    if not 1 <= port <= 65535:
        raise PeerImportError(400, "default_port 必须是 1-65535 的整数")

    role = _text("role", payload["role"], limit=200)
    repo = _text("repo", payload["repo"], limit=120)
    if not _REPO_RE.fullmatch(repo):
        raise PeerImportError(400, "repo 必须是 owner/name 形式")

    return {
        "id": peer_id,
        "product": product,
        "package": package,
        "default_url": default_url,
        "default_port": port,
        "health_path": health_path,
        "role": role,
        "repo": repo,
    }


def _scalar(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    # JSON 的字符串字面量是合法的 YAML 双引号标量：中文、引号、冒号都不用手写转义规则
    return json.dumps(str(value), ensure_ascii=False)


def _render_entry(entry: Mapping[str, Any], timestamp: str) -> list[str]:
    lines = [f"  # 设置页导入（{timestamp}）", f"  {entry['id']}:"]
    lines.extend(f"    {name}: {_scalar(entry[name])}" for name in IMPORT_FIELDS)
    return lines


def _insert_peer(text: str, entry: Mapping[str, Any], timestamp: str) -> str:
    """在 `peers:` 块末尾插入一条；保留注释、顺序与换行风格。"""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    peers_index = next(
        (index for index, line in enumerate(lines) if _PEERS_LINE_RE.match(line)), None
    )
    if peers_index is None:
        raise PeerImportError(409, "config/sources.yaml 没有 peers: 块，拒绝导入")

    block_end = len(lines)
    for index in range(peers_index + 1, len(lines)):
        line = lines[index]
        if line and not line[0].isspace():
            block_end = index
            break

    last_content = -1
    for index in range(peers_index + 1, block_end):
        line = lines[index]
        if line[:1].isspace() and line.strip():
            last_content = index
    insert_at = last_content + 1 if last_content >= 0 else peers_index + 1

    merged = lines[:insert_at] + _render_entry(entry, timestamp) + lines[insert_at:]
    body = newline.join(merged)
    return body + newline if text.endswith(("\n", "\r")) or not text else body


def _verify(new_text: str, previous: Mapping[str, Any], entry: Mapping[str, Any]) -> None:
    document = yaml.safe_load(new_text)
    peers = document.get("peers") if isinstance(document, dict) else None
    if not isinstance(peers, Mapping):
        raise PeerImportError(500, "写入后 peers: 块解析失败，已放弃写入")
    if list(peers) != [*previous, entry["id"]]:
        raise PeerImportError(500, "写入后注册表顺序与预期不符，已放弃写入")
    for key, value in previous.items():
        if peers.get(key) != value:
            raise PeerImportError(500, f"写入会改动已有条目 {key}，已放弃写入")
    written = peers.get(entry["id"])
    expected = {name: entry[name] for name in IMPORT_FIELDS}
    if written != expected:
        raise PeerImportError(500, "写入后的新条目与提交内容不一致，已放弃写入")


def _backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def import_peer(path: Path, payload: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    """校验并追加一条 peer；返回 `(归一化后的条目, 备份路径)`。"""
    entry = validate_payload(payload)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PeerImportError(500, f"读不到 {path.name}：{exc}") from None
    try:
        document = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PeerImportError(409, f"{path.name} 不是合法 YAML，先修复再导入（{exc}）") from None
    peers = document.get("peers") if isinstance(document, dict) else None
    if not isinstance(peers, Mapping) or not peers:
        raise PeerImportError(409, "config/sources.yaml 的 peers: 块缺失或为空，拒绝导入")
    if entry["id"] in peers:
        raise PeerImportError(409, f"peer id 已存在：{entry['id']}（导入不会覆盖已有条目）")

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    new_text = _insert_peer(text, entry, timestamp)
    _verify(new_text, peers, entry)

    backup = _backup(path)
    temp = path.with_name(f"{path.name}.tmp")
    try:
        temp.write_text(new_text, encoding="utf-8", newline="")
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise PeerImportError(500, f"写入 {path.name} 失败：{exc}") from None
    return entry, backup
