"""`config/industry.yaml` 的读取与校验（R5，QUOT-143）。

清单是**数据**，不是口径：行业名称、`l1_code` 绑定、板块同名绑定、窗口参数
都在这里，公式与缺失策略在 `docs/specs/SPEC-INDUSTRY-METRICS.md`。

读不到 / 读不懂都按 **3（配置错误）** 退出——行业清单配错时给出的快照比
「一片 null」更危险，因为看起来像是数据缺了。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ladderstock.cli.paths import config_dir
from ladderstock.errors import EXIT_CONFIG, LadderStockError

CONFIG_FILENAME = "industry.yaml"

#: 申万一级代码格式：6 位数字，前 2 位一级 + `0000`（与 `sw_industry_history`
#: 返回的 `l1_code` 同格式，能直接对上）
L1_CODE_PATTERN = re.compile(r"^\d{2}0000$")

DEFAULT_WINDOW: dict[str, Any] = {
    "fund_days": 5,
    "valuation_lookback_days": 250,
    "turnover_lookback_days": 60,
    "momentum_windows": [5, 20],
}

#: 窗口参数的下限：0 或负数会让「最近 N 日」变成「不看」，属于配置错误
MIN_WINDOW_DAYS = 1


@dataclass(frozen=True)
class IndustryEntry:
    """一个申万一级行业。"""

    name: str
    l1_code: str | None
    anchors: tuple[str, ...] = ()
    board_name: str | None = None
    enabled: bool = True
    note: str = ""

    @property
    def bound(self) -> bool:
        """是否已绑定申万一级代码（未绑定 ⇒ 成分派生维度全部显式 null）。"""
        return self.l1_code is not None


@dataclass(frozen=True)
class IndustryCatalog:
    window: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_WINDOW))
    entries: tuple[IndustryEntry, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    membership_caliber: str = "sw_l1"
    path: str = ""

    def enabled_entries(self) -> list[IndustryEntry]:
        return [entry for entry in self.entries if entry.enabled]

    def names(self, *, enabled_only: bool = True) -> list[str]:
        source = self.enabled_entries() if enabled_only else list(self.entries)
        return [entry.name for entry in source]

    def find(self, name: str) -> IndustryEntry | None:
        wanted = (name or "").strip()
        if not wanted:
            return None
        for entry in self.entries:
            if entry.name == wanted:
                return entry
        return None

    def window_days(self, key: str) -> int:
        return int(self.window.get(key) or DEFAULT_WINDOW[key])


def _problem(message: str) -> LadderStockError:
    return LadderStockError(f"config/{CONFIG_FILENAME}：{message}", EXIT_CONFIG)


def _entry(raw: Any, index: int) -> IndustryEntry:
    if not isinstance(raw, dict):
        raise _problem(f"industries[{index}] 不是对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise _problem(f"industries[{index}] 缺 name")
    code = raw.get("l1_code")
    l1_code: str | None = None
    if code not in (None, "", "null"):
        l1_code = str(code).strip()
        if not L1_CODE_PATTERN.match(l1_code):
            raise _problem(f"{name} 的 l1_code {l1_code!r} 不是 6 位申万一级代码（NN0000）")
    board = raw.get("board_name")
    return IndustryEntry(
        name=name,
        l1_code=l1_code,
        anchors=tuple(str(x).strip() for x in (raw.get("anchors") or []) if str(x).strip()),
        board_name=str(board).strip() if board else None,
        enabled=bool(raw.get("enabled", True)),
        note=str(raw.get("note") or ""),
    )


def load_catalog(config_root: Path | None = None) -> IndustryCatalog:
    """读取行业清单；文件缺失/格式错/代码非法一律 3 退出。"""
    root = Path(config_root) if config_root is not None else config_dir()
    path = root / CONFIG_FILENAME
    if not path.is_file():
        raise _problem(f"文件不存在（{path}）")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise _problem(f"YAML 解析失败：{exc}") from exc

    body = doc.get("industry") if isinstance(doc, dict) else None
    if not isinstance(body, dict):
        raise _problem("缺少顶层 `industry` 段")
    raw_entries = body.get("industries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise _problem("`industries` 必须是非空列表")

    entries = tuple(_entry(raw, index) for index, raw in enumerate(raw_entries))
    names = [entry.name for entry in entries]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise _problem(f"行业名称重复：{'、'.join(duplicated)}")
    codes = [entry.l1_code for entry in entries if entry.l1_code]
    collided = sorted({code for code in codes if codes.count(code) > 1})
    if collided:
        raise _problem(f"l1_code 重复绑定：{'、'.join(collided)}（一个代码只能属于一个行业）")

    window = dict(DEFAULT_WINDOW)
    raw_window = body.get("window")
    if isinstance(raw_window, dict):
        for key, value in raw_window.items():
            if key not in DEFAULT_WINDOW:
                raise _problem(f"未知的 window 键 {key!r}")
            window[key] = value
    for key in ("fund_days", "valuation_lookback_days", "turnover_lookback_days"):
        if int(window[key]) < MIN_WINDOW_DAYS:
            raise _problem(f"window.{key} 必须 ≥ {MIN_WINDOW_DAYS}")
    momentum = window.get("momentum_windows")
    if not isinstance(momentum, list) or not momentum:
        raise _problem("window.momentum_windows 必须是非空列表")
    window["momentum_windows"] = [int(x) for x in momentum]
    if any(x < MIN_WINDOW_DAYS for x in window["momentum_windows"]):
        raise _problem("window.momentum_windows 的每个窗口必须 ≥ 1")

    return IndustryCatalog(
        window=window,
        entries=entries,
        provenance=dict(body.get("provenance") or {}),
        membership_caliber=str(body.get("membership_caliber") or "sw_l1"),
        path=str(path),
    )
