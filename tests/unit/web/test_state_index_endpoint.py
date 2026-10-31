"""QUOT-288：生产态 `/__state/index` 两个路由的契约测试。

口径来源：`docs/UI-SPEC.md` §12.2 第 2 条 + `frontend/vite.config.ts` 的开发态中间件。
生产态（`ldst run` 托管 `frontend/dist`）没有那个中间件，7 条路由的 console 各留一条
`405 POST /__state/index`，`ldst state inspect` 也看不到分片——本文件钉住补上的两条路由：

1. POST 落盘、GET 读回与之一致（含 `ldst state inspect` 读得到）；
2. 上限 2 MB（超限 413）、坏 JSON / 顶层非对象 400；
3. 非回环来源与非法 `Host` → 403，写方法还必须有正向来源证据；
4. `pending_reset` 不被上报抹掉，回执 `ack_reset` 才清除；部分上报不清空概况；
5. 不受 `LDST_CONTROL_ENABLED` 影响，也不落进 SPA 兜底。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.cli import state as state_cli
from ladderstock.web import control
from ladderstock.web.server import create_app
from ladderstock.web.state_index import MAX_INDEX_BODY_BYTES, StateIndexPlane

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
ORIGIN = {"origin": "http://127.0.0.1:54321"}
ENDPOINT = "/__state/index"

INDEX_RELATIVE = Path("state") / "frontend-index.json"

#: 前端一次真实上报的形状（字段见 `frontend/persist/indexReporter.ts:IndexSnapshot`）
REPORT: dict[str, Any] = {
    "backend": "indexeddb",
    "schema_version": 2,
    "generated_at": "2026-11-01T01:00:00Z",
    "stores": {
        "review_draft": {
            "store_id": "review_draft",
            "kind": "draft",
            "schema_version": 2,
            "updated_at": "2026-11-01T00:59:58Z",
            "bytes": 4096,
            "recoverable": True,
            "needs_refetch": False,
            "needs_refetch_reason": None,
            "ttl_sec": None,
        },
        "watch_snapshot": {
            "store_id": "watch_snapshot",
            "kind": "snapshot",
            "schema_version": 2,
            "updated_at": "2026-11-01T00:59:50Z",
            "bytes": 512,
            "recoverable": True,
            "needs_refetch": True,
            "needs_refetch_reason": "ttl_expired",
            "ttl_sec": 300,
        },
    },
}


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def _index_file(root: Path) -> Path:
    return root / INDEX_RELATIVE


def _post(client: TestClient, payload: Any, **kwargs: Any):
    return client.post(ENDPOINT, json=payload, headers=ORIGIN, **kwargs)


def _seed_pending_reset(root: Path, stores: tuple[str, ...]) -> None:
    """模拟 `ldst state reset` 的登记结果：由 CLI 写进同一个文件。"""
    target = _index_file(root)
    document = {
        "backend": "indexeddb",
        "schema_version": 2,
        "generated_at": None,
        "stores": {},
        "pending_reset": [
            {
                "store": store,
                "requested_at": "2026-11-01T00:00:00Z",
                "note": "由 ldst state reset 登记",
            }
            for store in stores
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. POST 落盘 / GET 读回
# --------------------------------------------------------------------------- #


def test_post_writes_the_index_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = _post(client, REPORT)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "path": "data/state/frontend-index.json",
        "cleared_reset": [],
    }

    document = json.loads(_index_file(tmp_path).read_text(encoding="utf-8"))
    assert document["backend"] == "indexeddb"
    assert document["schema_version"] == 2
    assert document["generated_at"] == "2026-11-01T01:00:00Z"
    assert set(document["stores"]) == {"review_draft", "watch_snapshot"}
    assert document["stores"]["watch_snapshot"]["needs_refetch_reason"] == "ttl_expired"
    assert document["pending_reset"] == []
    # 落盘不留临时文件
    assert sorted(p.name for p in _index_file(tmp_path).parent.iterdir()) == ["frontend-index.json"]


def test_get_returns_what_post_wrote(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _post(client, REPORT)
    response = client.get(ENDPOINT)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["stores"] == REPORT["stores"]
    assert body["generated_at"] == REPORT["generated_at"]


def test_get_before_any_report_is_an_empty_index(tmp_path: Path) -> None:
    """没上报过时如实为空（不是 404、也不是伪造概况），文件也不该被读操作创建出来。"""
    response = _client(tmp_path).get(ENDPOINT)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "backend": "indexeddb",
        "schema_version": None,
        "generated_at": None,
        "stores": {},
        "pending_reset": [],
    }
    assert not _index_file(tmp_path).exists()


def test_corrupt_index_file_reads_as_empty(tmp_path: Path) -> None:
    _index_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _index_file(tmp_path).write_text("{半个 JSON", encoding="utf-8")
    body = _client(tmp_path).get(ENDPOINT).json()
    assert body["stores"] == {}


def test_state_inspect_sees_the_reported_shards(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收口径的直接断言：POST 之后 `ldst state inspect` 读得到分片，不再「尚未上报」。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    _post(_client(tmp_path), REPORT)

    report = state_cli.build_report()
    assert report["index_present"] is True
    assert report["index_generated_at"] == REPORT["generated_at"]
    rows = {row["name"]: row for row in report["stores"]}
    assert rows["review_draft"]["source"] == "index"
    assert rows["review_draft"]["bytes"] == 4096
    assert rows["watch_snapshot"]["needs_refetch_reason"] == "ttl_expired"


# --------------------------------------------------------------------------- #
# 2. 不落后端 / 不被 SPA 兜底吃掉
# --------------------------------------------------------------------------- #


def test_get_is_not_answered_by_the_spa_fallback(tmp_path: Path) -> None:
    """路径必须注册在 `@app.get("/{full_path:path}")` 之前：兜底会回一段 HTML。"""
    client = _client(tmp_path)
    response = client.get(ENDPOINT)
    assert "text/html" not in response.headers["content-type"]
    assert isinstance(response.json(), dict)


def test_other_methods_are_405_with_full_allow(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for method in ("put", "patch", "delete", "options"):
        response = getattr(client, method)(ENDPOINT)
        assert response.status_code == 405, (method, response.status_code)
        assert response.headers["allow"] == "GET, POST"


# --------------------------------------------------------------------------- #
# 3. 体积与载荷校验
# --------------------------------------------------------------------------- #


def test_oversized_body_is_413_and_writes_nothing(tmp_path: Path) -> None:
    payload = dict(REPORT, stores={"review_draft": "x" * (MAX_INDEX_BODY_BYTES + 1024)})
    response = _post(_client(tmp_path), payload)
    assert response.status_code == 413, response.text[:200]
    assert not _index_file(tmp_path).exists()


def test_raw_body_over_limit_without_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分块传输（无 `Content-Length`）也要被逐块累加挡住，不能靠头字段。"""
    monkeypatch.setattr("ladderstock.web.state_index.MAX_INDEX_BODY_BYTES", 1024)
    body = json.dumps({"stores": {"review_draft": "x" * 4096}}).encode()
    response = _client(tmp_path).request(
        "POST",
        ENDPOINT,
        content=body,
        headers={**ORIGIN, "content-type": "application/json", "transfer-encoding": "chunked"},
    )
    assert response.status_code == 413, response.text[:200]


