"""计算指纹：``calc_id = "c_" + sha256(op + CALC_VERSION + 规范化参数 + 数据指纹)[:8]``。

规范化参数要求：
- 键排序（映射按 key 排序后输出）；
- 浮点用统一 repr（``float.hex()``，保留位级差异）；
- ``None`` 与缺省区分：传入的映射里出现的键即视为「已给出」，
  缺省的键不会出现在规范串中，因此 ``{"a": None}`` 与 ``{}`` 得到不同 id。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from ladderstock.calc import CALC_VERSION

_SEP: Final = "\x1f"


def _canonical(node: Any) -> Any:
    if node is None:
        return ["none"]
    if isinstance(node, bool):
        return ["bool", node]
    if isinstance(node, int):
        return ["int", str(node)]
    if isinstance(node, float):
        return ["float", float.hex(node)]
    if isinstance(node, str):
        return ["str", node]
    if isinstance(node, Mapping):
        items = sorted(node.items(), key=lambda kv: _sort_key(kv[0]))
        return ["map", [[str(k), _canonical(v)] for k, v in items]]
    if isinstance(node, Sequence) and not isinstance(node, str | bytes | bytearray):
        return ["seq", [_canonical(v) for v in node]]
    raise TypeError(f"无法规范化的参数类型: {type(node).__name__}")


def _sort_key(key: Any) -> str:
    return json.dumps(key, default=str, sort_keys=True, ensure_ascii=False)


def canonical_params(params: Mapping[str, Any] | None) -> str:
    """把参数映射规范化为稳定字符串（键排序、浮点十六进制、区分 None 与缺省）。"""
    return json.dumps(_canonical(params or {}), separators=(",", ":"), ensure_ascii=False)


def data_fingerprint(rows: Sequence[Mapping[str, Any]] | None) -> str:
    """数据指纹：``行数:首日期:末日期:内容哈希前16位``。

    ``rows`` 为有序序列，每行至少含 ``date`` 字段（``YYYY-MM-DD``）。
    ``None`` 表示「没有数据」，与空列表不同。
    """
    if rows is None:
        return "none"
    if not rows:
        return "0::-:"
    first = str(rows[0].get("date", ""))
    last = str(rows[-1].get("date", ""))
    payload = json.dumps(
        [_canonical(dict(r)) for r in rows], separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{len(rows)}:{first}:{last}:{digest}"


def calc_id(
    op: str,
    params: Mapping[str, Any] | None = None,
    *,
    data_fp: str,
    version: str = CALC_VERSION,
) -> str:
    if not op:
        raise ValueError("op 不能为空")
    if not data_fp:
        raise ValueError("data_fp 不能为空")
    material = _SEP.join([op, version, canonical_params(params), data_fp])
    return "c_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
