"""板块解析与成分股（把"板块榜"接到"东财成分股"）。

TickHub 的两组板块接口**不同构**，这是实测出来的硬事实：

* 板块榜（``industry_boards`` / ``concept_boards`` / ``board_fund_flow``）返回的
  ``board_code`` 是新浪口径的 ``new_jxhy`` / ``gn_hwqc``；
* 成分股（``board_members``）只认东财的 ``BK0475``，喂 ``new_ysjs`` 会直接
  返回 ``QFSD-9001 invalid board code``。

能接起来的桥是 ``stock_concept_blocks``：它给一只票的**全部归属**，每条都带
``name``（和榜上的板块名同字）+ 东财 ``code``（``BKxxxx``）。所以"按名字找码"
这条路是通的——取板块的领涨股去查归属，在同名条目上取 BK 码。

这里不做全市场遍历：一次只解析被点名的那几个板块，成本可控。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ladderstock.data import collector

logger = logging.getLogger(__name__)

BOARD_CODE_RE = re.compile(r"^BK\d{4}$")
_CODE_PREFIX_RE = re.compile(r"^(sh|sz|bj)", re.IGNORECASE)

BOARD_KINDS = {"industry": "industry_boards", "concept": "concept_boards"}

#: 板块码表：东财板块名 → {code, change_pct, lead_stock}。
#: 建一次要 100+ 次上游调用（8 线程约 15 秒），所以落盘缓存，6 小时内复用。
CODEBOOK_FILENAME = "sector_codes.json"
CODEBOOK_TTL_SECONDS = 6 * 3600
CODEBOOK_SAMPLE_KINDS = ("industry", "concept")

#: 榜单噪音板块：涨跌幅高但不是"行业/题材"，答"哪些板块值得跟踪"时先剔掉。
#: 实测码表里涨幅第一的是「昨日连板_含一字」，第二是「ST股」——这些是状态榜，
#: 混进行业结论里就等于答错题。
NOISE_BOARD_MARKERS: tuple[str, ...] = (
    "昨日",
    "今日",
    "连板",
    "涨停",
    "跌停",
    "ST股",
    "破净",
    "融资融券",
    "HS300",
    "上证50",
    "中证",
    "沪深",
    "深证",
    "创业板综",
    "证金持股",
    "机构重仓",
)


def is_noise_board(name: str) -> bool:
    text = str(name or "").upper()
    return any(marker.upper() in text for marker in NOISE_BOARD_MARKERS)


class SectorError(RuntimeError):
    """板块解析失败；带上可读原因，让上层如实说缺口。"""


def _call(tool: str, params: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    exe = collector.tickhub_exe()
    if exe is None:
        raise SectorError(f"找不到 th-cn.exe（{collector.tickhub_home()}）")

    cmd = [str(exe), "tool", "call", tool, "--params", json.dumps(params, ensure_ascii=False)]
    proc = collector._run(cmd, env=collector._tickhub_env(), timeout=timeout)
    text = proc.stdout or ""
    start = text.find("{")
    if start < 0:
        raise SectorError(f"{tool} 没有返回 JSON（exit={proc.returncode}）")
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        raise SectorError(f"{tool} 返回不是合法 JSON：{exc}") from exc

    if not payload.get("ok"):
        error = payload.get("error") or {}
        raise SectorError(
            f"{tool} 失败（{error.get('code') or 'unknown'}）：{error.get('message') or ''}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {"items": data} if isinstance(data, list) else {}
    data["_meta"] = payload.get("meta") or {}
    return data


def _bare_code(value: Any) -> str:
    text = str(value or "").strip()
    return _CODE_PREFIX_RE.sub("", text)


def board_list(kind: str = "industry", *, limit: int = 12) -> list[dict[str, Any]]:
    """取板块榜前 N，**带真实领涨股代码**。

    这里刻意用 ``board_fund_flow`` 而不是 ``industry_boards``：后者给的
    ``leader_code`` 实测错位（值是涨跌额、``amount`` 才是代码），而且和
    ``board_fund_flow`` 的板块名集合对不上（同一个"电力行业"在两边都有，但
    排序不同，按名字取提示票会取空）。``board_fund_flow`` 的
    ``leader_code`` 是 ``sh600111`` 这种真代码，正好当"名字→BK 码"的桥。
    """

    scope = "industry" if str(kind).strip().lower() == "industry" else "concept"
    data = _call(
        "board_fund_flow", {"board_type": scope, "page": 1, "page_size": max(1, int(limit))}
    )
    rows: list[dict[str, Any]] = []
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "board_code": str(row.get("code") or ""),
                "board_name": str(row.get("name") or ""),
                "change_pct": row.get("change_pct"),
                "main_net": row.get("main_net"),
                "main_net_pct": row.get("main_net_pct"),
                "leader_name": row.get("leader_name"),
                "leader_code": _bare_code(row.get("leader_code")),
                "member_count": None,
            }
        )
    return rows


def leader_codes(kind: str = "industry", *, limit: int = 12) -> dict[str, str]:
    """板块名 → 领涨股裸代码。"""

    return {
        row["board_name"]: row["leader_code"]
        for row in board_list(kind, limit=limit)
        if row.get("board_name") and row.get("leader_code")
    }


def resolve_board_code(
    board_name: str,
    *,
    hint_code: str | None = None,
    leader_name: str | None = None,
    board_change_pct: float | None = None,
    allow_fuzzy: bool = False,
) -> tuple[str | None, str]:
    """板块名 → 东财 BK 码。返回 ``(code, matched_by)``。

    ``matched_by`` 说明这条映射是怎么来的，让上层能标可信度：

    * ``exact``：领涨股的归属里有**同名**板块，最可信；
    * ``leader``：名字对不上，但那块板的 ``lead_stock`` 正是榜上的领涨股，
      且**涨跌幅也对得上**；
    * ``None``：解析不到。

    ``leader`` 兜底默认关闭（``allow_fuzzy=False``），因为它会错配：实测
    "电力行业"的领涨股华能蒙电同时在好几块板里当 ``lead_stock``，加了
    0.5 个点的涨幅容差仍会挑到交通运输（BK1670），成分股全错。宁可给
    「成分股暂不可解析」这个真实缺口，也不给一份看着像、其实错的名单。
    """

    name = str(board_name or "").strip()
    if not name:
        return None, "empty"
    if BOARD_CODE_RE.match(name):
        return name, "exact"

    code = _bare_code(hint_code)
    if not code:
        return None, "no_hint"

    try:
        data = _call("stock_concept_blocks", {"code": code})
    except SectorError as exc:
        logger.info("sector_resolve_hint_failed name=%s hint=%s detail=%s", name, code, exc)
        return None, "call_failed"

    boards = [b for b in (data.get("boards") or []) if isinstance(b, dict)]
    for board in boards:
        if str(board.get("name") or "").strip() == name:
            candidate = str(board.get("code") or "")
            if BOARD_CODE_RE.match(candidate):
                return candidate, "exact"

    if not allow_fuzzy:
        return None, "unresolved"

    target_leader = str(leader_name or "").strip()
    if not target_leader:
        return None, "unresolved"

    best: tuple[float, str] | None = None
    for board in boards:
        if str(board.get("lead_stock") or "").strip() != target_leader:
            continue
        candidate = str(board.get("code") or "")
        if not BOARD_CODE_RE.match(candidate):
            continue
        try:
            diff = abs(float(board.get("change_pct")) - float(board_change_pct))
        except (TypeError, ValueError):
            diff = 0.0
        if board_change_pct is not None and diff > 0.5:
            continue
        if best is None or diff < best[0]:
            best = (diff, candidate)

    if best is not None:
        return best[1], "leader"
    return None, "unresolved"


def board_members(board_code: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """东财板块成分股。``board_code`` 必须是 ``BKxxxx``。"""

    code = str(board_code or "").strip().upper()
    if not BOARD_CODE_RE.match(code):
        raise SectorError(f"成分股接口只认东财板块码（BKxxxx），收到：{board_code}")

    data = _call("board_members", {"board_code": code, "page": 1, "page_size": max(1, int(limit))})
    rows: list[dict[str, Any]] = []
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "code": _bare_code(row.get("code")),
                "name": row.get("name"),
                "change_pct": row.get("change_pct"),
                "price": row.get("price"),
                "turnover_rate": row.get("turnover_rate"),
                "pe": row.get("pe"),
                "pb": row.get("pb"),
                "total_market_cap": row.get("total_market_cap"),
                "amount": row.get("amount"),
            }
        )
    return rows


def codebook_path() -> "Any":
    from pathlib import Path

    root = Path(collector._repo_root())
    return root / "var" / CODEBOOK_FILENAME


def load_codebook() -> dict[str, Any]:
    """读磁盘码表。文件不存在 / 坏了 / 过期都当"没有"，不抛异常。"""

    import time

    path = codebook_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    built_at = payload.get("built_at_ts") or 0
    if time.time() - float(built_at) > CODEBOOK_TTL_SECONDS:
        return {}
    boards = payload.get("boards")
    return boards if isinstance(boards, dict) else {}


def _sample_leader_codes(kind: str, *, limit: int = 100) -> list[tuple[str, str, str]]:
    rows = board_list(kind, limit=limit)
    scope = "industry" if str(kind).strip().lower() == "industry" else "concept"
    return [(r["board_name"], r["leader_code"], scope) for r in rows if r.get("leader_code")]


def build_codebook(
    kinds: tuple[str, ...] = CODEBOOK_SAMPLE_KINDS,
    *,
    limit: int = 100,
    workers: int = 8,
) -> dict[str, Any]:
    """用各板块的领涨股反查归属，聚合出「东财板块名 → BK 码」。

    为什么用领涨股而不是全市场遍历：每个板块榜的领涨股一定是该板块成员，
    48 个行业 + 100 个概念 = 148 次调用就够覆盖绝大部分板块，8 线程约 15 秒。
    聚合出来的板块名是**东财口径**（和 ``board_members`` 同源），
    从此不再受"新浪行业名 ≠ 东财行业名"的困扰。
    """

    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    leads: list[tuple[str, str, str]] = []
    for kind in kinds:
        leads.extend(_sample_leader_codes(kind, limit=limit))

    # 同一只领涨股可能被多个板块引用，去重后只查一次
    unique: dict[str, set[str]] = {}
    for _name, code, scope in leads:
        unique.setdefault(code, set()).add(scope)
    codes = sorted(unique)

    def one(code: str) -> tuple[list[dict[str, Any]], set[str]]:
        try:
            data = _call("stock_concept_blocks", {"code": code}, timeout=20.0)
        except SectorError as exc:
            logger.info("codebook_probe_failed code=%s detail=%s", code, exc)
            return [], unique.get(code, set())
        return [b for b in (data.get("boards") or []) if isinstance(b, dict)], unique.get(code, set())

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        results = list(pool.map(one, codes))

    boards: dict[str, dict[str, Any]] = {}
    for group, scopes in results:
        for board in group:
            name = str(board.get("name") or "").strip()
            code = str(board.get("code") or "").strip()
            if not name or not BOARD_CODE_RE.match(code):
                continue
            entry = boards.get(name)
            if entry is None:
                boards[name] = {
                    "code": code,
                    "change_pct": board.get("change_pct"),
                    "lead_stock": board.get("lead_stock"),
                    "votes": 1,
                    "kinds": sorted(scopes),
                }
            else:
                entry["votes"] = int(entry.get("votes") or 1) + 1
                entry["kinds"] = sorted(set(entry.get("kinds") or []) | set(scopes))
                # 涨幅以票数多的那次为准（不同票看到的板块涨跌幅应当一致）
                if board.get("change_pct") is not None:
                    entry["change_pct"] = board.get("change_pct")

    payload = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "built_at_ts": time.time(),
        "samples": len(codes),
        "boards": boards,
    }
    path = codebook_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{path}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        import os

        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("codebook_write_failed detail=%s", exc)
    return boards


def board_code_map(*, refresh: bool = False, allow_build: bool = True) -> dict[str, Any]:
    """取码表：先读盘，没有就现建（只在 allow_build 时）。"""

    cached = {} if refresh else load_codebook()
    if cached:
        return cached
    if not allow_build:
        return {}
    return build_codebook()


def covered_symbols(symbols: list[str] | None = None) -> dict[str, str]:

    try:
        from ladderstock.data import coverage
    except Exception:  # noqa: BLE001
        return {}

    try:
        with coverage._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, max(as_of) FROM dws.stock_agent_analysis_wide GROUP BY symbol"
                )
                rows = cur.fetchall() or []
    except Exception as exc:  # noqa: BLE001 - 库不可用只影响标注，不影响板块本身
        logger.info("sector_coverage_unavailable detail=%s", exc)
        return {}

    out = {str(r[0]): str(r[1])[:10] for r in rows if r and r[0]}
    if symbols:
        wanted = {str(s).strip() for s in symbols}
        out = {k: v for k, v in out.items() if k in wanted}
    return out


def sector_digest(
    kind: str = "industry",
    *,
    limit_boards: int = 5,
    members_per_board: int = 12,
    refresh_book: bool = False,
) -> dict[str, Any]:
    """一个板块一张卡：涨幅 + 领涨股 + 成分股 + 其中"我方有财报"的票。

    这是"这个板块最近有什么值得跟踪"的直接答案源：板块给谁强，成分股给谁在动，
    本地覆盖给"哪些票查得到基本面"，三者都是工具给的数，没有一处是估的。
    """

    scope = "industry" if str(kind).strip().lower() == "industry" else "concept"
    covered = covered_symbols()

    # 首选码表：板块名本身就是东财口径，和成分股同源，不存在"按名对不上"
    book = board_code_map(refresh=refresh_book)
    candidates: list[dict[str, Any]] = []
    for name, entry in book.items():
        # 码表没有"行业/概念"的可信标签：kinds 记录的是样本来源（某行业榜的领涨股
        # 恰好也挂在概念板块上），拿它过滤会把「ST股」「广东板块」当成行业。
        # 所以只按"是不是状态榜噪音"筛，不给假分类。
        if is_noise_board(name):
            continue
        candidates.append(
            {
                "board_name": name,
                "board_code_board": entry.get("code"),
                "board_code_resolved": entry.get("code"),
                "matched_by": "codebook",
                "change_pct": entry.get("change_pct"),
                # 码表是逐票归属聚合出来的，没有全市场资金流；资金流走 board_rank
                "main_net": None,
                "main_net_pct": None,
                "member_count": None,
                "leader_name": entry.get("lead_stock"),
                "leader_code": None,
            }
        )

    if not candidates:
        # 码表还没建（首次冷启动且不允许现建）：退回旧路径，如实标缺口
        for board in board_list(kind, limit=limit_boards):
            resolved, matched_by = None, "unresolved"
            try:
                resolved, matched_by = resolve_board_code(
                    board["board_name"],
                    hint_code=board.get("leader_code"),
                    leader_name=board.get("leader_name"),
                    board_change_pct=board.get("change_pct"),
                )
            except SectorError as exc:
                logger.info("sector_resolve_failed name=%s detail=%s", board["board_name"], exc)
                matched_by = "call_failed"
            candidates.append({**board, "board_code_resolved": resolved, "matched_by": matched_by})

    # 涨得好的排前面；缺值的沉底
    candidates.sort(
        key=lambda r: (r.get("change_pct") is None, -(r.get("change_pct") or 0))
    )
    candidates = candidates[: max(1, int(limit_boards))]

    out: list[dict[str, Any]] = []
    for board in candidates:
        name = board["board_name"]
        resolved = board.get("board_code_resolved")

        members: list[dict[str, Any]] = []
        if resolved:
            try:
                members = board_members(resolved, limit=members_per_board)
            except SectorError as exc:
                logger.info("sector_members_failed board=%s detail=%s", resolved, exc)

        # 按涨幅降序，把真正在动的放前面；缺值的沉底
        members.sort(key=lambda r: (r.get("change_pct") is None, -(r.get("change_pct") or 0)))
        for row in members:
            row["has_fundamentals"] = row["code"] in covered
            row["fundamentals_as_of"] = covered.get(row["code"])

        out.append(
            {
                "board_name": name,
                "board_code_board": board.get("board_code_board") or board.get("board_code"),
                "board_code_resolved": resolved,
                "matched_by": board.get("matched_by"),
                "change_pct": board.get("change_pct"),
                "main_net": board.get("main_net"),
                "main_net_pct": board.get("main_net_pct"),
                "member_count": board.get("member_count"),
                "leader_name": board.get("leader_name"),
                "leader_code": board.get("leader_code"),
                "members": members,
                "members_covered": [r["code"] for r in members if r.get("has_fundamentals")],
            }
        )

    from_codebook = any(b.get("matched_by") == "codebook" for b in out)
    return {
        "kind": scope,
        "source": "codebook" if from_codebook else "board_rank",
        "boards": out,
        "covered_total": len(covered),
        "notes": (
            ["板块与成分股同出东财码表（board_members 同源），板块名为东财口径。"]
            if from_codebook
            else [
                "板块码表尚未建立，退回新浪口径板块榜：只有同名板块能解析出成分股，"
                "其余如实留空。运行 build_codebook 可消除这个缺口。"
            ]
        ),
    }
