"""本地覆盖判定层（F1）。

回答一个问题：回答这只票所需的数据，本地库到底有没有、够不够新。

口径说明：

* **不读 ``ods.etl_status``**。ETL 落 DWD 后不回写该字段（实测 600000 的 24 行
  在 DWD 已落库完成后仍为 ``pending``），拿它判覆盖会把"已到货"误判成"没到货"。
* 判据落在 DWD/DWS 的实际行与日期上：宽表有行 → 财报可直接答；只有明细 → 只需
  重建宽表（单票 build 实测 0.88 秒），不必重新采集。
* 判定顺序固定 DWS → DWD。宽表是唯一能直接回答"最近一期营收多少"的状态，
  先查它可以把最常见的一跳省掉。

失败语义：连不上库一律抛 :class:`CoverageUnavailable`，由调用方决定降级
（回到"直接查线上"或"如实说缺口"），不在这里静默返回空字典——那样会让上层把
"库挂了"当成"数据没有"，进而触发一次无意义的采集。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: 财报新鲜度：最近一期报告日距今天超过这个天数即认为需要复查。
#: 130 天覆盖"A 股季报最长的披露间隔"（年报 12-31 → 一季报 3-31 约 90 天，
#: 加披露窗与节假日缓冲），再长就说明这一期确实没进来。
FINANCIAL_FRESH_DAYS = 130

#: 行情新鲜度：超过 3 个自然日视为 stale（含周末，正常交易日不会破这条）。
QUOTE_STALE_DAYS = 3

#: 公告默认观察窗口（天）。
ANNOUNCEMENT_WINDOW_DAYS = 30

KIND_FINANCIAL = "financial"
KIND_QUOTE = "quote"
KIND_ANNOUNCEMENT = "announcement"
KIND_KLINE = "kline"

KINDS: tuple[str, ...] = (KIND_FINANCIAL, KIND_QUOTE, KIND_ANNOUNCEMENT, KIND_KLINE)

#: 状态机取值。
STATE_READY = "ready"
STATE_STALE = "stale"
STATE_MISSING = "missing"

#: 原因码。
REASON_NO_ROW = "no_row"
REASON_NO_WIDE_ROW = "no_wide_row"
REASON_BUILD_FAILED = "build_failed"
REASON_OUT_OF_WINDOW = "out_of_window"

_CODE_RE = re.compile(r"(\d{6})")


class CoverageUnavailable(RuntimeError):
    """FC 的结构化库不可用：调用方应降级为线上取数或如实报缺口。"""


def normalize_symbol(symbol: str | None) -> str:
    """抽出 6 位 A 股代码；抽不出就原样返回（美股 ticker 等由调用方自己处理）。"""

    raw = str(symbol or "").strip().upper()
    match = _CODE_RE.search(raw)
    if match:
        return match.group(1)
    return raw


def _connect() -> Any:
    """连 FC 的结构化库。

    不直接用 ``knowledge.announcements._connect``：它的端口来自「基础端口 + 端口
    偏移」，在没有 ``LDST_PORT_OFFSET`` 的进程里会算成 5432，而 FC 的 PostgreSQL
    固定跑在 15432——正式服 5950 的启动口径恰好就是这种（没有偏移），照那条路径
    连会一直失败。这里按实际监听端口探测一次。
    """

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - 依赖缺失时由上层降级
        raise CoverageUnavailable(f"psycopg 未安装，无法直读 FC 的结构化库：{exc}") from exc

    explicit = (os.environ.get("LDST_FC_PG_DSN") or "").strip()
    if explicit:
        dsn = explicit
    else:
        from ladderstock.data.collector import fc_pg_port

        user = (os.environ.get("LDST_FC_PG_USER") or "fc").strip() or "fc"
        name = (os.environ.get("LDST_FC_PG_DB") or "financecoffer").strip() or "financecoffer"
        dsn = f"postgresql://{user}@127.0.0.1:{fc_pg_port()}/{name}"

    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn[len("postgresql+psycopg://"):]
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - 网络/信任配置失败都算库不可用
        raise CoverageUnavailable(f"连不上 FC 的 PostgreSQL：{exc}") from exc


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None


def _newest_financial_date(metrics: Any) -> date | None:
    """从宽表 ``metrics_json.years`` 里挑最新的、且确实有数的报告日。"""

    if not isinstance(metrics, dict):
        return None
    years = metrics.get("years")
    if not isinstance(years, list):
        return None
    newest: date | None = None
    for item in years:
        if not isinstance(item, dict):
            continue
        if str(item.get("data_status") or "").lower() != "present":
            continue
        parsed = _parse_date(item.get("report_date"))
        if parsed and (newest is None or parsed > newest):
            newest = parsed
    return newest


def _age_days(as_of: date | None, today: date) -> int | None:
    if as_of is None:
        return None
    return max(0, (today - as_of).days)


def _financial_state(cur: Any, symbol: str, today: date) -> dict[str, Any]:
    try:
        cur.execute(
            "SELECT as_of, metrics_json, built_at FROM dws.stock_agent_analysis_wide"
            " WHERE symbol = %s ORDER BY built_at DESC LIMIT 1",
            (symbol,),
        )
        wide = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - 表缺失/权限问题都算库不可用
        raise CoverageUnavailable(f"读 dws.stock_agent_analysis_wide 失败：{exc}") from exc

    if wide:
        as_of = _parse_date(wide[0])
        newest = _newest_financial_date(wide[1]) or as_of
        age = _age_days(newest, today)
        state = STATE_READY if (age is not None and age <= FINANCIAL_FRESH_DAYS) else STATE_STALE
        return {
            "state": state,
            "source": "dws",
            "as_of": newest.isoformat() if newest else None,
            "age_days": age,
            "reason": None if state == STATE_READY else REASON_OUT_OF_WINDOW,
        }

    try:
        cur.execute(
            "SELECT max(report_date), count(*) FROM dwd.stock_financial_snapshot"
            " WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise CoverageUnavailable(f"读 dwd.stock_financial_snapshot 失败：{exc}") from exc

    latest = _parse_date(row[0]) if row else None
    if latest:
        return {
            "state": STATE_STALE,
            "source": "dwd",
            "as_of": latest.isoformat(),
            "age_days": _age_days(latest, today),
            "reason": REASON_NO_WIDE_ROW,
        }
    return {
        "state": STATE_MISSING,
        "source": None,
        "as_of": None,
        "age_days": None,
        "reason": REASON_NO_ROW,
    }


def _max_date_state(
    cur: Any, table: str, symbol: str, today: date, stale_days: int
) -> dict[str, Any]:
    try:
        cur.execute(f"SELECT max(trade_date) FROM {table} WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise CoverageUnavailable(f"读 {table} 失败：{exc}") from exc

    latest = _parse_date(row[0]) if row else None
    if not latest:
        return {
            "state": STATE_MISSING,
            "source": None,
            "as_of": None,
            "age_days": None,
            "reason": REASON_NO_ROW,
        }
    age = _age_days(latest, today)
    state = STATE_READY if (age is not None and age <= stale_days) else STATE_STALE
    return {
        "state": state,
        "source": table.split(".")[-1],
        "as_of": latest.isoformat(),
        "age_days": age,
        "reason": None if state == STATE_READY else REASON_OUT_OF_WINDOW,
    }


def _announcement_state(
    cur: Any, symbol: str, today: date, days: int
) -> dict[str, Any]:
    since = today - timedelta(days=max(1, int(days)))
    try:
        cur.execute(
            "SELECT count(*), max(ann_date) FROM dwd.stock_announcement"
            " WHERE symbol = %s AND clean_content IS NOT NULL"
            " AND btrim(clean_content) <> '' AND ann_date >= %s",
            (symbol, since),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise CoverageUnavailable(f"读 dwd.stock_announcement 失败：{exc}") from exc

    count = int(row[0] or 0) if row else 0
    latest = _parse_date(row[1]) if row else None
    if count <= 0:
        return {
            "state": STATE_MISSING,
            "source": None,
            "as_of": None,
            "age_days": None,
            "rows": 0,
            "reason": REASON_OUT_OF_WINDOW,
        }
    return {
        "state": STATE_READY,
        "source": "stock_announcement",
        "as_of": latest.isoformat() if latest else None,
        "age_days": _age_days(latest, today),
        "rows": count,
        "reason": None,
    }


def check_coverage(
    symbol: str,
    kinds: list[str] | None = None,
    *,
    days: int = ANNOUNCEMENT_WINDOW_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """判定一只票各类数据的本地覆盖。

    :param symbol: 股票代码（6 位或带前缀均可，内部归一）
    :param kinds: 要判定的种类，默认全部四类
    :param days: 公告观察窗口
    :param today: 注入"今天"，便于单测固定时钟
    :raises CoverageUnavailable: FC 结构化库连不上或被拒
    """

    clean = normalize_symbol(symbol)
    if not clean:
        raise ValueError("symbol 不能为空")
    wanted = [str(k).strip().lower() for k in (kinds or KINDS) if str(k).strip()]
    unknown = [k for k in wanted if k not in KINDS]
    if unknown:
        raise ValueError(f"未知数据种类：{', '.join(unknown)}（可选 {', '.join(KINDS)}）")

    now = today or date.today()
    result: dict[str, Any] = {}

    with _connect() as conn:
        with conn.cursor() as cur:
            for kind in wanted:
                if kind == KIND_FINANCIAL:
                    result[kind] = _financial_state(cur, clean, now)
                elif kind == KIND_QUOTE:
                    result[kind] = _max_date_state(
                        cur, "dwd.stock_quote_snapshot", clean, now, QUOTE_STALE_DAYS
                    )
                elif kind == KIND_KLINE:
                    result[kind] = _max_date_state(
                        cur, "dwd.stock_kline", clean, now, QUOTE_STALE_DAYS
                    )
                elif kind == KIND_ANNOUNCEMENT:
                    result[kind] = _announcement_state(cur, clean, now, days)

    ready = [k for k, v in result.items() if v["state"] == STATE_READY]
    missing = [k for k, v in result.items() if v["state"] != STATE_READY]
    return {
        "symbol": clean,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "kinds": result,
        "ready": ready,
        "missing": missing,
        "complete": not missing,
    }


def expand_kinds(kinds: list[str] | None) -> list[str]:
    """按依赖补齐种类：财报要有行情快照才能算出 PE/PB。

    实测 600000 的宽表 ``pe/pb/roe`` 全是 null，原因写的是 ``no_price`` /
    ``no_equity``——只补财报不补行情，答出来仍然只有营收和净利。所以这里把
    ``quote`` 作为 ``financial`` 的硬依赖一起请求，而不是让调用方自己记得。
    """

    wanted = [str(k).strip().lower() for k in (kinds or KINDS) if str(k).strip()]
    out: list[str] = []
    for kind in wanted:
        if kind not in out:
            out.append(kind)
        if kind in (KIND_FINANCIAL, KIND_KLINE) and KIND_QUOTE not in out:
            out.append(KIND_QUOTE)
    for kind in KINDS:
        if kind in wanted and kind not in out:
            out.append(kind)
    return out
