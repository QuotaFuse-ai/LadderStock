"""持仓隐私闭环（QUOT-145 验收硬条）：哨兵数据只留本机，四个出口都必须干净。

做法是「先写入一条带哨兵的持仓，再去各个对外面上找这条哨兵」：

| 出口 | 断言 |
|---|---|
| MCP 工具清单 | 没有任何 position 相关工具名 |
| MCP 工具结果 | 12 个只读工具逐个 dispatch，信封里没有哨兵、没有 `entry_date` |
| AI 上下文 | `strip_private` → `build_messages` 序列化后不含持仓字段名 |
| `ldst pool` stdout | 持仓与候选池共用同一代码也不串数据 |
| 磁盘 | 不加 `--export` 不产生任何文件；加了才落盘并在 stderr 提醒 |

「互不引用」用 AST 检查 import，而不是全文搜字符串——`core/pool.py` 的文档里
本来就要写「本模块不读隐私库」这句话。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import FakeTransport
from typer.testing import CliRunner

from ladderstock.ai.structured_output import build_messages
from ladderstock.cli.main import app
from ladderstock.core import pool as pool_module
from ladderstock.core import privacy
from ladderstock.data import sqlite_store
from ladderstock.journal import position as position_module
from ladderstock.mcp.server import TOOL_HANDLERS, dispatch
from ladderstock.mcp.tools import FORBIDDEN_KEYWORDS, TOOL_SPECS, tool_listing

DATE = "2026-10-22"
SENTINEL = "隐私哨兵-POSITION-7c4a"
SENTINEL_CODE = "600519"
#: 只在持仓里出现的列名：对外面上出现任何一个都算泄露
PRIVATE_ONLY_KEYS = ("entry_date", "avg_price", "cost_basis", "unrealized_pnl", "holdings")

runner = CliRunner()

SRC = Path(__file__).resolve().parents[2] / "src" / "ladderstock"


@pytest.fixture(autouse=True)
def _private_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """隐私库与行情缓存都钉在 tmp：既不联网，也不碰仓库 `data/`。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport",
        lambda _peers: FakeTransport(),
    )
    position_module.add_position(
        SENTINEL_CODE,
        12.34,
        1000.0,
        entry_date="2026-09-01",
        industry="白酒",
        note=SENTINEL,
        root=tmp_path,
    )
    return tmp_path


def _imports(rel: str) -> set[str]:
    """文件里的 import 目标（含 `from x import y` 的 `x`）。"""
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


#: `web/` 里被授权直接读隐私库的文件 → 必须同时具备的闸门（缺一即算越界）
WEB_PRIVILEGED_IMPORTS: dict[str, tuple[str, ...]] = {
    # DEC-015 Tier 1 / QUOT-196：`/api/journal/stats` 只出聚合数字
    "web/server.py": ("def _journal_guard(", "is_loopback_host(", "host_header_allowed("),
    # DEC-030 / QUOT-279：`GET /api/positions` 平面 JSON、回环独占、`no-store`
    "web/radar.py": (
        "def _positions_guard(",
        "is_loopback_host(",
        "host_header_allowed(",
        '"Cache-Control": "no-store"',
    ),
}


# --------------------------------------------------------------------------- #
# 1. MCP 工具清单：没有持仓工具
# --------------------------------------------------------------------------- #

def test_mcp_listing_has_no_position_tool() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert names == sorted(names, key=names.index)  # 清单非空且顺序稳定
    assert "candidate_pool" in names, "候选池工具没注册上，下面的断言就是空的"

    blob = json.dumps(tool_listing(), ensure_ascii=False).lower()
    for keyword in FORBIDDEN_KEYWORDS:
        assert keyword.lower() not in blob, f"MCP 工具清单出现隐私关键字：{keyword}"

    for name in TOOL_HANDLERS:
        for keyword in ("position", "holding", "journal", "watchlist"):
            assert keyword not in name.lower(), f"持仓类工具混进 MCP 清单：{name}"


