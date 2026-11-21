"""F10 截图 / PDF 查证：把一份研报截图或公告 PDF 变成**可核对的结构化数字**。

三段流水，每段都能单独用：

1. `extract_pdf` / `extract_image`：拿到文本。PDF 走 `pypdf` 文本层；扫描版（抽不出字）
   明确报 `needs_ocr`；图片走**可插拔的 vision 后端**（`LDST_VISION_*`），没配置就
   报 `ocr_unavailable` 并说明怎么配——**绝不伪造识别结果**；
2. `parse_figures`：从文本里抽关键数字（代码 / 价格 / 涨跌 / PE / PB / 营收 / 净利 / EPS / ROE），
   每个数字保留原文片段，便于人工复核；
3. `reconcile`：把抽到的数字与**本机已落盘数据**（`daily_k` / `valuation_history` /
   `finance_indicators`）逐项比对，判定 `match` / `mismatch` / `unverifiable`。
   本地没有这个域就写「本地无此域」，不拿别的字段凑。

本机能力（实测）：`pypdf` ✓、`PIL` ✓；`pdfplumber` / `fitz` / `pytesseract` ✗、
PATH 无 `tesseract`。所以本地没有 OCR，图片路径必须靠 vision 后端或明确报不可用。

验收口径（F10）：20 张真实截图的关键数字识别 + 核对正确率 ≥85%。`verify()` 的返回值
就是逐项可数的：`reconciled` 里 `match` 数 / `comparable` 数即正确率分子分母。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

VERIFY_SUBDIR = "verify"

#: 抽出来短于这个长度就当"没有文本层"：合规公告正文常见 120+ 字，但截图/扫描件通常 <20
MIN_TEXT_CHARS = 80

STATUS_OK = "ok"
STATUS_NEEDS_OCR = "needs_ocr"
STATUS_OCR_UNAVAILABLE = "ocr_unavailable"
STATUS_UNSUPPORTED = "unsupported"
STATUS_FAILED = "failed"

VISION_BASE_ENV = "LDST_VISION_BASE_URL"
VISION_KEY_ENV = "LDST_VISION_API_KEY"
VISION_MODEL_ENV = "LDST_VISION_MODEL"
VISION_TIMEOUT_ENV = "LDST_VISION_TIMEOUT"

DEFAULT_VISION_TIMEOUT = 60.0

SUPPORTED_IMAGE_MIME: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp", "image/bmp")

#: 相对容差（价格 / 估值 / 财务）。截图常是盘中价或四舍五入后的口径，给 2%~10% 是必要的
TOLERANCE: dict[str, float] = {
    "price": 0.02,
    "pct": 0.005,  # 绝对百分点
    "pe": 0.10,
    "pb": 0.10,
    "revenue": 0.05,
    "net_profit": 0.05,
    "eps": 0.02,  # 绝对值
    "roe": 0.005,  # 绝对百分比点
}

FIELD_ORDER: tuple[str, ...] = (
    "code",
    "price",
    "pct",
    "pe",
    "pb",
    "revenue",
    "net_profit",
    "eps",
    "roe",
)

FIELD_LABELS: dict[str, str] = {
    "code": "证券代码",
    "price": "价格",
    "pct": "涨跌幅",
    "pe": "市盈率 PE",
    "pb": "市净率 PB",
    "revenue": "营业收入（亿元）",
    "net_profit": "净利润（亿元）",
    "eps": "每股收益 EPS",
    "roe": "净资产收益率 ROE",
}

#: 每个字段对应的本地数据集与取值口径（对账时逐项查，查不到就写缺）
FIELD_SOURCE: dict[str, str] = {
    "price": "daily_k · close",
    "pct": "daily_k · close / 前一交易日 close",
    "pe": "valuation_history · pe_ttm",
    "pb": "valuation_history · pb",
    "revenue": "finance_indicators · revenue（最近报告期，亿元）",
    "net_profit": "finance_indicators · net_profit（最近报告期，亿元）",
    "eps": "finance_indicators · eps",
    "roe": "finance_indicators · roe",
}

_CN_UNITS: dict[str, float] = {"万": 1e-4, "万元": 1e-4, "亿": 1.0, "亿元": 1.0, "万亿": 1e4}

_CODE_RE = re.compile(r"\b(60\d{4}|68\d{4}|00\d{4}|30\d{4}|8\d{4}|4\d{5})\b")
_PCT_RE = re.compile(r"(?:涨跌幅|涨跌|涨幅|跌幅)\s*[:：]?\s*(?:[-+]?\s*)?([-+]?\d+(?:\.\d+)?)\s*%")
_ANY_PCT_RE = re.compile(r"([-+]?\d{1,3}(?:\.\d{1,2})?)\s*%")
_PRICE_RE = re.compile(
    r"(?:收盘价|最新价|现价|股价|收盘)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?"
)
_PE_RE = re.compile(r"(?:市盈率\s*\(\s*TTM\s*\)|市盈率\s*TTM|市盈率|PE\s*\(?TTM\)?|PE)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)")
_PB_RE = re.compile(r"(?:市净率|PB)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)")
_REVENUE_RE = re.compile(
    r"(?:营业(?:总)?收入|营收)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*(万亿|亿元|亿|万元|万)?"
)
_NET_PROFIT_RE = re.compile(
    r"(?:归母净利润|净利润|净利)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*(万亿|亿元|亿|万元|万)?"
)
_EPS_RE = re.compile(r"(?:每股收益|EPS)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*(?:元)?")
_ROE_RE = re.compile(r"(?:净资产收益率|ROE)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*%")
_DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


# --------------------------------------------------------------------------- #
# 能力探测
# --------------------------------------------------------------------------- #


def _vision_settings() -> dict[str, Any]:
    base = (os.environ.get(VISION_BASE_ENV) or "").strip()
    key = (os.environ.get(VISION_KEY_ENV) or "").strip()
    model = (os.environ.get(VISION_MODEL_ENV) or "").strip()
    try:
        timeout = float(os.environ.get(VISION_TIMEOUT_ENV) or DEFAULT_VISION_TIMEOUT)
    except ValueError:
        timeout = DEFAULT_VISION_TIMEOUT
    return {"base_url": base, "api_key": key, "model": model, "timeout": timeout}


def capabilities() -> dict[str, Any]:
    """本机能做什么：PDF 文本层 / 图片 vision / 本地 OCR。页面与 CLI 都读这份。"""
    try:
        import pypdf  # noqa: F401

        pdf_text = True
        pdf_reason = ""
    except ImportError as exc:  # pragma: no cover - 依赖缺失时明确报
        pdf_text = False
        pdf_reason = f"缺 pypdf：{exc}"
    vision = _vision_settings()
    vision_ready = bool(vision["base_url"] and vision["model"])
    missing_env = [
        name
        for name, value in (
            (VISION_BASE_ENV, vision["base_url"]),
            (VISION_MODEL_ENV, vision["model"]),
        )
        if not value
    ]
    image_reason = ""
    if not vision_ready:
        hint = "、".join(missing_env) if missing_env else f"{VISION_BASE_ENV} / {VISION_MODEL_ENV}"
        image_reason = f"未配置图片识别后端：设 {hint}（图片走 OpenAI 兼容 chat/completions 的 image_url）"
    return {
        "pdf_text": pdf_text,
        "pdf_reason": pdf_reason,
        "image_vision": vision_ready,
        "image_reason": image_reason,
        "local_ocr": False,
        "local_ocr_reason": "本机未装 tesseract / pdfplumber / fitz，扫描件与图片都不在本地做 OCR",
        "supported_image_mime": list(SUPPORTED_IMAGE_MIME),
        "tolerance": dict(TOLERANCE),
    }


# --------------------------------------------------------------------------- #
# 抽取
# --------------------------------------------------------------------------- #


def _clean_pdf_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_pdf(data: bytes, *, max_pages: int = 20, password: str | None = None) -> dict[str, Any]:
    """PDF → 文本。抽不出字（扫描版）报 `needs_ocr`，不假装识别成功。"""
    try:
        import io

        import pypdf
    except ImportError as exc:  # pragma: no cover
        return {"status": STATUS_UNSUPPORTED, "text": "", "pages": 0, "chars": 0, "reason": f"缺 pypdf：{exc}"}
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and password:
            reader.decrypt(password)
        total = len(reader.pages)
        pages = reader.pages[: max(1, int(max_pages))]
        chunks: list[str] = []
        for page in pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - 单页坏不影响整篇
                continue
    except Exception as exc:  # noqa: BLE001 - 损坏 / 加密 PDF
        return {
            "status": STATUS_FAILED,
            "text": "",
            "pages": 0,
            "chars": 0,
            "reason": f"PDF 解析失败：{type(exc).__name__}: {exc}",
        }
    text = _clean_pdf_text("\n".join(chunks))
    if len(text) < MIN_TEXT_CHARS:
        return {
            "status": STATUS_NEEDS_OCR,
            "text": text,
            "pages": total,
            "chars": len(text),
            "reason": f"文本层只有 {len(text)} 字（<{MIN_TEXT_CHARS}），大概率是扫描版；本机无 OCR，需配 vision 后端或换带文本层的 PDF",
        }
    return {"status": STATUS_OK, "text": text, "pages": total, "chars": len(text), "reason": ""}


VISION_PROMPT = (
    "你是财报/研报信息抽取器。只输出 JSON，不要解释。结构：\n"
    '{"codes":["6位A股代码"],"date":"YYYY-MM-DD 或空",'
    '"text":"图上主要文字（原样，不超过 800 字）",'
    '"figures":[{"label":"图上原文标签","value":数字,"unit":"元/%/亿/万元/倍","page":1}]}\n'
    "规则：\n"
    "1. 只抄图上真实出现的数字，不做任何推算、不补单位、不四舍五入；\n"
    "2. 一个数字对应一个 label（如 收盘价 12.34、市盈率 18.2、营业收入 123.4 亿）；\n"
    "3. 看不清或没有数字就留空数组，禁止猜测；\n"
    "4. 代码只认 6 位 A 股代码（60/68/00/30/8/4 开头）。"
)


def extract_image(data: bytes, mime: str, *, vision: dict[str, Any] | None = None) -> dict[str, Any]:
    """图片 → 文本 + 数字。没配 vision 后端就报 `ocr_unavailable`（含怎么配）。"""
    settings = vision or _vision_settings()
    if mime not in SUPPORTED_IMAGE_MIME:
        return {
            "status": STATUS_UNSUPPORTED,
            "text": "",
            "figures": [],
            "reason": f"不支持的图片类型 {mime}；可用 {' / '.join(SUPPORTED_IMAGE_MIME)}",
        }
    if not (settings.get("base_url") and settings.get("model")):
        return {
            "status": STATUS_OCR_UNAVAILABLE,
            "text": "",
            "figures": [],
            "reason": capabilities()["image_reason"],
        }
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        return {"status": STATUS_UNSUPPORTED, "text": "", "figures": [], "reason": f"缺 httpx：{exc}"}

    payload = {
        "model": settings["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": VISION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "抽取这张图里的代码、日期与关键数字。"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
                    },
                ],
            },
        ],
    }
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    headers = {"content-type": "application/json"}
    if settings.get("api_key"):
        headers["authorization"] = f"Bearer {settings['api_key']}"
    try:
        with httpx.Client(timeout=settings.get("timeout") or DEFAULT_VISION_TIMEOUT) as client:
            response = client.post(url, headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        return {"status": STATUS_FAILED, "text": "", "figures": [], "reason": f"vision 请求失败：{exc}"}
    if response.status_code >= 400:
        return {
            "status": STATUS_FAILED,
            "text": "",
            "figures": [],
            "reason": f"vision 返回 HTTP {response.status_code}：{response.text[:200]}",
        }
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        return {"status": STATUS_FAILED, "text": "", "figures": [], "reason": f"vision 响应结构异常：{exc}"}
    parsed = _parse_vision_content(content)
    if parsed is None:
        return {
            "status": STATUS_FAILED,
            "text": str(content)[:800],
            "figures": [],
            "reason": "vision 返回的不是约定 JSON",
        }
    return {
        "status": STATUS_OK,
        "text": str(parsed.get("text") or ""),
        "figures": list(parsed.get("figures") or []),
        "codes": [str(code) for code in (parsed.get("codes") or [])],
        "doc_date": str(parsed.get("date") or ""),
        "reason": "",
        "model": settings.get("model"),
    }


def _parse_vision_content(content: Any) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    # 模型偶尔包 ```json 围栏，剥掉再解析
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    try:
        doc = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            doc = json.loads(match.group(0))
        except ValueError:
            return None
    return doc if isinstance(doc, dict) else None


# --------------------------------------------------------------------------- #
# 结构化
# --------------------------------------------------------------------------- #


@dataclass
class Figure:
    field_name: str
    value: float | str
    unit: str = ""
    raw: str = ""
    page: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field_name,
            "label": FIELD_LABELS.get(self.field_name, self.field_name),
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw,
            "page": self.page,
        }


def _scale(unit: str | None) -> float:
    return _CN_UNITS.get(str(unit or "").strip(), 1.0)


def _first(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    return pattern.search(text)


def _strip_thousands(text: str) -> str:
    """去掉数字里的千分位逗号：`1,234.56` → `1234.56`（正则就不必处理逗号）。"""
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)


def parse_figures(text: str, *, vision_figures: list[dict[str, Any]] | None = None) -> list[Figure]:
    """正则抽关键数字；`vision_figures` 里的 label 也按关键词映射到同一套字段。"""
    text = _strip_thousands(text)
    out: list[Figure] = []
    seen: set[str] = set()

    def push(field_name: str, value: float | str, unit: str, raw: str) -> None:
        if field_name in seen:
            return
        seen.add(field_name)
        out.append(Figure(field_name, value, unit, raw))

    code = _first(_CODE_RE, text)
    if code:
        push("code", code.group(1), "", code.group(0))
    pct = _first(_PCT_RE, text)
    if pct:
        push("pct", float(pct.group(1)), "%", pct.group(0))
    price = _first(_PRICE_RE, text)
    if price:
        push("price", float(price.group(1)), "元", price.group(0))
    pe = _first(_PE_RE, text)
    if pe:
        push("pe", float(pe.group(1)), "倍", pe.group(0))
    pb = _first(_PB_RE, text)
    if pb:
        push("pb", float(pb.group(1)), "倍", pb.group(0))
    revenue = _first(_REVENUE_RE, text)
    if revenue:
        push("revenue", float(revenue.group(1)) * _scale(revenue.group(2)), "亿元", revenue.group(0))
    net_profit = _first(_NET_PROFIT_RE, text)
    if net_profit:
        push(
            "net_profit",
            float(net_profit.group(1)) * _scale(net_profit.group(2)),
            "亿元",
            net_profit.group(0),
        )
    eps = _first(_EPS_RE, text)
    if eps:
        push("eps", float(eps.group(1)), "元", eps.group(0))
    roe = _first(_ROE_RE, text)
    if roe:
        push("roe", float(roe.group(1)), "%", roe.group(0))

    for item in vision_figures or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        name = _label_to_field(label)
        if name is None or name in seen:
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        unit = str(item.get("unit") or "")
        if name in {"revenue", "net_profit"}:
            value = value * _scale(unit)
            unit = "亿元"
        push(name, value, unit, f"{label}={item.get('value')}{unit}")
        out[-1].page = int(item.get("page") or 0) or None
    return out


_FIELD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("code", ("代码", "证券代码", "股票代码")),
    ("price", ("收盘价", "最新价", "现价", "股价")),
    ("pct", ("涨跌幅", "涨幅", "跌幅")),
    ("pe", ("市盈率", "pe", "pe_ttm")),
    ("pb", ("市净率", "pb")),
    ("revenue", ("营业收入", "营收", "营业总收入")),
    ("net_profit", ("净利润", "归母净利", "净利")),
    ("eps", ("每股收益", "eps")),
    ("roe", ("净资产收益率", "roe")),
)


def _label_to_field(label: str) -> str | None:
    lowered = label.strip().lower()
    for name, keywords in _FIELD_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def extract_dates(text: str) -> list[str]:
    out: list[str] = []
    for match in _DATE_RE.finditer(text):
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        iso = day.isoformat()
        if iso not in out:
            out.append(iso)
    return out


# --------------------------------------------------------------------------- #
# 对账
# --------------------------------------------------------------------------- #


def _local_rows(dataset: str, code: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """按代码读本地数据（按 trade_date 升序）；读不到就是空列表，不抛。"""
    if not code:
        return []
    try:
        from ladderstock.data import duckdb_store

        return duckdb_store.query_dataset(dataset, code=code, root=root)
    except Exception:  # noqa: BLE001 - 本地库读不动按"无数据"处理，并在结论里写
        return []


def _latest(rows: list[dict[str, Any]], dataset: str) -> dict[str, Any] | None:
    if not rows:
        return None
    key = "report_date" if dataset == "finance_indicators" else "trade_date"
    return sorted(rows, key=lambda row: str(row.get(key) or ""))[-1]


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN 过滤


def _verdict(field_name: str, doc: float, local: float) -> str:
    tolerance = TOLERANCE.get(field_name, 0.05)
    if field_name in {"pct", "roe", "eps"}:
        return "match" if abs(doc - local) <= tolerance else "mismatch"
    if local == 0:
        return "match" if abs(doc) < 1e-9 else "mismatch"
    return "match" if abs(doc - local) / abs(local) <= tolerance else "mismatch"


def reconcile(
    figures: list[Figure],
    *,
    code: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """逐项与本地落盘数据比对。本地缺这个域就 `unverifiable`，并写明缺什么。"""
    doc_code = code
    if not doc_code:
        for figure in figures:
            if figure.field_name == "code":
                doc_code = str(figure.value)
                break

    daily: list[dict[str, Any]] = []
    valuation: list[dict[str, Any]] = []
    finance: list[dict[str, Any]] = []
    if doc_code:
        daily = _local_rows("daily_k", doc_code, root=root)
        valuation = _local_rows("valuation_history", doc_code, root=root)
        finance = _local_rows("finance_indicators", doc_code, root=root)

    rows: list[dict[str, Any]] = []
    comparable = matches = 0
    for figure in figures:
        name = figure.field_name
        if name == "code":
            rows.append(
                {
                    "field": name,
                    "label": FIELD_LABELS[name],
                    "doc_value": figure.value,
                    "local_value": doc_code,
                    "local_source": "输入/图上代码",
                    "verdict": "match" if doc_code else "unverifiable",
                    "note": "" if doc_code else "没抽到代码，无法定位标的",
                }
            )
            continue

        local_value: float | None = None
        local_date = ""
        note = ""
        source = FIELD_SOURCE.get(name, "")
        if not doc_code:
            note = "缺证券代码，无法定位本地数据"
        elif name == "price":
            row = _latest(daily, "daily_k")
            if row:
                local_value = _num(row.get("close"))
                local_date = str(row.get("trade_date") or "")
        elif name == "pct":
            rows_sorted = sorted(daily, key=lambda row: str(row.get("trade_date") or ""))
            if len(rows_sorted) >= 2:
                last = _num(rows_sorted[-1].get("close"))
                prev = _num(rows_sorted[-2].get("close"))
                if last is not None and prev:
                    local_value = (last - prev) / prev * 100
                local_date = str(rows_sorted[-1].get("trade_date") or "")
        elif name in {"pe", "pb"}:
            row = _latest(valuation, "valuation_history")
            if row:
                local_value = _num(row.get(name))
                local_date = str(row.get("trade_date") or "")
        else:
            row = _latest(finance, "finance_indicators")
            if row:
                if name in {"revenue", "net_profit"}:
                    raw = _num(row.get(name))
                    local_value = raw / 1e8 if raw is not None else None
                else:
                    local_value = _num(row.get(name))
                local_date = str(row.get("report_date") or "")[:10]

        if local_value is None:
            verdict = "unverifiable"
            if not note:
                note = (
                    "本地没有该标的的这条数据"
                    if doc_code
                    else "缺证券代码"
                )
        else:
            try:
                doc_value = float(figure.value)
            except (TypeError, ValueError):
                verdict = "unverifiable"
                note = "图上抽到的不是数字"
            else:
                verdict = _verdict(name, doc_value, local_value)
                comparable += 1
                matches += 1 if verdict == "match" else 0

        rows.append(
            {
                "field": name,
                "label": FIELD_LABELS.get(name, name),
                "doc_value": figure.value,
                "doc_unit": figure.unit,
                "doc_raw": figure.raw,
                "local_value": None if local_value is None else round(local_value, 4),
                "local_date": local_date,
                "local_source": source,
                "verdict": verdict,
                "note": note,
            }
        )

    accuracy = (matches / comparable) if comparable else None
    return {
        "code": doc_code,
        "rows": rows,
        "comparable": comparable,
        "matched": matches,
        "mismatched": comparable - matches,
        "unverifiable": len([row for row in rows if row["verdict"] == "unverifiable"]),
        "accuracy": None if accuracy is None else round(accuracy, 4),
        "accuracy_formula": "matched / comparable（只数本地有数据、图上也有数字的项）",
    }


# --------------------------------------------------------------------------- #
# 一步到位 + 落盘
# --------------------------------------------------------------------------- #


def _store_path(day: str, digest: str, root: Path | None = None) -> Path:
    base = root if root is not None else _default_root()
    return base / VERIFY_SUBDIR / day / f"{digest}.json"


def _default_root() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


def verify(
    data: bytes,
    *,
    filename: str,
    mime: str,
    root: Path | None = None,
    code: str | None = None,
    persist: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """一份文件从字节到对账结论。返回 `extract` / `figures` / `reconciled` / `saved`。"""
    digest = hashlib.sha1(data).hexdigest()[:12]
    is_pdf = mime == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        extracted = extract_pdf(data)
    elif mime.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        extracted = extract_image(data, mime if mime.startswith("image/") else "image/png")
    else:
        extracted = {
            "status": STATUS_UNSUPPORTED,
            "text": "",
            "reason": f"不支持的文件类型 {mime or filename}；只收 PDF / PNG / JPEG / WEBP / BMP",
        }

    text = str(extracted.get("text") or "")
    figures = parse_figures(text, vision_figures=extracted.get("figures") or [])
    dates = extract_dates(text)
    reconciled = reconcile(figures, code=code, root=root)

    payload: dict[str, Any] = {
        "ok": extracted["status"] == STATUS_OK,
        "filename": filename,
        "mime": mime,
        "sha1": digest,
        "bytes": len(data),
        "generated_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "extract": {
            "status": extracted["status"],
            "pages": extracted.get("pages"),
            "chars": extracted.get("chars"),
            "reason": extracted.get("reason") or "",
        },
        "dates": dates,
        "figures": [figure.as_dict() for figure in figures],
        "reconciled": reconciled,
        "text_preview": text[:1200],
        "capabilities": capabilities(),
    }
    if persist:
        day = (now or datetime.now()).astimezone().date().isoformat()
        path = _store_path(day, digest, root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            payload["saved"] = str(path)
        except OSError as exc:
            payload["saved"] = None
            payload["save_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def history(root: Path | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
    """最近做过的查证（按落盘文件倒序）。"""
    base = (root if root is not None else _default_root()) / VERIFY_SUBDIR
    if not base.is_dir():
        return []
    files = sorted(base.rglob("*.json"), reverse=True)[: max(1, int(limit))]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            {
                "filename": doc.get("filename"),
                "sha1": doc.get("sha1"),
                "status": (doc.get("extract") or {}).get("status"),
                "figures": len(doc.get("figures") or []),
                "code": (doc.get("reconciled") or {}).get("code"),
                "accuracy": (doc.get("reconciled") or {}).get("accuracy"),
                "generated_at": doc.get("generated_at"),
                "path": str(path),
            }
        )
    return out


__all__ = [
    "FIELD_LABELS",
    "FIELD_ORDER",
    "Figure",
    "MIN_TEXT_CHARS",
    "STATUS_FAILED",
    "STATUS_NEEDS_OCR",
    "STATUS_OCR_UNAVAILABLE",
    "STATUS_OK",
    "STATUS_UNSUPPORTED",
    "SUPPORTED_IMAGE_MIME",
    "TOLERANCE",
    "VERIFY_SUBDIR",
    "capabilities",
    "extract_dates",
    "extract_image",
    "extract_pdf",
    "history",
    "parse_figures",
    "reconcile",
    "verify",
]