@pytest.mark.parametrize(
    ("payload", "detail_hint"),
    [
        ([], "JSON 对象"),
        ("not-an-object", "JSON 对象"),
        (None, "JSON 对象"),
    ],
)
def test_non_object_body_is_400(tmp_path: Path, payload: Any, detail_hint: str) -> None:
    response = _post(_client(tmp_path), payload)
    assert response.status_code == 400, response.text
    assert detail_hint in response.json()["detail"]


def test_malformed_json_is_400(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        ENDPOINT,
        content=b"{broken",
        headers={**ORIGIN, "content-type": "application/json"},
    )
    assert response.status_code == 400, response.text


def test_empty_body_is_400(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        ENDPOINT,
        content=b"",
        headers={**ORIGIN, "content-type": "application/json"},
    )
    assert response.status_code == 400, response.text


# --------------------------------------------------------------------------- #
# 4. `pending_reset` 不能被上报抹掉
# --------------------------------------------------------------------------- #


def test_report_keeps_a_pending_reset_registration(tmp_path: Path) -> None:
    """`ldst state reset` 登记后，任何一次普通上报都不能把它抹掉。"""
    _seed_pending_reset(tmp_path, ("review_draft",))
    client = _client(tmp_path)
    _post(client, REPORT)

    body = client.get(ENDPOINT).json()
    assert [item["store"] for item in body["pending_reset"]] == ["review_draft"]
    assert body["stores"], "上报本身要写进去"
    document = json.loads(_index_file(tmp_path).read_text(encoding="utf-8"))
    assert document["pending_reset"][0]["note"] == "由 ldst state reset 登记"


