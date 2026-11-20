"""公告正文补全：TickHub 给的是 PDF 链接，正文得自己抽。

为什么需要这一层：``cninfo_announcements`` 的返回里只有 ``title`` /
``publish_time`` / ``url``，TickHub 推给 FC 的事件里 ``content.clean_content``
就是标题本身（实测 3145/3174 条 DWD 正文短于 300 字，000001 平均 16 字）。
不补正文，投影进知识库的只是标题碎片，语义检索没有意义。

三条硬口径：

* **不动 FC 的表**：FC 只留 ODS/DWD/DWS 结构化事实，正文补全的结果落 LS 自己的
  ``var/knowledge/fulltext/<symbol>.jsonl``，投影时优先用它覆盖短正文。
* **可复跑**：同一 (公告日期, 标题) 已有正文就跳过，不重复下载；抓失败的记进
  ``failed`` 原样返回，不吞掉。
* **限流自觉**：cninfo 静态 PDF 是公共资源，默认 4 并发 + 每次请求间隔，慢一点
  也别把对方站点打挂。
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 低于这个长度就认为"只有标题、没有正文"。标题本身通常 10~60 字，200 字足够
# 把"标题党正文"筛出来，又不至于把"副行长任职资格核准"这种天然短公告误判成缺正文。
MIN_BODY_CHARS = 200
# PDF 抽出来短于这个长度才算失败：很多合规公告（任职核准、债券发行完毕）正文
# 本来就只有 300 多字，按"短就是扫描件"一刀切会误杀。
MIN_EXTRACT_CHARS = 120
DEFAULT_WORKERS = 4
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class FulltextUnavailable(RuntimeError):
    """PDF 源或解析依赖不可用。"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def store_dir() -> Path:
    explicit = (os.environ.get("LDST_FULLTEXT_DIR") or "").strip()
    root = Path(explicit) if explicit else _repo_root() / "var" / "knowledge" / "fulltext"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_path(symbol: str) -> Path:
    return store_dir() / f"{str(symbol).strip()}.jsonl"


def _key(ann_date: str, title: str) -> str:
    return f"{str(ann_date or '').strip()}|{str(title or '').strip()}"


def load(symbol: str) -> dict[str, str]:
    """读已缓存的正文：``{日期|标题: 正文}``。"""

    path = _store_path(symbol)
    if not path.exists():
        return {}
    found: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            text = str(item.get("text") or "")
            if text:
                found[str(item.get("key") or "")] = text
    return found


