"""统一输出层：结构化结果写 stdout，日志/错误写 stderr。

约定（见 docs/CLI.md §F）：
- 结构化结果 -> stdout；日志与错误 -> stderr
- 三档格式：json（默认用于管道）、table（交互）、csv
- **全局 `--output`**：31 条基线命令统一用 `OutputOption` 声明，未显式指定时
  按 `resolve_format()` 取默认值——交互终端 table、管道/重定向 json
- Windows 控制台强制 UTF-8，避免中文变成乱码
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import unicodedata
from typing import Annotated, Any, Literal

import typer

OutputFormat = Literal["json", "table", "csv"]

# docs/CLI.md §F：默认 table（交互）/ json（管道）
TTY_FORMAT: OutputFormat = "table"
PIPE_FORMAT: OutputFormat = "json"

# 全局输出选项：各命令只写 `output: OutputOption = None`，默认值在 emit 时解析，
# 避免在 import 期就把「当前是不是终端」烤进函数签名。
OutputOption = Annotated[
    OutputFormat | None,
    typer.Option("--output", help="输出格式：json / table / csv（默认交互 table、管道 json）"),
]


def default_format() -> OutputFormat:
    """未传 `--output` 时的默认格式：交互终端 table，其余（管道/重定向）json。"""
    stream = sys.stdout
    try:
        interactive = bool(stream.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - 非常规流
        interactive = False
    return TTY_FORMAT if interactive else PIPE_FORMAT


def resolve_format(fmt: OutputFormat | None) -> OutputFormat:
    """把「用户没传」归一成实际格式；显式传入的值原样返回。"""
    return fmt if fmt is not None else default_format()


def _force_utf8() -> None:
    """Windows 下 Python 默认用 locale 编码（cp936）写管道，中文会乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - 非常规流
            pass


_force_utf8()


# --------------------------------------------------------------------------- #
# 单元格格式化
# --------------------------------------------------------------------------- #

def _text(value: Any) -> str:
    """把任意值转成一行的可读文本；嵌套结构用紧凑 JSON 表达。"""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict)):
        if not value:
            return "-"
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _disp_len(text: str) -> int:
    """显示宽度：东亚全角字符占两列，否则表格对不齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_len(text))


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and isinstance(value[0], dict)


def _headers(records: list[dict[str, Any]]) -> list[str]:
    """按首次出现顺序取并集，避免 dict 无序导致列跳动。"""
    seen: dict[str, None] = {}
    for row in records:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _grid(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [[_text(v) for v in row] for row in rows]
    widths = [_disp_len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _disp_len(cell))
    buf = io.StringIO()
    buf.write("  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)).rstrip() + "\n")
    buf.write("  ".join("-" * w for w in widths) + "\n")
    for row in cells:
        buf.write("  ".join(_pad(c, widths[i]) for i, c in enumerate(row)).rstrip() + "\n")
    return buf.getvalue()


def _records(records: list[dict[str, Any]]) -> list[list[Any]]:
    headers = _headers(records)
    return [[row.get(h) for h in headers] for row in records]


# --------------------------------------------------------------------------- #
# 凭据脱敏（docs/CLI.md §F：日志与报错不得打印凭据原文）
# --------------------------------------------------------------------------- #

#: 形如 `sk-xxxx` / `ghp_xxxx` / `AKIAxxxx` 的密钥
_KEY_VALUE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{16,}|akia[0-9a-z]{12,})\b"
)
#: `Authorization: Bearer xxxx`
_BEARER = re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+")
#: `api_key=xxx` / `token: xxx` / `password=xxx`
_ASSIGN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|pwd|authorization|cookie)\s*[=:]\s*)([^\s,;&\"']+)"
)
#: URL 查询串里的凭据
_URL_QUERY = re.compile(
    r"(?i)([?&](?:key|api[_-]?key|token|access[_-]?token|secret|password)=)[^&\s]+"
)
#: 环境变量名里带这些词的，其值一律打码
_SECRET_ENV_NAME = re.compile(r"(?i)(?:key|token|secret|password|passwd|credential)")


def _env_secret_values() -> list[str]:
    """当前进程环境里「名字像凭据」且长度足够的变量值（用于精确替换）。"""
    values: list[str] = []
    for name, value in os.environ.items():
        if len(value) >= 8 and _SECRET_ENV_NAME.search(name):
            values.append(value)
    return values


def redact(text: str) -> str:
    """把文本里的凭据/Token 打码，保留原有排版（不折叠换行、不截断）。

    只处理「会进 stderr / 日志」的文本；结构化 stdout 数据原样输出，
    避免把用户自己的复盘内容误伤成 `***`。
    """
    if not text:
        return text
    cleaned = str(text)
    for value in _env_secret_values():
        cleaned = cleaned.replace(value, "***")
    cleaned = _URL_QUERY.sub(r"\1***", cleaned)
    cleaned = _ASSIGN.sub(r"\1***", cleaned)
    cleaned = _BEARER.sub(r"\1***", cleaned)
    cleaned = _KEY_VALUE.sub("***", cleaned)
    return cleaned


# --------------------------------------------------------------------------- #
# 三种格式
# --------------------------------------------------------------------------- #

def _as_table(data: Any) -> str:
    # 结果集：列表套记录
    if _is_record_list(data):
        return _grid(_headers(data), _records(data))

    if isinstance(data, dict):
        # 摘要 + 明细：标量走 key/value，记录列表单独成表（如 doctor）
        blocks = [(k, v) for k, v in data.items() if _is_record_list(v)]
        scalars = [(k, v) for k, v in data.items() if not _is_record_list(v)]
        if blocks:
            buf = io.StringIO()
            if scalars:
                buf.write(_grid(["field", "value"], [[k, v] for k, v in scalars]))
            for key, rows in blocks:
                buf.write(f"\n{key}:\n")
                buf.write(_grid(_headers(rows), _records(rows)))
            return buf.getvalue()
        return _grid(["field", "value"], [[k, v] for k, v in data.items()])

    if isinstance(data, list):
        return _grid(["value"], [[item] for item in data])

    return _grid(["value"], [[data]])


def _as_csv(data: Any) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if _is_record_list(data):
        headers = _headers(data)
        writer.writerow(headers)
        for row in _records(data):
            writer.writerow([_text(v) for v in row])
    elif isinstance(data, dict):
        writer.writerow(["field", "value"])
        for key, value in data.items():
            writer.writerow([key, _text(value)])
    elif isinstance(data, list):
        writer.writerow(["value"])
        for item in data:
            writer.writerow([_text(item)])
    else:
        writer.writerow(["value"])
        writer.writerow([_text(data)])
    return buf.getvalue()


def emit(data: Any, fmt: OutputFormat | None = None) -> None:
    """把结果写到 stdout；`fmt` 为空时按 `resolve_format()` 取默认值。"""
    fmt = resolve_format(fmt)
    if fmt == "json":
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
        return
    sys.stdout.write(_as_table(data) if fmt == "table" else _as_csv(data))


def error(message: str) -> None:
    """错误写 stderr；任何凭据/Token 在落盘前先打码（CLI.md §F）。"""
    sys.stderr.write(f"ldst: {redact(message)}\n")
    sys.stderr.flush()
