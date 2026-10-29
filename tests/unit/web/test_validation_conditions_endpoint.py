"""QUOT-197：`PUT /api/control/validation/conditions` 的契约测试。

五条硬验收（issue 原文）：

1. 写入并读回一致（`PUT` → `GET /api/validation` 拿到同一个 `code` / `trigger`）；
2. `reconcile` 未被破坏（只碰 `conditions` 与 `updated_at`）；
3. 非回环来源 / Host 不在白名单 → 403；
4. 非法 `trade_date` → 400；
5. `merge` 按 `code+trigger` 去重。

另外钉住：`replace` 覆盖、单条/顶层白名单外字段 400（不静默丢弃）、50 条上限、
`trigger` 空 / 超 200 字 400、写前 `.bak` 留底、`LDST_CONTROL_ENABLED=0` → 404、
原子写不留 `.tmp`。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.validation import load_validation, validation_path
from ladderstock.web import control
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
DATE = "2026-10-30"
URL = "/api/control/validation/conditions"


def _client(root: Path, client: tuple[str, int] = LOOPBACK, **kwargs: Any) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist", **kwargs)
    return TestClient(app, client=client)


def _put(client: TestClient, payload: Any, **kwargs: Any):
    return client.put(URL, json=payload, **kwargs)


def _expected_id(code: str | None, trigger: str) -> str:
    return hashlib.sha1(f"{code or ''}|{trigger}".encode()).hexdigest()[:12]


def _seed_document(root: Path, document: dict[str, Any]) -> Path:
    path = validation_path(DATE, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. 写入并读回一致
# --------------------------------------------------------------------------- #

def test_write_then_read_back_matches(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = _put(
        client,
        {
            "trade_date": DATE,
            "conditions": [
                {"code": "600000", "trigger": "回踩 5 日线不破", "note": "可空"}
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"trade_date", "conditions", "updated_at", "saved"}
    assert body["trade_date"] == DATE
    assert body["saved"] == 1
    assert body["updated_at"]

    # 盘上：`trigger` 落在 `text`，`id` 由 code+trigger 确定性生成
    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    disk_item = document["conditions"][0]
    assert disk_item["text"] == "回踩 5 日线不破"
    assert disk_item["code"] == "600000"
    assert disk_item["id"] == _expected_id("600000", "回踩 5 日线不破")
    assert document["updated_at"] == body["updated_at"]

    # 读路径往返：GET /api/validation 拿回同一个 code / trigger
    read = client.get("/api/validation", params={"date": DATE})
    assert read.status_code == 200, read.text
    payload = read.json()["payload"]
    assert payload["conditions"] == [
        {"code": "600000", "trigger": "回踩 5 日线不破", "note": None}
    ]


def test_minimal_skeleton_when_file_absent(tmp_path: Path) -> None:
    path = validation_path(DATE, tmp_path)
    assert not path.exists()
    response = _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"trigger": "放量突破前高"}]},
    )
    assert response.status_code == 200, response.text

    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document) == {"trade_date", "conditions", "reconcile", "updated_at"}
    assert document["trade_date"] == DATE
    assert document["reconcile"] is None
    assert document["conditions"][0]["text"] == "放量突破前高"
    assert document["conditions"][0]["id"] == _expected_id(None, "放量突破前高")
    # 首次写没有原文件，不该凭空造出 `.bak`
    assert not path.with_name(path.name + ".bak").exists()
    # 原子写：临时文件不残留
    assert not list(path.parent.glob("*.tmp"))


# --------------------------------------------------------------------------- #
# 2. `reconcile` 等对账结果原样保留
# --------------------------------------------------------------------------- #

def test_existing_reconcile_is_preserved_verbatim(tmp_path: Path) -> None:
    original = {
        "date": DATE,
        "generated_at": "2026-10-29T08:00:00+00:00",
        "source": "review_pipeline",
        "conditions": [
            {
                "id": "temperature_floor",
                "text": "明日情绪温度不低于 30（今日 45）",
                "metric": "temperature",
                "operator": ">=",
                "value": 30.0,
            }
        ],
        "reconcile": {
            "status": "ok",
            "passed": 1,
            "failed": 0,
            "unknown": 0,
            "next_session": "2026-10-31",
            "results": [{"id": "temperature_floor", "passed": True}],
            "generated_at": "2026-10-30T15:00:00+00:00",
        },
    }
    path = _seed_document(tmp_path, original)

    response = _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"code": "600000", "trigger": "回踩不破"}]},
    )
    assert response.status_code == 200, response.text

    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    assert document["reconcile"] == original["reconcile"]  # 硬约束：一字不动
    assert document["date"] == DATE
    assert document["generated_at"] == original["generated_at"]
    assert document["source"] == "review_pipeline"
    assert [item["id"] for item in document["conditions"]] == [
        "temperature_floor",
        _expected_id("600000", "回踩不破"),
    ]

    # 写前留底：`<file>.bak` 是原文件的逐字副本
    backup = path.with_name(path.name + ".bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_replace_mode_keeps_reconcile_but_drops_old_conditions(tmp_path: Path) -> None:
    _seed_document(
        tmp_path,
        {
            "date": DATE,
            "conditions": [
                {"id": "a", "text": "旧条件", "metric": "temperature", "operator": ">=", "value": 1}
            ],
            "reconcile": {"status": "partial", "results": [{"id": "a", "passed": None}]},
        },
    )
    response = _put(
        _client(tmp_path),
        {
            "trade_date": DATE,
            "mode": "replace",
            "conditions": [{"trigger": "新条件一"}, {"trigger": "新条件二"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["saved"] == 2

    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    assert [item["text"] for item in document["conditions"]] == ["新条件一", "新条件二"]
    assert document["reconcile"]["status"] == "partial"


# --------------------------------------------------------------------------- #
# 3. 闸门：非回环 403 / 开关关闭 404
# --------------------------------------------------------------------------- #

def test_non_loopback_source_is_403(tmp_path: Path) -> None:
    payload = {"trade_date": DATE, "conditions": [{"trigger": "回踩不破"}]}
    response = _put(_client(tmp_path, client=REMOTE), payload)
    assert response.status_code == 403, response.text
    assert not validation_path(DATE, tmp_path).exists()

    # 回环来源但 Host 不在白名单（DNS rebinding）同样 403
    rebinding = _put(
        _client(tmp_path), payload, headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403, rebinding.text
    assert not validation_path(DATE, tmp_path).exists()

    # 对照：回环 + 正常 Host 放行
    assert _put(_client(tmp_path), payload).status_code == 200


def test_control_disabled_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    response = _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"trigger": "回踩不破"}]},
    )
    assert response.status_code == 404, response.text
    assert not validation_path(DATE, tmp_path).exists()


def test_cross_site_write_is_403(tmp_path: Path) -> None:
    """写方法的 Origin / `Sec-Fetch-Site` 检查由 `_guard()` 复用，不另起一套。"""
    client = _client(tmp_path)
    payload = {"trade_date": DATE, "conditions": [{"trigger": "回踩不破"}]}
    cross = _put(client, payload, headers={"origin": "https://evil.example.com"})
    assert cross.status_code == 403, cross.text
    site = _put(client, payload, headers={"sec-fetch-site": "cross-site"})
    assert site.status_code == 403, site.text
    assert not validation_path(DATE, tmp_path).exists()

    same = _put(client, payload, headers={"sec-fetch-site": "same-origin"})
    assert same.status_code == 200, same.text


# --------------------------------------------------------------------------- #
# 4. 请求体校验：非法日期 / 白名单 / 条数 / 文案长度
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "trade_date",
    ["20261030", "2026-13-01", "2026/10/30", "30-10-2026", "", None, 20261030],
)
def test_invalid_trade_date_is_400(tmp_path: Path, trade_date: Any) -> None:
    response = _put(
        _client(tmp_path),
        {"trade_date": trade_date, "conditions": [{"trigger": "回踩不破"}]},
    )
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)
    assert not validation_path(DATE, tmp_path).exists()


@pytest.mark.parametrize(
    "payload",
    [
        # 顶层白名单外字段：不静默丢弃
        {"trade_date": DATE, "conditions": [], "owner": "someone"},
        # 条件元素白名单外字段
        {"trade_date": DATE, "conditions": [{"trigger": "x", "metric": "temperature"}]},
        # trigger 缺失 / 空白 / 非字符串
        {"trade_date": DATE, "conditions": [{"code": "600000"}]},
        {"trade_date": DATE, "conditions": [{"trigger": "   "}]},
        {"trade_date": DATE, "conditions": [{"trigger": 123}]},
        # trigger 超 200 字
        {"trade_date": DATE, "conditions": [{"trigger": "x" * 201}]},
        # code / note 类型不对
        {"trade_date": DATE, "conditions": [{"trigger": "x", "code": 600000}]},
        {"trade_date": DATE, "conditions": [{"trigger": "x", "note": ["a"]}]},
        # conditions 不是数组 / 超 50 条
        {"trade_date": DATE, "conditions": "not-a-list"},
        {"trade_date": DATE, "conditions": [{"trigger": "x"}] * 51},
        # mode 非法
        {"trade_date": DATE, "conditions": [], "mode": "append"},
    ],
)
def test_bad_request_body_is_400(tmp_path: Path, payload: dict[str, Any]) -> None:
    response = _put(_client(tmp_path), payload)
    assert response.status_code == 400, (payload, response.text)
    assert not validation_path(DATE, tmp_path).exists()


def test_malformed_json_is_400(tmp_path: Path) -> None:
    response = _client(tmp_path).put(
        URL, content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400, response.text


def test_50_conditions_is_still_accepted(tmp_path: Path) -> None:
    """边界：正好 50 条放行（上限拦的是 51 条的请求体）。"""
    response = _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"trigger": f"条件 {i}"} for i in range(50)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["saved"] == 50


def test_trigger_whitespace_is_trimmed(tmp_path: Path) -> None:
    _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"code": " 600000 ", "trigger": "  回踩不破  "}]},
    )
    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    assert document["conditions"][0]["text"] == "回踩不破"
    assert document["conditions"][0]["code"] == "600000"


# --------------------------------------------------------------------------- #
# 5. `merge` 去重
# --------------------------------------------------------------------------- #

def test_merge_dedupes_by_code_and_trigger(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = _put(
        client,
        {
            "trade_date": DATE,
            "mode": "merge",
            "conditions": [
                {"code": "600000", "trigger": "回踩 5 日线不破"},
                {"code": "600000", "trigger": "回踩 5 日线不破"},  # 同一批内重复
            ],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["saved"] == 1

    second = _put(
        client,
        {
            "trade_date": DATE,
            "conditions": [
                {"code": "600000", "trigger": "回踩 5 日线不破"},  # 与盘上重复
                {"code": "600000", "trigger": "缩量回踩不破"},  # 新文案 → 追加
                {"code": "000001", "trigger": "回踩 5 日线不破"},  # 不同 code → 追加
            ],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["saved"] == 3

    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    keys = [(item.get("code"), item["text"]) for item in document["conditions"]]
    assert keys == [
        ("600000", "回踩 5 日线不破"),
        ("600000", "缩量回踩不破"),
        ("000001", "回踩 5 日线不破"),
    ]
    # id 稳定：重复提交同一条件不会换 id
    assert document["conditions"][0]["id"] == _expected_id("600000", "回踩 5 日线不破")


def test_merge_keeps_programmatic_conditions(tmp_path: Path) -> None:
    """程序直出的条件（无 `code`、文案在 `text`）不被 merge 清掉，也不被重复追加。"""
    _seed_document(
        tmp_path,
        {
            "date": DATE,
            "conditions": [
                {
                    "id": "height_floor",
                    "text": "明日最高板不低于 2 板（今日 3 板）",
                    "metric": "max_board",
                    "operator": ">=",
                    "value": 2,
                }
            ],
        },
    )
    response = _put(
        _client(tmp_path),
        {"trade_date": DATE, "conditions": [{"trigger": "回踩不破"}]},
    )
    assert response.status_code == 200, response.text
    document = load_validation(DATE, root=tmp_path)
    assert document is not None
    assert [item["id"] for item in document["conditions"]] == [
        "height_floor",
        _expected_id(None, "回踩不破"),
    ]