def _append(symbol: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path = _store_path(symbol)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def pdf_list(symbol: str, *, limit: int = 200, timeout: float = 60.0) -> list[dict[str, str]]:
    """调 ``th-cn announcement list`` 拿公告的 PDF 链接。"""

    from ladderstock.data import collector

    exe = collector.tickhub_exe()
    if exe is None:
        raise FulltextUnavailable(f"找不到 th-cn.exe（{collector.tickhub_home()}）")
    argv = [str(exe), "announcement", "list", str(symbol).strip(), "--limit", str(int(limit))]
    try:
        done = subprocess.run(  # noqa: S603 - 固定可执行文件 + 列表传参
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=collector._tickhub_env(),
            creationflags=collector._create_no_window(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FulltextUnavailable(f"th-cn announcement list 超时：{exc}") from exc
    raw = (done.stdout or "").strip()
    if not raw:
        raise FulltextUnavailable(
            f"th-cn 无输出（exit={done.returncode}）：{(done.stderr or '')[:200]}"
        )
    # CLI 会先打几行日志（redis 降级警告之类），取最后一个 JSON 对象
    start = raw.find("{")
    if start < 0:
        raise FulltextUnavailable(f"th-cn 输出不是 JSON：{raw[:200]}")
    payload = json.loads(raw[start:])
    if not payload.get("ok"):
        raise FulltextUnavailable(f"th-cn 返回失败：{str(payload)[:200]}")
    data = payload.get("data") or {}
    items = data.get("announcements") if isinstance(data, dict) else data
    result: list[dict[str, str]] = []
    for item in items or []:
        url = str(item.get("url") or item.get("pdf") or "").strip()
        if not url:
            continue
        result.append(
            {
                "title": str(item.get("title") or "").strip(),
                "ann_date": str(item.get("publish_time") or "")[:10],
                "url": url,
            }
        )
    return result


def extract(url: str, *, timeout: float = 40.0, max_pages: int = 60) -> str:
    """下载 PDF 并抽纯文本；扫描件（抽不出字）返回空串。"""

    try:
        import httpx
        import pypdf
    except ImportError as exc:  # pragma: no cover - 缺依赖时明确报
        raise FulltextUnavailable(f"缺 PDF 解析依赖（需要 httpx + pypdf）：{exc}") from exc

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                url, headers={"User-Agent": _UA, "Referer": "http://www.cninfo.com.cn/"}
            )
    except Exception as exc:  # noqa: BLE001 - 网络问题按不可用
        raise FulltextUnavailable(f"下载 PDF 失败（{url}）：{exc}") from exc
    if response.status_code >= 400:
        raise FulltextUnavailable(f"下载 PDF 失败（{url}）HTTP {response.status_code}")
    try:
        reader = pypdf.PdfReader(io.BytesIO(response.content))
    except Exception as exc:  # noqa: BLE001 - 损坏/加密 PDF
        raise FulltextUnavailable(f"PDF 解析失败（{url}）：{exc}") from exc

    pages = reader.pages[: max(1, int(max_pages))]
    chunks: list[str] = []
    for page in pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - 单页坏不影响整篇
            continue
    text = "\n".join(chunks)
    # 去掉 PDF 常见的整页页眉重复与多余空行
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def enrich(
    symbol: str,
    *,
    rows: list[Any] | None = None,
    limit: int = 200,
    workers: int = DEFAULT_WORKERS,
    pause: float = 0.15,
) -> dict[str, Any]:
    """把一只票的短正文公告补成全文（落本地 jsonl）。

    ``rows`` 传 ``AnnouncementRow`` 列表可跳过重复查库；不传则自行读 DWD。
    返回 ``{symbol, needed, fetched, chars, skipped, failed}``。
    """

    from ladderstock.knowledge import announcements

    if rows is None:
        rows = announcements.fetch_announcements(symbol, limit=limit)
    short = [row for row in rows if int(row.content_chars or 0) < MIN_BODY_CHARS]
    cached = load(symbol)
    todo = [row for row in short if _key(row.ann_date, row.title) not in cached]
    if not todo:
        return {
            "symbol": symbol,
            "needed": len(short),
            "fetched": 0,
            "chars": 0,
            "skipped": len(short),
            "failed": [],
        }

    links: dict[str, str] = {}
    try:
        # 列表要多取一些：DWD 里那批公告未必正好是 cninfo 最新 N 条（采集按类别抓过），
        # 只按行数取会漏掉"排在后面但 DWD 里有"的那几条，匹配率直接掉一半。
        for item in pdf_list(symbol, limit=max(int(limit) * 3, 80)):
            links[_key(item.get("ann_date", ""), item.get("title", ""))] = item["url"]
    except FulltextUnavailable as exc:
        return {
            "symbol": symbol,
            "needed": len(short),
            "fetched": 0,
            "chars": 0,
            "skipped": 0,
            "failed": [{"symbol": symbol, "error": f"取 PDF 链接失败：{exc}"}],
        }

    lock = threading.Lock()
    records: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def one(row: Any) -> None:
        key = _key(row.ann_date, row.title)
        url = links.get(key)
        if not url:
            with lock:
                failed.append(
                    {"symbol": symbol, "title": row.title, "error": "PDF 列表里没有对应条目"}
                )
            return
        try:
            text = extract(url)
        except FulltextUnavailable as exc:
            with lock:
                failed.append({"symbol": symbol, "title": row.title, "error": str(exc)})
            return
        if len(text) < MIN_EXTRACT_CHARS:
            with lock:
                failed.append(
                    {
                        "symbol": symbol,
                        "title": row.title,
                        "error": f"抽出的正文只有 {len(text)} 字（疑似扫描件）",
                    }
                )
            return
        with lock:
            records.append(
                {"key": key, "dwd_id": row.id, "url": url, "chars": len(text), "text": text}
            )

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        list(pool.map(one, todo))
        time.sleep(pause)
    _append(symbol, records)
    logger.info(
        "announcement_fulltext symbol=%s needed=%s fetched=%s chars=%s failed=%s",
        symbol,
        len(todo),
        len(records),
        sum(r["chars"] for r in records),
        len(failed),
    )
    return {
        "symbol": symbol,
        "needed": len(todo),
        "fetched": len(records),
        "chars": sum(r["chars"] for r in records),
        "skipped": len(short) - len(todo),
        "failed": failed,
    }


def enrich_many(
    symbols: list[str], *, limit: int = 200, workers: int = 3, progress: Any = None
) -> dict[str, Any]:
    """批量补正文；单只失败记进 failed，不拖垮整批。"""

    wanted: list[str] = []
    seen: set[str] = set()
    for item in symbols or []:
        code = str(item or "").strip()
        if code and code not in seen:
            seen.add(code)
            wanted.append(code)

    def one(code: str) -> dict[str, Any]:
        try:
            info = enrich(code, limit=limit, workers=DEFAULT_WORKERS)
        except Exception as exc:  # noqa: BLE001 - 单只异常要能被记下来
            logger.warning("announcement_fulltext_failed symbol=%s detail=%s", code, exc)
            info = {
                "symbol": code,
                "fetched": 0,
                "chars": 0,
                "failed": [{"symbol": code, "error": str(exc)}],
            }
        if progress is not None:
            try:
                progress(info)
            except Exception:  # noqa: BLE001
                pass
        return info

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        results = list(pool.map(one, wanted))

    errors = [err for info in results for err in info.get("failed") or []]
    return {
        "ok": not errors,
        "symbols": len(wanted),
        "fetched": sum(int(info.get("fetched") or 0) for info in results),
        "chars": sum(int(info.get("chars") or 0) for info in results),
        "failed": errors[:50],
        "failed_count": len(errors),
    }


__all__ = [
    "DEFAULT_WORKERS",
    "FulltextUnavailable",
    "MIN_BODY_CHARS",
    "enrich",
    "enrich_many",
    "extract",
    "load",
    "pdf_list",
    "store_dir",
]
