"""结构化输出：Schema、解析与文本补偿（M3 §6.3）。

三段降级的第 1、2 级都在这里：

1. **结构化**：模型输出 JSON 对象，按段落 Schema 校验（只允许叙述字段）；
2. **文本补偿**：JSON 不合法 / 不合 Schema 时，从同一段返回文本里提取
   叙述段落，重组为合法 payload（`mode="parsed"`）；
3. **纯文本**（第 3 级，见 `ai/orchestrator.py`）：显式不使用 JSON 模式，
   结果标注 `degraded=True`。

Schema 刻意不含任何指标键：AI 写数字即越界（DEC-006 闸门 5）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import SECTIONS
from ladderstock.core.boundary import AI_WRITABLE

SECTION_TITLES: dict[str, str] = {
    "sentiment": "情绪温度与晋级率",
    "capital": "资金流向",
    "theme": "题材轮动",
    "dragon_tiger": "龙虎榜与席位",
    "leader": "龙头与梯队",
}

# 段落输出 Schema：必需 narrative，可选 highlights / risks / evidence
SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["narrative"],
    "additionalProperties": False,
    "properties": {
        "narrative": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "highlights": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}

# config/ai.yaml 的 output_schema 名称 → 实际 Schema
SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    "SentimentSection": SECTION_SCHEMA,
    "CapitalSection": SECTION_SCHEMA,
    "ThemeSection": SECTION_SCHEMA,
    "DragonTigerSection": SECTION_SCHEMA,
    "LeaderSection": SECTION_SCHEMA,
}

# AI 可以写、且这里允许模型直接产出的字段（model/degraded 由编排层补）
MODEL_WRITABLE: frozenset[str] = AI_WRITABLE - {"model", "degraded"}

NARRATIVE_FIELDS: tuple[str, ...] = ("narrative", "highlights", "risks", "evidence")

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_PUNCT_STRIP = " \t-*#>•\u3000"


class StructuredOutputError(ValueError):
    """模型输出不是合法 JSON，或不符合段落 Schema。"""


def schema_for(section: str, schema_name: str | None = None) -> dict[str, Any]:
    """取段落 Schema；`schema_name` 来自 ai.yaml，未知名称回落到通用 Schema。"""
    if schema_name and schema_name in SCHEMA_BY_NAME:
        return SCHEMA_BY_NAME[schema_name]
    if section not in SECTIONS:
        raise StructuredOutputError(f"未知段落：{section}")
    return SECTION_SCHEMA


def strip_fences(text: str) -> str:
    """去掉 ```json 围栏（模型很爱加），保留内部正文。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE.sub("", cleaned).strip()
    return cleaned


def parse_json(text: str) -> dict[str, Any]:
    """解析模型输出的 JSON 对象；允许前后有少量解释文字。"""
    cleaned = strip_fences(text)
    if not cleaned:
        raise StructuredOutputError("模型返回空内容")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise StructuredOutputError("模型输出不是合法 JSON 对象") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(f"模型输出不是合法 JSON：{exc.msg}") from None
    if not isinstance(parsed, dict):
        raise StructuredOutputError("模型输出的 JSON 顶层必须是对象")
    return parsed


def validate_payload(section: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """过 Schema + 字段白名单；返回清洗后的 payload。"""
    from jsonschema import Draft202012Validator

    illegal = set(payload) - MODEL_WRITABLE
    if illegal:
        raise StructuredOutputError(
            "AI 输出含非叙述字段：" + ", ".join(sorted(illegal)) + "（数字必须由程序计算）"
        )
    schema = schema_for(section)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(dict(payload)),
        key=lambda err: list(err.path),
    )
    if errors:
        raise StructuredOutputError(f"不符合 {section} Schema：{errors[0].message}")
    return _clean(dict(payload))


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {"narrative": _strings(payload.get("narrative"))}
    for key in ("highlights", "risks", "evidence"):
        values = _strings(payload.get(key))
        if values:
            cleaned[key] = values
    if not cleaned["narrative"]:
        raise StructuredOutputError("narrative 不能为空")
    return cleaned


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def compensate(text: str) -> dict[str, Any] | None:
    """第 2 级降级：从失败文本里抢救叙述。

    先尝试截取其中像 JSON 的片段；不行就把正文按空行/换行切成段落。
    纯空白或明显过短的返回视为不可补偿（`None`），交给纯文本降级。
    """
    cleaned = strip_fences(text or "")
    if not cleaned.strip():
        return None
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            narrative = _strings(parsed.get("narrative")) or _strings(parsed.get("text"))
            extra = {
                key: _strings(parsed.get(key))
                for key in ("highlights", "risks", "evidence")
            }
            if narrative:
                payload = {"narrative": narrative}
                payload.update({k: v for k, v in extra.items() if v})
                return payload
    paragraphs = [
        line.strip(_PUNCT_STRIP)
        for line in re.split(r"\n\s*\n|\n", cleaned)
        if len(line.strip(_PUNCT_STRIP)) >= 8
    ]
    if not paragraphs:
        return None
    return {"narrative": paragraphs[:8]}


def plain_payload(text: str) -> dict[str, Any] | None:
    """第 3 级降级：整段文本原样作为叙述（调用方标记 degraded）。"""
    cleaned = strip_fences(text or "").strip()
    if not cleaned:
        return None
    return {"narrative": [cleaned]}


def load_prompt(path: str | Path) -> str:
    """读 prompt 模板；缺失时给出最小可用的内置指令（不让整条链路崩掉）。"""
    target = Path(path)
    if not target.is_absolute():
        target = _project_root() / target
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError:
        return "你是 A 股短线复盘助手，只写叙述，不写任何指标数字。"


def build_messages(
    section: str,
    day: str,
    metrics: Mapping[str, Any],
    prompt_text: str,
) -> tuple[str, str]:
    """拼装 system / user 消息；指标以只读快照形式提供。"""
    title = SECTION_TITLES.get(section, section)
    system = (
        f"{prompt_text}\n\n"
        "硬约束：\n"
        "1. 只输出一个 JSON 对象，字段仅限 narrative / highlights / risks / evidence；\n"
        "2. 不得输出任何指标数字键（temperature、promotion_rate 等），数字由程序计算；\n"
        "3. 不得编造指标数值；引用数字时必须与给定快照一致；\n"
        "4. 中文输出，narrative 为字符串数组。"
    )
    user = (
        f"交易日：{day}\n"
        f"段落：{title}\n"
        "程序指标快照（只读）：\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2, default=str)}\n\n"
        "请基于快照写复盘叙述，输出 JSON。"
    )
    return system, user


def _project_root() -> Path:
    from ladderstock.cli.paths import project_root

    return project_root()


__all__ = [
    "MODEL_WRITABLE",
    "SECTION_SCHEMA",
    "SECTION_TITLES",
    "StructuredOutputError",
    "build_messages",
    "compensate",
    "load_prompt",
    "parse_json",
    "plain_payload",
    "schema_for",
    "strip_fences",
    "validate_payload",
]
