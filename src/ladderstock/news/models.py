"""统一资讯 schema、归一化与去重键（R6）。

三条口径写在这里，其余模块只引用、不各自实现：

1. **同一 schema**：`{id, source, title, url, published_at, related_codes, summary,
   content_hash}` 八个键是对外契约（R10 页面与 MCP `news_stream` 都吃这一份）；
   `origins` / `provenance` 是审计附加键——去重要「记录来源列表」，
   DEC-018 要求「任何进入报告的内容必须带来源标签」，两者都不能少。
2. **去重键**：`url` 规范化后优先，其次标题归一化。同一条稿子在巨潮/深交所/东财
   同时出现时只留一条，其余并进 `origins`。
3. **时间不做推测**：上游只给日期就输出日期（`2026-10-30`），给到秒才输出 ISO 日期时间；
   解析不了就是 `None`，不拿抓取时间冒充发布时间。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

#: 对外契约的八个键：R10 页面、MCP `news_stream`、本机库都按这一份走。
#: 加键要先改契约（DEC-019），不能在某个出口单方面多塞一个字段。
CONTRACT_KEYS: tuple[str, ...] = (
    "id",
    "source",
    "title",
    "url",
    "published_at",
    "related_codes",
    "summary",
    "content_hash",
)

#: A 股发布时间统一按东八区表述（上游 cls/见闻给的是 epoch 秒，巨潮给的是本地串）
CN_TZ = timezone(timedelta(hours=8))

#: `summary` 落库上限：快讯正文整段进来会把库撑爆，页面也不需要全文
SUMMARY_LIMIT = 1000

#: 送模型的摘要上限（issue 要求「喂模型的只有标题 + ≤200 字摘要 + url」）
AI_SUMMARY_LIMIT = 200

ID_DIGITS = 16

_TRACKING_PARAMS = re.compile(r"^(?:utm_|sk=|from=|srcid|ivt_|share_|traceid|wm)", re.I)

#: 6 位 A 股代码：沪 60/68、深 00/30、北 43/83/87/92。
#: 故意只认这些前缀：把「123456 万元」这类金额当成代码是主要的误抽来源。
_CODE_RE = re.compile(r"(?<!\d)((?:60|68|00|30|43|83|87|92)\d{4})(?!\d)")

_PUNCT_RE = re.compile(
    r"[\s\u3000]+|[【】\[\]（）()《》〈〉「」『』“”\"'’‘·、,，。.;:；:！!？?—－_-]+"
)


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #

def normalize_title(title: Any) -> str:
    """标题归一化：全角转半角、去空白与标点、转小写。

    去重靠这个串，所以必须**稳定且可重跑**：同一篇稿子在不同来源里的
    区别通常只有空格、书名号和大小写，不能因为排版差异被判成两条。
    """
    text = unicodedata.normalize("NFKC", str(title or "")).strip().lower()
    return _PUNCT_RE.sub("", text)


def canonical_url(url: Any) -> str:
    """URL 规范化：补 https、host 转小写、去 tracking 查询串与 fragment。"""
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    query = "&".join(
        pair for pair in parts.query.split("&") if pair and not _TRACKING_PARAMS.match(pair)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def extract_codes(*values: Any, preferred: str | None = None) -> tuple[str, ...]:
    """从「上游给的 code」+ 标题/正文里的 6 位代码提取关联代码。

    `preferred` 是取数时用的 code（公告类必须带上），排在最前。
    已知局限：快讯里的公司简称→代码映射没有数据源，不做猜测（漏抽见抽查表）。
    """
    codes: list[str] = []
    seen: set[str] = set()
    for value in (preferred, *(v for v in values if v is not None)):
        for raw in _iter_code_candidates(value):
            code = normalize_code(raw)
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
    return tuple(codes)


def _iter_code_candidates(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_iter_code_candidates(item))
        return out
    text = str(value or "").strip()
    if not text:
        return []
    return _CODE_RE.findall(text)


def normalize_code(value: Any) -> str:
    """`600519.SH` / `sh600519` / `600519` → `600519`；前缀不是合法 A 股段就返回空串。"""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = text.split(".", 1)[0]
    text = re.sub(r"^(?:SH|SZ|BJ)", "", text)
    return text if _CODE_RE.fullmatch(text) else ""


def fingerprint(title_key: str, summary: str) -> str:
    """内容指纹：标题归一化 + 摘要归一化，用于「同内容不同地址」的二次判定。"""
    digest = hashlib.sha256(f"{title_key}\x1f{_PUNCT_RE.sub('', summary.lower())}".encode())
    return digest.hexdigest()


def make_id(dedup_key: str) -> str:
    """稳定 ID：只由去重键决定，所以同一 fixture 重跑必然得到同一批 ID。"""
    return hashlib.sha1(dedup_key.encode()).hexdigest()[:ID_DIGITS]


def parse_published_at(value: Any) -> str | None:
    """把上游各种时间写法收敛成 ISO 串；解析不了返回 None（不猜）。

    实测形态（`th-cn tools/list` + 真实返回）：
    - `cls_telegraph` / `wallstreetcn_lives`：epoch **秒**（int）
    - `cninfo/szse/eastmoney`：`"2026-10-31 00:00:00"`
    - `eastmoney_reports` / `sina_research_reports`：`"2026-10-31"`（只有日期）
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # 毫秒
            seconds /= 1000.0
        if seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds, CN_TZ).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text or text in {"None", "null", "-"}:
        return None

    # 只有日期的形态（研报 `publish_date`、cctv 的 `date`、`20261030` 这种紧凑写法）
    # **必须排在 epoch 分支前面**：`"20261030"` 也是纯数字，先按秒解析会得到 1970-08-23。
    date_only = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text) or re.fullmatch(
        r"(\d{4})(\d{2})(\d{2})", text
    )
    if date_only:
        iso = "-".join(date_only.groups())
        try:
            return date.fromisoformat(iso).isoformat()
        except ValueError:
            return None

    if text.isdigit():
        return parse_published_at(int(text))

    candidates = (text, text.replace(" ", "T", 1))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CN_TZ)
        return parsed.astimezone(CN_TZ).isoformat(timespec="seconds")
    return None