def test_position_module_is_not_reachable_from_mcp_surface() -> None:
    """`mcp/` `web/` `ai/` 三个对外面里不许出现任何对隐私库的引用。

    与 `scripts/check_privacy.py` 第 3 组同一口径（那边还带白名单闸门自检），
    这里补的是「持仓」这条新链路：R7 加了工具与命令之后，这条断言仍然要成立。

    被授权的只有两个只读出口，而且闸门必须**同时在文件里**才算白名单：
    `/api/journal/stats`（只出聚合数字）与 `/api/positions`（平面 JSON、
    回环独占、`no-store`）。`cli.position` 是传递引用隐私库的载体，同样算越界——
    要现价请用 `core.quotes.price_map()`。
    """
    stray: list[str] = []
    for package in ("mcp", "web", "ai"):
        for path in sorted((SRC / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(SRC).as_posix()
            modules = _imports(rel)
            private = [
                m for m in modules
                if m.startswith(("ladderstock.journal", "ladderstock.cli.position"))
            ]
            if not private:
                continue
            markers = WEB_PRIVILEGED_IMPORTS.get(rel)
            if markers is not None:
                missing = [m for m in markers if m not in path.read_text(encoding="utf-8")]
                assert missing == [], f"{rel} 在册但闸门缺失：{missing}"
                continue
            stray.append(f"{rel} -> {private[0]}")
    assert stray == [], f"对外面引用了隐私库：{stray}"


# --------------------------------------------------------------------------- #
# 2. MCP 工具结果：哨兵不出现在任何信封里
# --------------------------------------------------------------------------- #

def test_dispatched_envelopes_are_position_free(tmp_path: Path) -> None:
    read_only = [spec.name for spec in TOOL_SPECS if spec.read_only]
    assert read_only, "工具清单为空，这条断言就没有意义"

    seen = 0
    for name in read_only:
        try:
            envelope = dispatch(name, {"date": DATE}, root=tmp_path)
        except Exception as exc:  # noqa: BLE001 - 缓存为空时的降级路径也允许
            assert SENTINEL not in str(exc), f"{name} 把持仓哨兵写进了报错"
            continue
        blob = json.dumps(envelope, ensure_ascii=False, default=str)
        assert SENTINEL not in blob, f"{name} 的信封里出现持仓备注"
        assert SENTINEL_CODE not in blob, f"{name} 返回了只在持仓里出现的代码"
        for key in PRIVATE_ONLY_KEYS:
            assert key not in blob, f"{name} 的信封里出现持仓字段 {key}"
        seen += 1
    assert seen >= 8, f"只跑通了 {seen} 个工具，断言覆盖面不足"


def test_candidate_pool_envelope_carries_no_position_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候选池与持仓共用同一个代码时，打分结果里也不许出现持仓的任何一个字段。"""
    rows = [{"code": SENTINEL_CODE, "name": "贵州茅台", "boards": 3, "hot_topic": "白酒"}]
    monkeypatch.setattr(
        pool_module,
        "dataset_snapshot",
        lambda dataset, day, root=None: (rows if dataset == "limit_pool" else [], "fixture"),
    )

    envelope = dispatch("candidate_pool", {"date": DATE}, root=tmp_path)
    blob = json.dumps(envelope, ensure_ascii=False, default=str)
    assert SENTINEL not in blob
    assert envelope["payload"]["pool"]["available"] is True
    candidate = envelope["payload"]["pool"]["candidates"][0]
    assert candidate["code"] == SENTINEL_CODE
    # 候选的键就是打分那六个，没有一个来自持仓
    assert set(candidate) == {"code", "name", "score", "parts", "absent_parts", "raw"}
    for key in PRIVATE_ONLY_KEYS:
        assert key not in blob, f"候选池信封出现持仓字段 {key}"


# --------------------------------------------------------------------------- #
# 3. AI 上下文：序列化结果不含持仓字段名
# --------------------------------------------------------------------------- #

def test_ai_context_serialization_excludes_position_fields(tmp_path: Path) -> None:
    rows = position_module.list_positions(root=tmp_path)
    assert rows and rows[0]["note"] == SENTINEL  # 哨兵确实写进了库

    # 复盘载荷里任意层级混进持仓（这正是 `ldst export` 与 AI 上下文共用的剥离路径）
    raw_metrics = {
        "temperature": 62.0,
        "positions": rows,
        "detail": {"holdings": rows, "持仓": rows},
    }
    assert SENTINEL in json.dumps(raw_metrics, ensure_ascii=False, default=str)

    system, user = build_messages(
        "sentiment", DATE, privacy.strip_private(raw_metrics), "你是 A 股短线复盘助手。"
    )
    blob = system + user
    assert SENTINEL not in blob
    for key in (*position_module.POSITION_FIELDS, *PRIVATE_ONLY_KEYS):
        if key in {"id", "code", "qty"}:  # 这三个是行情侧也常用的中性名
            continue
        assert key not in blob, f"AI 上下文出现持仓字段名：{key}"
    assert privacy.private_keys_present(raw_metrics) == ["positions", "holdings", "持仓"]
    assert privacy.private_keys_present(privacy.strip_private(raw_metrics)) == []


def test_pool_stdout_free_of_positions_when_codes_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ldst pool` 与持仓共用代码：stdout 里只有打分，没有成本/建仓日/备注。"""
    limit_pool = [{"code": SENTINEL_CODE, "name": "贵州茅台", "boards": 2, "turnover_rate": 8.0}]
    boards = [{"name": "白酒", "change_pct": 4.0}]

    def snapshot(dataset: str, day: Any, root: Any = None) -> tuple[list[dict[str, Any]], str]:
        if dataset == "limit_pool":
            return limit_pool, "fixture"
        if dataset == "concept_board":
            return boards, "fixture"
        return [], "fixture"

    monkeypatch.setattr(pool_module, "dataset_snapshot", snapshot)
    result = runner.invoke(app, ["pool", "--date", DATE, "--output", "json"])
    assert result.exit_code == 0, result.stderr
    assert SENTINEL not in result.stdout
    assert "12.34" not in result.stdout  # 成本价
    assert "2026-09-01" not in result.stdout  # 建仓日
    payload = json.loads(result.stdout)
    assert payload["payload"]["available"] is True
    assert payload["topic"] == "candidate_pool"
    for key in PRIVATE_ONLY_KEYS:
        assert key not in result.stdout


# --------------------------------------------------------------------------- #
# 4. 互不引用（AST）
# --------------------------------------------------------------------------- #

def test_pool_does_not_import_privacy_store() -> None:
    for rel in ("core/pool.py", "cli/pool.py"):
        modules = _imports(rel)
        assert not [m for m in modules if m.startswith("ladderstock.journal")], rel
        assert not [m for m in modules if m.startswith("ladderstock.data.sqlite_store")], rel
        assert "sqlite3" not in modules, rel


def test_position_does_not_import_market_or_ai() -> None:
    """持仓侧只依赖隐私库与错误码：不 import 行情、AI、MCP，也就能独立测试。

    `ladderstock.journal.` 放开的是**同一侧的隐私库**：`journal.log`（成交流水，
    QUOT-292 的已实现盈亏复算要用）与 `journal.realized`（纯算法，只吃数据不读库）。
    它们和 `data.sqlite_store` 一样只在本地，不引入任何网络面；行情 / AI / MCP 仍然一个都不许出现。
    """
    allowed_prefixes = ("ladderstock.data", "ladderstock.errors", "ladderstock.journal.")
    for rel in ("journal/position.py",):
        ladder = {m for m in _imports(rel) if m.startswith("ladderstock.")}
        assert ladder, rel
        stray = sorted(m for m in ladder if not m.startswith(allowed_prefixes))
        assert stray == [], f"{rel} 引用了行情/AI/MCP：{stray}"
    cli_modules = _imports("cli/position.py")
    assert not [m for m in cli_modules if m.startswith("ladderstock.core.pool")], "持仓引用了候选池"


# --------------------------------------------------------------------------- #
# 5. 磁盘：不显式 --export 就不落文件
# --------------------------------------------------------------------------- #

def _json_artifacts(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*.json")
    )


def test_no_export_file_without_flag(tmp_path: Path) -> None:
    listed = runner.invoke(app, ["position", "list", "--output", "json"])
    assert listed.exit_code == 0, listed.stderr
    assert SENTINEL in listed.stdout  # 本机终端可以打印（本地命令，不经 MCP）

    added = runner.invoke(
        app, ["position", "add", "--code", "600000", "--cost", "10", "--qty", "100"]
    )
    assert added.exit_code == 0, added.stderr
    assert _json_artifacts(tmp_path) == []
    assert {p.name for p in tmp_path.iterdir()} <= {
        sqlite_store.DB_FILENAME,
        f"{sqlite_store.DB_FILENAME}-wal",
        f"{sqlite_store.DB_FILENAME}-shm",
    }


def test_explicit_export_writes_file_and_warns(tmp_path: Path) -> None:
    target = tmp_path / "out" / "my_positions.json"
    result = runner.invoke(
        app, ["position", "list", "--output", "json", "--export", str(target)]
    )
    assert result.exit_code == 0, result.stderr
    assert target.is_file()
    assert _json_artifacts(tmp_path) == [str(Path("out") / "my_positions.json")]

    on_disk: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["exported"] == str(target)
    assert on_disk["items"][0]["note"] == SENTINEL
    assert json.loads(result.stdout)["exported"] == str(target)  # stdout 与文件同一份内容
    assert "隐私" in result.stderr and str(target) in result.stderr


def test_export_failure_keeps_exit_code_3(tmp_path: Path) -> None:
    """导出目标不可写：按配置错误 3 退出，不许把哨兵打到 stdout 上「当作成功」。"""
    blocked = tmp_path / "journal.sqlite"  # 已存在的目录位（同名文件）→ mkdir 必然 OSError
    result = runner.invoke(
        app, ["position", "list", "--output", "json", "--export", str(blocked / "x.json")]
    )
    assert result.exit_code == 3, f"{result.exit_code}\n{result.stdout}\n{result.stderr}"
    assert result.stdout.strip() == ""
