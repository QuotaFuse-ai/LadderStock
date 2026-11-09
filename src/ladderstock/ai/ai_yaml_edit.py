"""`config/ai.yaml` 的 `modules:` 段原地编辑（QUOT-498）。

只在文本层面改 `ai.modules.*`：注释、空行、其余段的顺序与缩进原样保留。
落盘前用完整解析器重解析校验，通过后才「临时文件 + os.replace」原子替换；
任何一步不对劲都抛 `BoundaryViolation` 并放弃写入（fail closed）。

认得的写法就是本仓 `ai.yaml` 的写法（2 或 4 空格缩进、`key: value`、
`roles.<role>: {model: x}` 的 flow 映射或块映射）。条目的正文整段重渲染，
条目之间和条目上方的注释保持原位；条目整条删除（覆盖清空）时，贴着它的
注释行一起删。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ladderstock.core.boundary import BoundaryViolation

#: 条目字段的固定输出顺序
MODULE_ORDER: tuple[str, ...] = ("model", "fallback_chain", "memory", "roles")
#: `memory` / `roles.<role>` 下的字段顺序
MEMORY_ORDER: tuple[str, ...] = ("agent_id", "kb_scope")
ROLE_ORDER: tuple[str, ...] = ("model",)

_AI_KEY = "ai"
_MODULES_KEY = "modules"


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def _scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise BoundaryViolation(f"ai.yaml modules 只接受字符串 / 字符串数组，收到 {value!r}")
    if isinstance(value, (int, float)):
        value = str(value)
    # JSON 字符串字面量就是合法的 YAML 双引号标量：中文、冒号、# 都不用自己写转义
    return json.dumps(str(value), ensure_ascii=False)


def _inline_map(value: Mapping[str, Any], order: tuple[str, ...]) -> str:
    parts = [
        f"{name}: {_scalar(value[name])}"
        for name in order
        if value.get(name) not in (None, "", (), [])
    ]
    return "{" + ", ".join(parts) + "}"


def _render_module_body(item: Mapping[str, Any], indent: str) -> list[str]:
    """`model` / `fallback_chain` / `memory` / `roles` 各自的落行方式。"""
    body = indent + "  "
    sub = indent + "    "
    out: list[str] = []
    for name in MODULE_ORDER:
        value = item.get(name)
        if value in (None, "", (), []):
            continue
        if name == "fallback_chain":
            out.append(f"{body}fallback_chain: [{', '.join(_scalar(v) for v in value)}]")
        elif name == "memory":
            out.append(f"{body}memory:")
            out.extend(
                f"{sub}{key}: {_scalar(value[key])}"
                for key in MEMORY_ORDER
                if value.get(key) not in (None, "")
            )
        elif name == "roles":
            out.append(f"{body}roles:")
            for role, role_item in value.items():
                out.append(f"{sub}{_scalar(role)}: {_inline_map(role_item or {}, ROLE_ORDER)}")
        else:
            out.append(f"{body}{name}: {_scalar(value)}")
    return out


def render_modules(modules: Mapping[str, Mapping[str, Any]], indent: str = "  ") -> str:
    """把 `modules` 映射渲染成 YAML 文本（`indent` 是 `modules:` 自己的缩进）。"""
    if not modules:
        return ""
    lines: list[str] = [f"{indent}{_MODULES_KEY}:"]
    for module, item in modules.items():
        lines.append(f"{indent}  {_scalar(module)}:")
        lines.extend(_render_module_body(item, f"{indent}  "))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 文本模型：给每行算出它贡献的 YAML 键路径
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Line:
    text: str
    #: `ai.modules.senate.roles.bull` 这样的键路径；注释 / 空行 / 列表项为空
    keys: tuple[str, ...] = ()


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        try:
            return str(json.loads(raw)) if raw[0] == '"' else raw[1:-1].replace("''", "'")
        except ValueError:
            return raw[1:-1]
    return raw


def _key_of(content: str) -> str | None:
    """`key: rest` / `key:` → key；列表项、纯量续行 → None。"""
    stripped = content.strip()
    if not stripped or stripped[0] in "#-|>":
        return None
    index = content.find(":")
    if index <= 0 or content[index - 1] in " \t":
        return None
    return _unquote(content[:index].strip())


def _indent_of(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _annotate(text: str) -> list[_Line]:
    lines: list[_Line] = []
    stack: list[tuple[int, str]] = []
    list_owner: tuple[int, tuple[str, ...]] | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(_Line(raw))
            continue
        indent = _indent_of(raw)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stripped.startswith("-"):
            keys = list_owner[1] if list_owner and indent == list_owner[0] else ()
            lines.append(_Line(raw, keys))
            continue
        list_owner = None
        key = _key_of(raw)
        if key is None:
            lines.append(_Line(raw))
            continue
        keys = tuple(name for _, name in stack) + (key,)
        lines.append(_Line(raw, keys))
        stack.append((indent, key))
        list_owner = (indent + 1, keys)
    return lines


def _find(lines: list[_Line], prefix: tuple[str, ...]) -> int | None:
    return next(
        (index for index, line in enumerate(lines) if line.keys[: len(prefix)] == prefix), None
    )


def _end_of(lines: list[_Line], start: int, prefix: tuple[str, ...]) -> int:
    """`prefix` 声明的区段末行之后（后续行不再是它的子键 / 子列表项）。"""
    last = start
    for index in range(start + 1, len(lines)):
        keys = lines[index].keys
        if not keys:
            continue
        if keys[: len(prefix)] != prefix:
            break
        last = index
    return last + 1


def _ai_block_end(lines: list[_Line], start: int) -> int:
    indent = _indent_of(lines[start].text)
    last = start
    for index in range(start + 1, len(lines)):
        text = lines[index].text
        if not text.strip():
            continue
        if _indent_of(text) <= indent:
            break
        last = index
    return last + 1


def _parse_region(text: str, first: int, end: int) -> Any:
    snippet = "\n".join(text.splitlines()[first:end])
    document = yaml.safe_load(snippet)
    if not isinstance(document, dict):
        return None
    return document.get(_MODULES_KEY)


def _rejoin(original: str, lines: list[str]) -> str:
    newline = "\r\n" if "\r\n" in original else "\n"
    body = newline.join(lines)
    return body + newline if original.endswith(("\n", "\r\n")) else body


# --------------------------------------------------------------------------- #
# 条目合并
# --------------------------------------------------------------------------- #


def _merge_module(current: Any, override: Mapping[str, Any]) -> dict[str, Any]:
    """`override` 覆盖 `current` 的同名字段；空值 = 删掉该字段。

    `roles` 逐角色增改：角色 `model` 为空串 = 删掉这个角色。
    """
    merged: dict[str, Any] = dict(current or {})
    for name in ("model", "fallback_chain", "memory"):
        if name not in override:
            continue
        if override[name] in (None, "", (), []):
            merged.pop(name, None)
        else:
            merged[name] = override[name]
    if "roles" in override:
        roles = dict(merged.get("roles") or {})
        for role, role_body in (override.get("roles") or {}).items():
            body = dict(role_body or {})
            if not str(body.get("model") or "").strip():
                roles.pop(role, None)
            else:
                roles[role] = body
        if roles:
            merged["roles"] = roles
        else:
            merged.pop("roles", None)
    return merged


# --------------------------------------------------------------------------- #
# 编辑入口
# --------------------------------------------------------------------------- #


def edit_module(text: str, module: str, override: Mapping[str, Any]) -> str:
    """把 `modules.<module>` 合并成 `override`；空 `override` = 整条删除。

    只走文本编辑，不做校验——调用方必须对结果再跑一次完整解析（见 `write_ai_yaml`）。
    """
    lines = text.splitlines()
    annotated = _annotate(text)
    module_prefix = (
        (_MODULES_KEY, module)
        if _locate_modules(annotated) is None
        else (
            _AI_KEY,
            _MODULES_KEY,
            module,
        )
    )
    modules_prefix = module_prefix[: len(module_prefix) - 1]
    module_start = _find(annotated, module_prefix)

    if module_start is None:
        if not override:
            return text
        return _insert_module(text, lines, annotated, module, override)

    if not override:
        start = module_start
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        end = _end_of(annotated, module_start, module_prefix)
        del lines[start:end]
        return _rejoin(text, lines)

    current = _current_module(text, annotated, module_start, modules_prefix, module)
    desired = _merge_module(current, override)
    indent = " " * _indent_of(lines[module_start])
    rendered = [f"{indent}{_scalar(module)}:"] + _render_module_body(desired, indent)
    end = _end_of(annotated, module_start, module_prefix)
    lines[module_start:end] = rendered
    return _rejoin(text, lines)


def _locate_modules(annotated: list[_Line]) -> int | None:
    return _find(annotated, (_AI_KEY, _MODULES_KEY))


def _current_module(
    text: str,
    annotated: list[_Line],
    module_start: int,
    modules_prefix: tuple[str, ...],
    module: str,
) -> Any:
    if modules_prefix == (_AI_KEY, _MODULES_KEY):
        modules_index = _find(annotated, modules_prefix)
        assert modules_index is not None
        end = _end_of(annotated, modules_index, modules_prefix)
        parsed = _parse_region(text, modules_index, end)
        if not isinstance(parsed, dict):
            raise BoundaryViolation("ai.yaml: modules: 块解析失败，请手工修复后再保存")
        return parsed.get(module) or {}

    # `modules:` 不在 `ai:` 块里：只截这一个条目交给 YAML 解析，不整段猜测
    end = _end_of(annotated, module_start, (_MODULES_KEY, module))
    snippet = "\n".join(f"{line.text}" for line in annotated[module_start:end])
    parsed = yaml.safe_load(snippet)
    if not isinstance(parsed, dict) or not isinstance(parsed.get(module), dict | type(None)):
        raise BoundaryViolation(f"ai.yaml: modules.{module} 解析失败，请手工修复后再保存")
    return parsed.get(module) or {}


def _insert_module(
    text: str,
    lines: list[str],
    annotated: list[_Line],
    module: str,
    override: Mapping[str, Any],
) -> str:
    item = {name: value for name, value in override.items() if value not in (None, "", (), [])}
    roles = item.get("roles")
    if roles:
        item["roles"] = {role: body for role, body in roles.items() if (body or {}).get("model")}
        if not item["roles"]:
            item.pop("roles")

    ai_index = _find(annotated, (_AI_KEY,))
    if ai_index is None:
        if text.strip():
            raise BoundaryViolation("ai.yaml 没有 ai: 块，拒绝写入子模块配置")
        return render_modules({module: item})

    modules_index = _locate_modules(annotated)
    if modules_index is not None:
        indent = " " * _indent_of(lines[modules_index])
    else:
        # 沿用文件里同级的已有字段缩进（4 空格文件插 4 空格），没有才退回 +2
        ai_indent = _indent_of(lines[ai_index])
        siblings = [
            _indent_of(line.text)
            for line in annotated[ai_index + 1 :]
            if line.keys[:1] == (_AI_KEY,) and _indent_of(line.text) > ai_indent
        ]
        indent = " " * (siblings[0] if siblings else ai_indent + 2)

    rendered = [f"{indent}  {_scalar(module)}:"] + _render_module_body(item, f"{indent}  ")
    if modules_index is not None:
        insert_at = _end_of(annotated, modules_index, (_AI_KEY, _MODULES_KEY))
        # 未声明的模块跟在已有条目之后，最贴近手写习惯
        return _rejoin(text, lines[:insert_at] + rendered + lines[insert_at:])

    insert_at = _ai_block_end(annotated, ai_index)
    # 块末的注释 / 空行属于「收尾」，插在它们之前，新块才会紧贴上一个字段
    while insert_at - 1 > ai_index and not lines[insert_at - 1].strip():
        insert_at -= 1
    block = [""] + [f"{indent}{_MODULES_KEY}:"] + rendered
    if insert_at < len(lines) and not lines[insert_at].strip() and block[-1] == "":
        block.pop()
    return _rejoin(text, lines[:insert_at] + block + lines[insert_at:])


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WriteResult:
    path: Path
    backup: Path


def _backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def write_ai_yaml(path: Path, new_text: str, validate: Callable[[Path], Any]) -> WriteResult:
    """候选文本先落到临时目录、用同一个解析器读回校验，通过后才替换正式文件。

    校验不过：删掉临时文件、抛 `BoundaryViolation`，`ai.yaml` 一个字节都不动。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = path.with_name(f".{path.name}.tmp")
    temp = path.with_name(f".{path.name}.replace")
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        candidate = tmp_dir / path.name
        candidate.write_text(new_text, encoding="utf-8", newline="")
        try:
            validate(candidate)
        except BoundaryViolation as exc:
            raise BoundaryViolation(f"写入被拒绝（校验未通过，ai.yaml 未改动）：{exc}") from None
        backup = _backup(path)
        temp.write_text(new_text, encoding="utf-8", newline="")
        os.replace(temp, path)
    except OSError as exc:
        raise BoundaryViolation(f"写入 {path.name} 失败：{exc}") from None
    finally:
        temp.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return WriteResult(path=path, backup=backup)


__all__ = [
    "MODULE_ORDER",
    "ROLE_ORDER",
    "WriteResult",
    "edit_module",
    "render_modules",
    "write_ai_yaml",
]