def test_ack_reset_clears_only_the_acked_store(tmp_path: Path) -> None:
    _seed_pending_reset(tmp_path, ("review_draft", "watch_snapshot"))
    client = _client(tmp_path)
    payload = dict(REPORT, ack_reset=["review_draft"])
    response = _post(client, payload)
    assert response.status_code == 200, response.text
    assert response.json()["cleared_reset"] == ["review_draft"]

    body = client.get(ENDPOINT).json()
    assert [item["store"] for item in body["pending_reset"]] == ["watch_snapshot"]


def test_partial_report_keeps_previous_summary(tmp_path: Path) -> None:
    """只带回执的启动上报（`syncPendingResets` 的 ack 通道）不能把概况清空。"""
    client = _client(tmp_path)
    _post(client, REPORT)
    _post(client, {"backend": "indexeddb", "ack_reset": []})
    body = client.get(ENDPOINT).json()
    assert set(body["stores"]) == {"review_draft", "watch_snapshot"}
    assert body["generated_at"] == REPORT["generated_at"]


def test_unknown_keys_in_report_are_dropped(tmp_path: Path) -> None:
    """落盘只留契约字段：将来前端多带东西不会静默写进本机文件。"""
    _post(_client(tmp_path), dict(REPORT, database="ladderstock-state", stray="x"))
    document = json.loads(_index_file(tmp_path).read_text(encoding="utf-8"))
    assert set(document) == {"backend", "schema_version", "generated_at", "stores", "pending_reset"}


# --------------------------------------------------------------------------- #
# 5. 闸门
# --------------------------------------------------------------------------- #


def test_non_loopback_source_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")
    client = _client(tmp_path, client=REMOTE)

    assert client.get(ENDPOINT).status_code == 403
    response = client.post(ENDPOINT, json=REPORT, headers=ORIGIN)
    assert response.status_code == 403
    assert not _index_file(tmp_path).exists()


def test_host_outside_allowlist_is_403(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rebinding = client.get(ENDPOINT, headers={"host": "evil.example.com"})
    assert rebinding.status_code == 403
    response = client.post(ENDPOINT, json=REPORT, headers={"host": "evil.example.com", **ORIGIN})
    assert response.status_code == 403


def test_write_without_source_evidence_is_403(tmp_path: Path) -> None:
    """跨站页面用 `text/plain` 包一段 JSON 可以绕过预检，来源证据是唯一的挡点。"""
    client = _client(tmp_path)
    response = client.post(
        ENDPOINT,
        json=REPORT,
        headers={"content-type": "text/plain", "origin": "http://evil.example.com:1234"},
    )
    assert response.status_code == 403
    assert not _index_file(tmp_path).exists()

    # 非浏览器客户端的正向证据：显式声明身份
    cli = client.post(
        ENDPOINT, json=REPORT, headers={"content-type": "application/json", "X-LDST-Client": "1"}
    )
    assert cli.status_code == 200, cli.text


def test_cross_site_sec_fetch_site_is_403(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        ENDPOINT, json=REPORT, headers={**ORIGIN, "sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403


def test_control_switch_does_not_close_state_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是写交易数据的面：本机状态面必须照常可用。"""
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    client = _client(tmp_path)
    assert client.get("/api/control/peers").status_code == 404
    assert client.get(ENDPOINT).status_code == 200
    assert _post(client, REPORT).status_code == 200


# --------------------------------------------------------------------------- #
# 6. 与 CLI 侧的原子写共存
# --------------------------------------------------------------------------- #


def test_plane_reads_what_cli_wrote(tmp_path: Path) -> None:
    """`ldst state reset` 的落盘格式（同 `core/validation.write_document`）被这里原样读回。"""
    from ladderstock.cli.state import INDEX_RELATIVE as CLI_RELATIVE

    assert CLI_RELATIVE == INDEX_RELATIVE, "两侧路径必须同源"
    target = _index_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"backend": "indexeddb", "stores": {}, "pending_reset": [{"store": "review_draft"}]}
    target.write_text(json.dumps(document) + "\n", encoding="utf-8")
    document = StateIndexPlane(data_root=tmp_path).document()
    assert [item["store"] for item in document["pending_reset"]] == ["review_draft"]
    assert document["pending_reset"][0]["requested_at"], "缺 requested_at 要补上时间戳"
