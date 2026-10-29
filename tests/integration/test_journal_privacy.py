"""M2 隐私闭环与本地工作台（QUOT-95）。

覆盖四件事：

1. `journal.sqlite` schema 迁移幂等、WAL 生效、版本号自洽；
2. `journal add/list/stats` 可用，数据只落本机；
3. 隐私断言：MCP 工具清单、`LadderEnvelope`、只读 API、导出产物都不含交易日志；
4. `ldst run` 端口占用退出码 3；只读 API 的空态 / 404 / 405；前端未构建提示页。
"""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core.models import LadderEnvelope
from ladderstock.data import sqlite_store
from ladderstock.journal import log as journal_log
from ladderstock.mcp.tools import FORBIDDEN_KEYWORDS, tool_listing
from ladderstock.web.server import create_app as create_web_app

DATE = "2026-10-22"
SENTINEL = "隐私哨兵-NOTE-9f3c"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _local_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """数据目录钉在 tmp：隐私库只在临时目录产生，绝不污染仓库 `data/`。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. schema 迁移
# --------------------------------------------------------------------------- #

def test_schema_migration_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        assert sqlite_store.schema_version(conn) == sqlite_store.SCHEMA_VERSION
        assert {"trades", "positions", "watchlist", "schema_meta"} <= set(
            sqlite_store.table_names(conn)
        )
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        snapshot = sqlite_store.table_names(conn)

        # 重复迁移不改变结构、不重置版本
        assert sqlite_store.migrate(conn) == sqlite_store.SCHEMA_VERSION
        assert sqlite_store.migrate(conn) == sqlite_store.SCHEMA_VERSION
        assert sqlite_store.table_names(conn) == snapshot

        # 版本号两处一致（PRAGMA user_version 与 schema_meta）
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        meta = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert int(user_version) == int(meta) == sqlite_store.SCHEMA_VERSION

        # 时间索引存在（trades 按日、代码；watchlist / positions 按时间）
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert {"idx_trades_trade_date", "idx_trades_code_date", "idx_trades_created_at"} <= indexes
        assert {"idx_watchlist_created_at", "idx_positions_updated_at"} <= indexes
    finally:
        sqlite_store.close(conn)

    assert (tmp_path / sqlite_store.DB_FILENAME).is_file()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / sqlite_store.DB_FILENAME
    raw = sqlite3.connect(str(path))
    raw.execute("PRAGMA user_version = 999")
    raw.commit()
    raw.close()

    from ladderstock.errors import EXIT_CONFIG, LadderStockError

    with pytest.raises(LadderStockError) as excinfo:
        sqlite_store.connect(root=tmp_path, create=True)
    assert excinfo.value.code == EXIT_CONFIG


# --------------------------------------------------------------------------- #
# 2. journal 命令闭环（只写本机）
# --------------------------------------------------------------------------- #

def _add(argv: list[str]) -> None:
    result = runner.invoke(app, ["journal", "add", *argv])
    assert result.exit_code == 0, result.stderr


def test_journal_add_list_stats_roundtrip(tmp_path: Path) -> None:
    _add(
        [
            "--code", "600000", "--side", "buy", "--price", "10", "--qty", "100",
            "--note", SENTINEL, "--date", DATE, "--plan-price", "10.0",
        ]
    )
    _add(
        [
            "--code", "600000", "--side", "sell", "--price", "11", "--qty", "100",
            "--date", DATE,
        ]
    )

    listed = runner.invoke(app, ["journal", "list", "--output", "json"])
    assert listed.exit_code == 0
    rows = json.loads(listed.stdout)
    assert [row["side"] for row in rows] == ["buy", "sell"]
    assert all(row["code"] == "600000" for row in rows)

    filtered = runner.invoke(
        app, ["journal", "list", "--code", "000001", "--output", "json"]
    )
    assert filtered.exit_code == 0
    assert json.loads(filtered.stdout) == []

    stats = runner.invoke(app, ["journal", "stats", "--output", "json"])
    assert stats.exit_code == 0
    payload = json.loads(stats.stdout)
    assert payload["local_only"] is True
    assert payload["data_source_status"] == "ok"
    assert payload["total_trades"] == 2
    assert payload["closed_rounds"] == 1
    assert payload["win_rate"] == 100.0
    assert payload["realized_pnl"] == 100.0
    assert payload["deviation_review"]["count"] == 0

    # 数据只在本机：临时目录里只有隐私库，没有任何网络行为
    assert (tmp_path / sqlite_store.DB_FILENAME).is_file()
    assert "http" not in listed.stdout.lower()


def test_journal_stats_flags_deviation(tmp_path: Path) -> None:
    _add(
        [
            "--code", "600519", "--side", "buy", "--price", "12", "--qty", "10",
            "--date", DATE, "--plan-price", "10",
        ]
    )
    result = runner.invoke(app, ["journal", "stats", "--output", "json"])
    assert result.exit_code == 0
    stats = json.loads(result.stdout)
    assert stats["deviation_review"]["count"] == 1
    assert stats["deviation_review"]["threshold_pct"] == 1.0


def test_journal_list_without_store_returns_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["journal", "list", "--output", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    # 只是查询不该在磁盘上留下空库
    assert not (tmp_path / sqlite_store.DB_FILENAME).exists()


# --------------------------------------------------------------------------- #
# 3. 隐私断言
# --------------------------------------------------------------------------- #

def test_journal_never_in_mcp_listing_or_envelope(tmp_path: Path) -> None:
    journal_log.add_trade(
        "600000", "buy", 10.0, 100.0, note=SENTINEL, trade_date=DATE, root=tmp_path
    )

    listing = json.dumps(tool_listing(), ensure_ascii=False).lower()
    assert SENTINEL.lower() not in listing
    for keyword in FORBIDDEN_KEYWORDS:
        assert keyword.lower() not in listing, f"MCP 工具清单泄露隐私关键字：{keyword}"

    envelope = LadderEnvelope(
        domain="sentiment",
        topic="shortline_sentiment",
        data_as_of=date.fromisoformat(DATE),
        payload={"temperature": 55.0},
        fetched_at=datetime.now(UTC),
    )
    dumped = envelope.model_dump_json()
    assert SENTINEL not in dumped
    assert "journal" not in dumped
    assert "trades" not in dumped


def test_web_api_never_returns_journal(tmp_path: Path) -> None:
    journal_log.add_trade(
        "600000", "buy", 10.0, 100.0, note=SENTINEL, trade_date=DATE, root=tmp_path
    )
    client = TestClient(create_web_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist"))

    responses = [
        client.get("/api/health"),
        client.get("/api/state/config"),
        client.get("/api/review", params={"date": DATE}),
        client.get("/api/sentiment", params={"date": DATE}),
        client.get("/api/ladder", params={"date": DATE}),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert SENTINEL not in response.text
        assert "journal" not in response.text.lower()
        assert "600000" not in response.text

    # 静态兜底页同样不含隐私数据
    hint = client.get("/")
    assert hint.status_code == 200
    assert SENTINEL not in hint.text


def test_export_hides_journal_unless_explicit(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / f"{DATE}.json").write_text(
        json.dumps({"title": "测试复盘", "date": DATE, "sections": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    journal_log.add_trade(
        "600000", "buy", 10.0, 100.0, note=SENTINEL, trade_date=DATE, root=tmp_path
    )

    plain = runner.invoke(
        app, ["export", "--format", "md", "--date", DATE, "--out", str(tmp_path / "plain")]
    )
    assert plain.exit_code == 0, plain.stderr
    plain_body = (tmp_path / "plain.md").read_text(encoding="utf-8")
    assert SENTINEL not in plain_body
    assert "交易日志（隐私摘要）" not in plain_body
    assert json.loads(plain.stdout)["journal_included"] is False

    flagged = runner.invoke(
        app,
        [
            "export", "--format", "md", "--date", DATE,
            "--out", str(tmp_path / "flagged"), "--with-journal",
            "--output", "json",
        ],
    )
    assert flagged.exit_code == 0, flagged.stderr
    assert "隐私" in flagged.stderr
    flagged_body = (tmp_path / "flagged.md").read_text(encoding="utf-8")
    assert "交易日志（隐私摘要）" in flagged_body
    assert SENTINEL not in flagged_body  # 只有聚合值，不含备注/明细
    assert json.loads(flagged.stdout)["journal_included"] is True


def test_export_strips_nested_private_fields(tmp_path: Path) -> None:
    """R-2：复盘 JSON 里任意层级混进隐私字段，导出时都要剥掉并在 stderr 明示。"""
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / f"{DATE}.json").write_text(
        json.dumps(
            {
                "title": "测试复盘",
                "date": DATE,
                "sections": [
                    {
                        "title": "纪律",
                        "journal": {"note": SENTINEL},
                        "detail": {"trades": [{"note": SENTINEL}]},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["export", "--format", "md", "--date", DATE, "--out", str(tmp_path / "nested")]
    )
    assert result.exit_code == 0, result.stderr
    body = (tmp_path / "nested.md").read_text(encoding="utf-8")
    assert SENTINEL not in body
    assert "journal" not in body
    assert "trades" not in body
    assert "已剔除隐私字段" in result.stderr
    assert "journal" in result.stderr and "trades" in result.stderr


# --------------------------------------------------------------------------- #
# 4. run 与只读 API
# --------------------------------------------------------------------------- #
def test_run_exits_3_when_port_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        result = runner.invoke(app, ["run", "--port", str(port)])
    assert result.exit_code == 3, result.stderr
    assert "--port" in result.stderr


def test_launch_passes_loopback_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ladderstock.cli import run as run_module

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    seen: dict[str, object] = {}

    def fake_serve(application: object, *, host: str, port: int) -> None:
        seen.update({"app": application, "host": host, "port": port})

    monkeypatch.setattr("ladderstock.web.server.serve", fake_serve)

    with pytest.raises(typer.Exit) as excinfo:
        run_module.launch(host="127.0.0.1", port=port)
    assert excinfo.value.exit_code == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == port


def test_read_only_api_empty_states_and_404(tmp_path: Path) -> None:
    client = TestClient(create_web_app(data_root=tmp_path, dist_dir=tmp_path / "missing"))

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["read_only"] is True

    for path, key in (
        ("/api/review", "review"),
        ("/api/sentiment", "sentiment"),
        ("/api/ladder", "ladder"),
    ):
        response = client.get(path, params={"date": DATE})
        assert response.status_code == 200
        body = response.json()
        assert body["payload"]["data_source_status"] == "missing"
        assert body["source_status"] == "partial"
        assert key in body["payload"]

    # 非法日期 400，未知 API 404，写方法 405（只读）
    assert client.get("/api/review", params={"date": "2026-13-01"}).status_code == 400
    assert client.get("/api/review").status_code == 400
    assert client.get("/api/nope").status_code == 404
    assert client.post("/api/review", params={"date": DATE}).status_code == 405
    assert client.delete("/api/health").status_code == 405

    # 前端未构建：提示页而不是空白 404
    hint = client.get("/")
    assert hint.status_code == 200
    assert "npm run build" in hint.text


def test_review_endpoint_strips_private_keys(tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / f"{DATE}.json").write_text(
        json.dumps(
            {
                "date": DATE,
                "headline": ["情绪修复"],
                "journal": {"trades": [{"note": SENTINEL}]},
                "positions": [{"code": "600000"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(create_web_app(data_root=tmp_path, dist_dir=tmp_path / "missing"))
    response = client.get("/api/review", params={"date": DATE})
    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["data_source_status"] == "ok"
    assert body["payload"]["review"]["headline"] == ["情绪修复"]
    text = response.text
    assert SENTINEL not in text
    assert "journal" not in text.lower()
    assert "positions" not in text.lower()


def test_log_config_is_valid_and_writes_stderr() -> None:
    """uvicorn `log_config` 必须是合法 dictConfig，且 handler 走 stderr。"""
    import logging.config

    from ladderstock.web.server import log_config

    config = log_config()
    logging.config.dictConfig(config)  # 非法 level/字段会在这里直接抛错
    for name, logger_cfg in config["loggers"].items():
        assert logger_cfg["level"].isupper(), name
    for handler in config["handlers"].values():
        assert handler["stream"] == "ext://sys.stderr", handler


def test_run_serves_health_and_shuts_down_gracefully(repo: Path, tmp_path: Path) -> None:
    """真起一次 `ldst run`：/api/health 可用，Ctrl+C（SIGBREAK/SIGINT）后退出码 0 且释放端口。"""
    import os
    import signal
    import subprocess
    import sys
    import time
    import urllib.request

    from ladderstock.cli.run import port_free

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(os.environ, LDST_DATA_DIR=str(tmp_path))
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "ladderstock", "run", "--port", str(port)],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    try:
        deadline = time.time() + 30
        health: dict[str, object] | None = None
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1
                ) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    break
            except OSError:
                time.sleep(0.2)
        assert health is not None, "服务未在 30s 内起来"
        assert health["ok"] is True
        assert health["read_only"] is True

        interrupt = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
        proc.send_signal(interrupt)
        _, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, stderr
        assert "看板已停止" in stderr
        assert port_free(port), "退出后端口未释放"
    finally:
        if proc.poll() is None:  # pragma: no cover - 失败兜底
            proc.kill()
            proc.wait(timeout=10)
