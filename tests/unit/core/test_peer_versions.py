"""peer_versions 的三态比对与 Release 缓存单测（QUOT-402）。

判据口径：
- 「未知」是合法结论：缺号 / 非 semver / 无缓存 → unknown，不许猜 0.0.0；
- 语义化版本比较：1.10.0 > 1.9.0（字符串比较会错）；
- 缓存失败静默：断网 / 非 200 / 无 tag 都保留旧值，不抛异常、不清缓存。
"""

from __future__ import annotations

import json
from pathlib import Path

from ladderstock.core.peer_versions import (
    ReleaseCache,
    parse_semver,
    upgrade_command,
    version_status,
)


def test_parse_semver_accepts_v_prefix_and_suffixes() -> None:
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("1.2.3-rc1") == (1, 2, 3)
    assert parse_semver("1.2.3+build.7") == (1, 2, 3)
    assert parse_semver("1.2.3 ") == (1, 2, 3)


def test_parse_semver_rejects_garbage() -> None:
    assert parse_semver(None) is None
    assert parse_semver("") is None
    # 两段号 / 纯文本 / 空洞：宁可未知，不许猜
    assert parse_semver("1.2") is None
    assert parse_semver("1.0.1+local") == (1, 0, 1)
    assert parse_semver("dev") is None


def test_version_status_semver_not_string_compare() -> None:
    # 字符串比较会说 1.10.0 < 1.9.0；语义化比较必须反过来
    assert version_status("1.10.0", "1.9.0") == "latest"
    assert version_status("1.9.0", "1.10.0") == "behind"
    assert version_status("1.1.0", "1.1.0") == "latest"
    assert version_status("1.0.1", "1.0.1+local") == "latest"


def test_version_status_unknown_when_any_side_missing() -> None:
    assert version_status(None, "1.0.0") == "unknown"
    assert version_status("1.0.0", None) == "unknown"
    assert version_status("dev", "1.0.0") == "unknown"
    assert version_status(None, None) == "unknown"


def test_upgrade_command_is_text_only() -> None:
    cmd = upgrade_command(r"D:\workplace\TickHub-CN")
    assert cmd.startswith("cd D:\\workplace\\TickHub-CN && git pull")
    # 只读红线：命令里不许出现 rm / restart / start 之类的执行动作
    assert "restart" not in cmd.lower()
    assert "rm " not in cmd.lower()


def _write_cache(path: Path, repos: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "repos": repos}, ensure_ascii=False), encoding="utf-8"
    )


def test_release_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "control" / "github-releases.json"
    entry = {"tag": "1.1.0", "etag": "W/abc", "fetched_at": 1.0}
    _write_cache(path, {"QuotaFuse-ai/TickHub-CN": entry})
    cache = ReleaseCache(path)
    assert cache.expected("QuotaFuse-ai/TickHub-CN") == "1.1.0"
    assert cache.stale("QuotaFuse-ai/TickHub-CN") is True  # fetched_at=1.0 远古 → stale


def test_release_cache_missing_file_is_empty(tmp_path: Path) -> None:
    cache = ReleaseCache(tmp_path / "nope.json")
    assert cache.expected("QuotaFuse-ai/quotafuse-wire") is None
    assert cache.stale("QuotaFuse-ai/quotafuse-wire") is True


def test_release_cache_bad_schema_ignored(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema": 99, "repos": {"a": {"tag": "9.9.9"}}}), encoding="utf-8")
    cache = ReleaseCache(path)
    assert cache.expected("a") is None


def test_release_cache_refresh_keeps_old_value_on_http_error(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "c.json"
    _write_cache(path, {"r": {"tag": "1.0.0", "etag": "W/e", "fetched_at": 1.0}})
    cache = ReleaseCache(path)

    class FakeResp:
        status_code = 500
        headers: dict = {}

    monkeypatch.setattr(
        "httpx.get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("net down"))
    )
    cache.refresh("r")
    assert cache.expected("r") == "1.0.0"

    # 非 200（含 403 限流）：同样静默保留
    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResp())
    cache.refresh("r")
    assert cache.expected("r") == "1.0.0"


def test_release_cache_refresh_updates_tag(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "c.json"
    cache = ReleaseCache(path)

    class FakeResp:
        status_code = 200
        headers = {"ETag": "W/new"}
        body_tag = "v1.2.0"

        def json(self) -> dict:
            return {"tag_name": self.body_tag}

    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResp())
    cache.refresh("QuotaFuse-ai/TickHub-CN")
    assert cache.expected("QuotaFuse-ai/TickHub-CN") == "1.2.0"
    # 落盘了：新实例能读回
    assert ReleaseCache(path).expected("QuotaFuse-ai/TickHub-CN") == "1.2.0"


def test_release_cache_304_touches_timestamp(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "c.json"
    _write_cache(path, {"r": {"tag": "1.0.0", "etag": "W/e", "fetched_at": 1.0}})
    cache = ReleaseCache(path)

    class NotModified:
        status_code = 304
        headers: dict = {}

    monkeypatch.setattr("httpx.get", lambda *a, **kw: NotModified())
    cache.refresh("r")
    assert cache.expected("r") == "1.0.0"
    assert cache._data["r"]["fetched_at"] > 1.0
