"""长期偏好沉淀：把用户说过一次的稳定偏好记下来，下次自动带上。

它解决的是「每次都要重新交代」：用户说「我只看创业板和半导体」「回答短一点」，
下一轮不该再让他重复。本模块只做三件事——从**用户提问**里抽取偏好、落盘、拼注入段。

三条设计边界：

1. **只从提问抽，不从回答抽。** 回答是模型的产出，不是用户事实；从回答里抽偏好
   等于把模型措辞当成用户意愿。
2. **纯规则、零模型调用。** 可解释、零成本、可测；宁可漏抽，不可错抽——错抽的
   偏好会一直污染后续每一轮。
3. **和 mem0 记忆分开。** `ai/memory.py` 存的是「聊过什么」（问答记忆，按模块命名
   空间隔离）；这里存的是「用户是谁、要什么」（画像，全局一份）。两者不混。

存储是单文件 `<data_dir>/preferences.json`，原子写、上限 `MAX_ITEMS` 条。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: 偏好文件在数据根下的固定位置（与 `watch_live_state.json` 同级，用户能直接看到）
FILENAME = "preferences.json"

#: 上限：超了按「先淘汰从没被再提过、且最旧的」收敛，避免无限增长
MAX_ITEMS = 30

#: 单条偏好文本上限（字符）——超过就不是一句偏好，是段描述
MAX_TEXT = 50

#: 只有 ≤ 这个长度的句子才当作偏好句；长句多半是行情描述，不是设定
MAX_SENTENCE = 60

#: 偏好类别：范围 / 表达 / 约束 / 跟踪标的
KIND_SCOPE = "scope"
KIND_STYLE = "style"
KIND_CONSTRAINT = "constraint"
KIND_WATCH = "watch"

KIND_LABELS = {
    KIND_SCOPE: "关注范围",
    KIND_STYLE: "表达方式",
    KIND_CONSTRAINT: "约束",
    KIND_WATCH: "跟踪对象",
}

#: 注入段的标题行；`_prepare_call` 把它接在 system 之后
SECTION_HEADER = "【用户长期偏好】（自动沉淀自历史对话；与本轮要求冲突时以本轮为准）"

#: 已覆盖的市场 / 板块 / 赛道词表。只认这张表里的词，避免把任意名词当偏好
_TRACK_WORDS = (
    "A股", "港股", "美股", "创业板", "科创板", "北交所", "主板", "沪深300", "中证500",
    "半导体", "芯片", "白酒", "医药", "医疗", "新能源", "光伏", "储能", "军工", "券商",
    "银行", "保险", "有色", "煤炭", "钢铁", "化工", "建材", "地产", "房地产", "消费",
    "家电", "汽车", "机器人", "算力", "人工智能", "AI", "通信", "传媒", "游戏", "农业",
    "稀土", "电力", "机场", "航运", "港口", "军工电子", "低空经济", "创新药", "中药",
)

#: 表达方式词表
_STYLE_WORDS = (
    "短句", "简短", "简洁", "精简", "详细", "详尽", "表格", "图表", "中文", "英文",
    "大白话", "少术语", "别太长", "不要长篇", "分点", "结论先行", "直接说结论",
)

#: 用户明确不想要的（合规与体验约束都归这里）
_CONSTRAINT_WORDS = (
    "买卖建议", "推荐", "荐股", "预测", "涨跌预测", "目标价", "止损", "仓位", "仓位建议",
)

#: 「不要 X」里 X 属于表达要求时，归到表达方式而不是约束（同一件事只留一条）
_STYLE_NEG: dict[str, str] = {
    "长篇": "回答别太长",
    "太长": "回答别太长",
    "啰嗦": "回答别太长",
    "长篇大论": "回答别太长",
    "术语": "回答少用术语",
    "套话": "回答别说套话",
    "废话": "回答别说废话",
}

_SENTENCE_SPLIT = re.compile(r"[。！？；\n\r]+")
_SPACES = re.compile(r"\s+")
_TRAILING = "，,、 　"

#: 句式 → 类别。「X」处只收上面词表内的词，命中即归一化成一句短偏好。
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 我只看创业板 / 我主要做半导体 / 我只关注港股
    (KIND_SCOPE, re.compile(r"我(?:只|就|主要|平时|一般|通常)(?:看|做|关注|研究|炒|玩)(.{2,24})")),
    # 以后都用短句 / 每次都给我表格
    (KIND_STYLE, re.compile(r"(?:以后|今后|下次|每次|之后)(?:都|请|要|给我)?(.{2,24})")),
    (KIND_STYLE, re.compile(r"回答(?:请)?(?:用|要|尽量)?(.{2,24})")),
    # 不要给我买卖建议 / 别长篇
    (KIND_CONSTRAINT, re.compile(r"(?:不要|别|不用|不需要|别再)(?:给我)?(.{2,24})")),
    # 我偏好短句
    (KIND_STYLE, re.compile(r"我(?:更)?(?:偏好|喜欢|习惯|想要)(.{2,24})")),
    # 帮我盯一下 600036 / 跟踪浦发银行
    (KIND_WATCH, re.compile(r"(?:帮我)?(?:盯|跟踪|看好|关注下)(.{2,24})")),
)

#: 「记住 X」= 用户显式要求沉淀，原样收（仍需过长度与脱敏关）
_EXPLICIT = re.compile(r"(?:记住|记一下|记录一下|帮我记下)(.{2,40})")


@dataclass
class Preference:
    """一条偏好。`hits` 记被重复提出的次数，用来决定淘汰顺序。"""

    text: str
    kind: str
    created_at: str
    updated_at: str = ""
    hits: int = 1
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def path_for(data_root: Path | str | None = None) -> Path:
    """偏好文件路径。`data_root` 可注入（测试用），生产走 `cli.paths.data_dir()`。"""
    root = Path(data_root) if data_root is not None else _data_dir()
    return root / FILENAME


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


# --------------------------------------------------------------------------- #
# 读 / 写
# --------------------------------------------------------------------------- #


def load(data_root: Path | str | None = None) -> list[Preference]:
    """读全部偏好。文件缺失 / 损坏一律当空表——偏好是增强，不是必需。"""
    path = path_for(data_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("preferences_unreadable path=%s detail=%s", path, exc)
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    result: list[Preference] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or KIND_STYLE)
        if not text or kind not in KIND_LABELS:
            continue
        result.append(
            Preference(
                text=text,
                kind=kind,
                created_at=str(item.get("created_at") or ""),
                updated_at=str(item.get("updated_at") or ""),
                hits=int(item.get("hits") or 1),
                sources=[str(s) for s in (item.get("sources") or [])][:5],
            )
        )
    return result


def save(items: Iterable[Preference], data_root: Path | str | None = None) -> Path:
    """原子写：先写同目录临时文件再 `os.replace`，避免半截文件让下次读崩。"""
    path = path_for(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": _now(),
        "items": [item.to_dict() for item in items],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# 抽取
# --------------------------------------------------------------------------- #


def candidates(question: str) -> list[tuple[str, str]]:
    """从**一句用户提问**里抽候选偏好 `(kind, text)`。抽不到就返回空表。"""
    text = (question or "").strip()
    if not text:
        return []

    found: list[tuple[str, str]] = []

    explicit = _EXPLICIT.search(text)
    if explicit:
        body = _clean(explicit.group(1))
        if body and len(body) <= MAX_TEXT:
            found.append((_kind_of(body), body))
        # 显式「记住」已经覆盖整句，不再走句式匹配，避免同句抽两条互相矛盾的
        return found

    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip(_TRAILING)
        if not sentence or len(sentence) > MAX_SENTENCE:
            continue
        for kind, pattern in _PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            body = _clean(match.group(1))
            if not body:
                continue
            resolved = _resolve(kind, body, sentence)
            if resolved:
                found.append(resolved)
                break
    return _dedupe(found)


def _kind_of(body: str) -> str:
    """显式语句按内容归类别：命中词表才算数，否则归「表达方式」。"""
    if _hits(body, _CONSTRAINT_WORDS):
        return KIND_CONSTRAINT
    if _hits(body, _TRACK_WORDS):
        return KIND_SCOPE
    return KIND_STYLE


def _resolve(kind: str, body: str, sentence: str) -> tuple[str, str] | None:
    """把一个候选短语收敛成一句话；短语里没有词表词就丢弃（宁可漏，不可错）。"""
    if kind == KIND_CONSTRAINT:
        style = _hits(body, tuple(_STYLE_NEG))
        if style:
            return KIND_STYLE, _STYLE_NEG[style]
        hit = _hits(body, _CONSTRAINT_WORDS)
        if not hit:
            return None
        return KIND_CONSTRAINT, f"不要{hit}"
    if kind == KIND_WATCH:
        # 标的必须像代码（6 位数字），否则当成普通句子丢掉
        code = re.search(r"\d{6}", body)
        if code:
            return KIND_WATCH, f"跟踪 {code.group(0)}"
        return None
    track = _hits(body, _TRACK_WORDS)
    style = _hits(body, _STYLE_WORDS)
    if kind == KIND_SCOPE:
        if track:
            # 文本用用户原话（「只看创业板和半导体」），词表只用来判定「这确实是范围」，
            # 避免把「我只看热闹」这类句子收成偏好。
            verb = "只看" if any(word in sentence for word in ("看", "关注", "研究")) else "只做"
            return KIND_SCOPE, f"{verb}{body}"
        return None
    # style 分支：两种词都认，优先表达类
    if style:
        return KIND_STYLE, f"回答用{style}"
    if track and "看" in sentence:
        return KIND_SCOPE, f"只看{track}"
    return None


def _hits(body: str, words: tuple[str, ...]) -> str:
    """返回 phrase 里第一个命中的词表词（长词优先，避免「白酒」被「酒」抢）。"""
    for word in sorted(words, key=len, reverse=True):
        if word in body:
            return word
    return ""


def _clean(body: str) -> str:
    text = _SPACES.sub("", body).strip(_TRAILING)
    text = re.sub(r"^[：:，,。！!？?\s]+", "", text)
    text = re.sub(r"^(?:就|都|要|请|给我|帮我|一下)+", "", text)
    return text[:MAX_TEXT]


def _dedupe(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """同一句里可能两个句式都命中：按 (类别, 归一化文本) 去重，保留首次出现的写法。"""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for kind, text in pairs:
        key = (kind, _norm(text))
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, text))
    return out


def _norm(text: str) -> str:
    """去重口径：忽略标点、空格与大小写。"""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #


def remember(question: str, *, data_root: Path | str | None = None) -> list[Preference]:
    """抽取并落盘，返回**本次新增或强化**的偏好（无变化返回空表）。

    重复提出同一偏好时 `hits += 1`，这让「常被提到的」在淘汰时活得更久。
    """
    pairs = candidates(question)
    if not pairs:
        return []
    items = load(data_root)
    index: dict[tuple[str, str], Preference] = {(item.kind, _norm(item.text)): item for item in items}
    changed: list[Preference] = []
    now = _now()
    for kind, text in pairs:
        key = (kind, _norm(text))
        existing = index.get(key)
        if existing is not None:
            existing.hits += 1
            existing.updated_at = now
            changed.append(existing)
            continue
        item = Preference(text=text, kind=kind, created_at=now, updated_at=now)
        items.append(item)
        index[key] = item
        changed.append(item)
    save(_trim(items), data_root)
    if changed:
        logger.info("preferences_stored count=%d kinds=%s", len(changed), [c.kind for c in changed])
    return changed


def _trim(items: list[Preference]) -> list[Preference]:
    """超上限时先淘汰 hits 最低、再淘汰最旧的。"""
    if len(items) <= MAX_ITEMS:
        return items
    ordered = sorted(items, key=lambda item: (item.hits, item.updated_at or item.created_at), reverse=True)
    return ordered[:MAX_ITEMS]


def clear(data_root: Path | str | None = None) -> bool:
    """删掉偏好文件。返回是否真的删了。"""
    path = path_for(data_root)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


# --------------------------------------------------------------------------- #
# 注入
# --------------------------------------------------------------------------- #


def render(items: Iterable[Preference] | None = None, *, limit: int = 12) -> str:
    """拼成注入段。空表返回空串——调用点据此决定要不要加这一段。"""
    listed = list(items) if items is not None else load()
    if not listed:
        return ""
    ordered = sorted(listed, key=lambda item: (item.hits, item.created_at), reverse=True)[:limit]
    lines = [SECTION_HEADER]
    for item in ordered:
        lines.append(f"- [{KIND_LABELS[item.kind]}] {item.text}")
    return "\n".join(lines)


def as_dict(items: Iterable[Preference] | None = None) -> dict[str, Any]:
    """给 HTTP 层用的稳定结构（`/api/preferences`）。

    带上 `kind_label`：前端不该自己维护一份中文表，否则加类别时要改两处。
    """
    listed = list(items) if items is not None else load()
    return {
        "count": len(listed),
        "items": [
            {**item.to_dict(), "kind_label": KIND_LABELS.get(item.kind, item.kind)}
            for item in sorted(listed, key=lambda i: i.created_at, reverse=True)
        ],
    }


def add_item(
    text: str,
    *,
    kind: str | None = None,
    data_root: Path | str | None = None,
) -> Preference | None:
    """手工加一条偏好（页面「我记住的」里自己写一条）。

    与 `remember()` 的区别：不做句式抽取、不因为「像行情描述」而丢弃——用户亲手
    写的就是他想要的。类别没给时按同一套词表推断，推不出归「表达方式」（最无害
    的一类，不会改变关注范围）。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw) > MAX_TEXT:
        raw = raw[:MAX_TEXT]
    resolved = kind if kind in KIND_LABELS else ""
    if not resolved:
        inferred = candidates(raw)
        resolved = inferred[0][0] if inferred else KIND_STYLE
    items = load(data_root)
    now = _now()
    for item in items:
        if (item.kind, _norm(item.text)) == (resolved, _norm(raw)):
            item.hits += 1
            item.updated_at = now
            save(_trim(items), data_root)
            return item
    item = Preference(text=raw, kind=resolved, created_at=now, updated_at=now)
    items.append(item)
    save(_trim(items), data_root)
    logger.info("preferences_added kind=%s text=%s", resolved, raw)
    return item


def remove(text: str, *, data_root: Path | str | None = None) -> bool:
    """按文本删一条。文本是用户唯一认得的「主键」，不做 id 映射。"""
    target = _norm((text or "").strip())
    if not target:
        return False
    items = load(data_root)
    kept = [item for item in items if _norm(item.text) != target]
    if len(kept) == len(items):
        return False
    save(kept, data_root)
    logger.info("preferences_removed text=%s", target)
    return True


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
