"""盯盘状态文件 `data/watch/status.json` 的读取判据——**全仓唯一一份**。

`ldst watch start` 把一轮扫描的结果落在这个文件里（`running / mode / detail /
event_count / recent_events / ...`）。CLI 的 `ldst watch status|stop` 与 MCP 的
`ladder_get_watch` 都从这里读，不在各自入口里再抄一份解析逻辑（DEC-008 / DEC-010
的教训：同一判据写两份，下一次就一定会漂移）。

本模块只做**读取与判型**：不写文件、不联网、不采集（DEC-006）。四种结果：

| state | 含义 | 调用方该怎么回 |
|---|---|---|
| `ok` | 文件存在且字段齐、类型对 | 直接用 `document` |
| `missing` | 文件不存在 | 如实说「尚未执行 ldst watch start」，**不伪造信号** |
| `corrupt` | 读不了 / 空文件 / 非 JSON / 顶层不是对象 | 说清路径与错误类型，不抛裸异常 |
| `unsupported` | JSON 是对象，但关键字段缺失或类型不对 | 同 missing，并说明哪个字段不认识 |

`corrupt` 与 `unsupported` 都**不代表没有信号**——它们代表状态文件坏了，调用方
必须把它当作「不可用」而不是「无事件」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUS_FILENAME = "status.json"

#: 状态文件里保留的近期事件条数；CLI 落盘与 MCP 读取共用同一上限
STATUS_RECENT_EVENTS = 50

STATE_OK = "ok"
STATE_MISSING = "missing"
STATE_CORRUPT = "corrupt"
STATE_UNSUPPORTED = "unsupported"

STATES: tuple[str, ...] = (STATE_OK, STATE_MISSING, STATE_CORRUPT, STATE_UNSUPPORTED)


@dataclass(frozen=True, slots=True)
class StatusRead:
    """一次状态文件读取的结果：`state` 决定调用方怎么回，`document` 只在成功时可信。"""

    state: str
    path: Path
    document: dict[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == STATE_OK


def watch_dir(data_root: Path) -> Path:
    """盯盘产物目录：`<data_root>/watch`。"""
    return Path(data_root) / "watch"


def status_path(data_root: Path) -> Path:
    """状态文件：`<data_root>/watch/status.json`。"""
    return watch_dir(data_root) / STATUS_FILENAME


def _type_name(value: Any) -> str:
    return type(value).__name__


def schema_problems(document: Mapping[str, Any]) -> list[str]:
    """检查 `watch start` 写入的关键字段；返回人话问题列表（空列表 = 认识这份 schema）。"""
    problems: list[str] = []

    running = document.get("running")
    if not isinstance(running, bool):
        problems.append(f"running 需要布尔值，收到 {_type_name(running)}")

    mode = document.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        problems.append(f"mode 需要非空字符串，收到 {_type_name(mode)}")

    count = document.get("event_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        problems.append(f"event_count 需要非负整数，收到 {_type_name(count)}")

    if not isinstance(document.get("recent_events"), list):
        problems.append(
            f"recent_events 需要数组，收到 {_type_name(document.get('recent_events'))}"
        )

    return problems


def read_status_document(path: Path) -> StatusRead:
    """读状态文件并按 `ok / missing / corrupt / unsupported` 判型，**不抛异常**。"""
    target = Path(path)

    try:
        exists = target.is_file()
    except OSError as exc:  # 权限 / 坏链接等：当作坏文件，别让调用方炸
        return StatusRead(
            STATE_CORRUPT, target, error=f"无法判断文件是否存在（{_type_name(exc)}）：{exc}"
        )
    if not exists:
        return StatusRead(STATE_MISSING, target)

    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return StatusRead(
            STATE_CORRUPT, target, error=f"读取失败（{_type_name(exc)}）：{exc}"
        )

    if not text.strip():
        return StatusRead(STATE_CORRUPT, target, error="文件为空（0 字节），没有可读的状态")

    try:
        raw: Any = json.loads(text)
    except ValueError as exc:
        return StatusRead(
            STATE_CORRUPT, target, error=f"JSON 解析失败（{_type_name(exc)}）：{exc}"
        )

    if not isinstance(raw, dict):
        return StatusRead(
            STATE_CORRUPT, target, error=f"顶层需要 JSON 对象，收到 {_type_name(raw)}"
        )

    problems = schema_problems(raw)
    if problems:
        return StatusRead(STATE_UNSUPPORTED, target, document=raw, error="；".join(problems))

    return StatusRead(STATE_OK, target, document=raw)


def recent_events(
    document: Mapping[str, Any], *, limit: int = STATUS_RECENT_EVENTS
) -> list[Any]:
    """取状态文件里的近期事件，只保留最后 `limit` 条（与 CLI 落盘口径一致）。"""
    events = document.get("recent_events")
    if not isinstance(events, list) or limit <= 0:
        return []
    return list(events[-limit:])


def status_detail(document: Mapping[str, Any]) -> str:
    """一句话摘要：CLI 与 MCP 共用同一口径，不再各写一份人话。"""
    explicit = document.get("detail")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    parts: list[str] = ["运行中" if document.get("running") is True else "已停止"]
    mode = document.get("mode")
    if isinstance(mode, str) and mode.strip():
        parts.append(f"模式 {mode.strip()}")
    day = document.get("date")
    if isinstance(day, str) and day.strip():
        parts.append(f"交易日 {day.strip()}")
    last_run = document.get("last_run_at")
    if isinstance(last_run, str) and last_run.strip():
        parts.append(f"最近一次扫描 {last_run.strip()}")

    bars = document.get("bars")
    if isinstance(bars, int) and not isinstance(bars, bool):
        parts.append(f"分钟样本 {bars} 根")
    count = document.get("event_count")
    if isinstance(count, int) and not isinstance(count, bool):
        parts.append(f"累计事件 {count} 条")

    return "，".join(parts)


__all__ = [
    "STATES",
    "STATUS_FILENAME",
    "STATUS_RECENT_EVENTS",
    "STATE_CORRUPT",
    "STATE_MISSING",
    "STATE_OK",
    "STATE_UNSUPPORTED",
    "StatusRead",
    "read_status_document",
    "recent_events",
    "schema_problems",
    "status_detail",
    "status_path",
    "watch_dir",
]