def publish_day(published_at: str | None) -> date | None:
    """从 `published_at` 取日期部分（日历与时间窗过滤都用它）。"""
    if not published_at:
        return None
    try:
        return date.fromisoformat(published_at[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 统一条目
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NewsItem:
    """资讯流的一条统一记录（八个契约键 + 两个审计键）。"""

    id: str
    source: str
    title: str
    url: str
    published_at: str | None
    related_codes: tuple[str, ...]
    summary: str
    content_hash: str
    dedup_key: str
    origins: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
            "related_codes": list(self.related_codes),
            "summary": self.summary,
            "content_hash": self.content_hash,
            # 审计键：去重必须留下「同稿多源」的来源列表，不能静默丢弃
            "origins": [dict(origin) for origin in self.origins],
            "provenance": dict(self.provenance),
        }

    @property
    def day(self) -> date | None:
        return publish_day(self.published_at)

    def ai_payload(self) -> dict[str, Any]:
        """送模型的最小视图：**只有**标题 + ≤200 字摘要 + url。

        正文全文、related_codes、provenance 一律不进 prompt（issue 要求 4），
        这条边界由 `tests/unit/news/test_ai_payload.py` 反向钉住。
        """
        summary = self.summary[:AI_SUMMARY_LIMIT]
        return {"title": self.title, "summary": summary, "url": self.url}


def build_item(
    *,
    dataset: str,
    title: Any,
    url: Any,
    published_at: Any,
    summary: Any = "",
    code: Any = None,
    provenance: dict[str, Any] | None = None,
) -> NewsItem | None:
    """把一条上游记录翻译成 `NewsItem`；标题与地址都没有就丢弃（无从审计）。"""
    clean_title = str(title or "").strip()
    clean_url = canonical_url(url)
    if not clean_title and not clean_url:
        return None

    title_key = normalize_title(clean_title)
    summary_text = str(summary or "").strip()[:SUMMARY_LIMIT]
    dedup_key = f"url:{clean_url}" if clean_url else f"title:{title_key}"
    if not clean_title:
        # 只有地址没有标题：标题用地址末段顶上，去重仍按 url，不丢条目
        clean_title = clean_url.rsplit("/", 1)[-1]

    return NewsItem(
        id=make_id(dedup_key),
        source=dataset,
        title=clean_title,
        url=clean_url,
        published_at=parse_published_at(published_at),
        related_codes=extract_codes(clean_title, summary_text, preferred=code),
        summary=summary_text,
        content_hash=fingerprint(title_key, summary_text),
        dedup_key=dedup_key,
        origins=({"dataset": dataset, "title": clean_title, "url": clean_url},),
        provenance=dict(provenance or {}),
    )
