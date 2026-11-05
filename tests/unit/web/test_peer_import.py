"""QUOT-420：设置页「导入模块」——配置写入可回滚、可校验、不覆盖已有条目。

这些用例全部走临时目录 + TestClient：不碰真实 config/sources.yaml，不起进程。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.boundary import PeerControl
from ladderstock.web.control import ControlPlane, PortOwner
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}

SOURCES_TEMPLATE = """\
# 头注释：必须保留
peers:
  # alpha 的注释：必须保留
  alpha:
    product: Alpha
    package: alpha-pkg
    default_url: http://127.0.0.1:8002/mcp
    default_port: 8002
    health_path: /health
    role: 甲模块
    repo: QuotaFuse-ai/Alpha

sources:
  - id: a_source
    via: alpha
    tool: some_tool
"""

SAMPLE = {
    "id": "gamma",
    "product": "Gamma",
    "package": "gamma-pkg",
    "default_url": "http://127.0.0.1:8123/mcp",
    "health_path": "/healthz",
    "default_port": 8123,
    "role": "示例：导入验证",
    "repo": "QuotaFuse-ai/Gamma",
}


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text(SOURCES_TEMPLATE, encoding="utf-8")
    return config


@pytest.fixture()
def peer_dirs(tmp_path: Path) -> dict[str, Path]:
    return {"alpha": tmp_path / "peers" / "alpha"}


def _plane(tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]) -> ControlPlane:
    import yaml

    def load() -> dict[str, Any]:
        document = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))
        return document.get("peers") or {}

    return ControlPlane(
        data_root=tmp_path / "data",
        config_root=config_dir,
        peers={
            "alpha": PeerControl(
                key="alpha",
                directory=str(peer_dirs["alpha"]),
                import_package="alpha_pkg",
                mcp_port=8002,
                web_port=None,
                web_port_label="管理端",
                health_path="/health",
                start_commands=(("alpha", "start"),),
                config_keys=(),
            )
        },
        components={},
        sources_loader=load,
        run=lambda args, cwd, timeout: SimpleNamespace(ok=False, code=1, output=""),
        port_probe=lambda port: [],
        health_probe=lambda url, timeout: (False, "无法连接"),
        spawn=lambda args, *, cwd, log_path: SimpleNamespace(pid=1),
    )


def _client(tmp_path: Path, plane: ControlPlane) -> TestClient:
    app = create_app(
        data_root=tmp_path / "data", dist_dir=tmp_path / "missing", control_plane=plane
    )
    return TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)


def test_import_appends_peer_and_keeps_comments_order(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    client = _client(tmp_path, plane)

    response = client.post("/api/control/peers/import", json=SAMPLE)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["imported"] == "gamma"
    assert [row["id"] for row in payload["registry"]] == ["alpha", "gamma"]
    assert payload["peers"][-1]["id"] == "gamma"

    text = (config_dir / "sources.yaml").read_text(encoding="utf-8")
    assert "# 头注释：必须保留" in text
    assert "# alpha 的注释：必须保留" in text
    # 新条目落在 peers 块内、sources: 之前
    assert text.index("gamma:") < text.index("sources:")
    assert text.index("alpha:") < text.index("gamma:")
    assert "default_port: 8123" in text
    assert "role: " + json.dumps("示例：导入验证", ensure_ascii=False) in text

    backups = sorted(config_dir.glob("sources.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == SOURCES_TEMPLATE
    assert payload["backup"].endswith(backups[0].name)


def test_imported_peer_card_is_not_installed(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    plane.import_peer(SAMPLE)
    board = plane.board()
    card = next(row for row in board["peers"] if row["id"] == "gamma")
    assert card["status"] == "not_installed"
    assert card["status_label"] == "未安装"
    assert card["installed"] is False
    assert card["actions"]["can_install"] is False
    row = next(item for item in board["registry"] if item["id"] == "gamma")
    assert row["package"] == "gamma-pkg"
    assert row["default_port"] == 8123
    assert row["installed"] is False


def test_imported_peer_runs_only_when_health_passes(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    plane._port_probe = lambda port: (
        [PortOwner(pid=99, process="python.exe")] if port == 8123 else []
    )
    plane._health = lambda url, timeout: (True, "HTTP 200")
    plane.import_peer(SAMPLE)
    card = next(row for row in plane.board()["peers"] if row["id"] == "gamma")
    assert card["status"] == "running"
    assert "只读观测" in card["detail"]


def test_duplicate_id_is_409_and_file_untouched(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    client = _client(tmp_path, plane)
    before = (config_dir / "sources.yaml").read_text(encoding="utf-8")

    response = client.post("/api/control/peers/import", json={**SAMPLE, "id": "alpha"})
    assert response.status_code == 409
    assert "已存在" in response.json()["detail"]
    assert (config_dir / "sources.yaml").read_text(encoding="utf-8") == before
    assert not list(config_dir.glob("sources.yaml.bak.*"))


@pytest.mark.parametrize(
    "patch",
    [
        {"product": ""},
        {"default_port": 0},
        {"default_port": 70000},
        {"health_path": "healthz"},
        {"repo": "not-a-repo"},
        {"default_url": "file:///etc/passwd"},
        {"id": "Bad-Id"},
        {"role": "x\nevil: y"},
    ],
)
def test_incomplete_or_unsafe_fields_are_rejected(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path], patch: dict[str, Any]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    client = _client(tmp_path, plane)
    before = (config_dir / "sources.yaml").read_text(encoding="utf-8")

    response = client.post("/api/control/peers/import", json={**SAMPLE, **patch})
    assert 400 <= response.status_code < 500
    assert (config_dir / "sources.yaml").read_text(encoding="utf-8") == before
    assert not list(config_dir.glob("sources.yaml.bak.*"))


def test_missing_field_is_rejected(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    client = _client(tmp_path, plane)
    body = dict(SAMPLE)
    body.pop("repo")
    response = client.post("/api/control/peers/import", json=body)
    assert response.status_code == 422
    assert not list(config_dir.glob("sources.yaml.bak.*"))


def test_command_like_fields_are_rejected(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    client = _client(tmp_path, plane)
    response = client.post(
        "/api/control/peers/import",
        json={**SAMPLE, "command": "powershell -c whoami"},
    )
    assert response.status_code == 422


def test_import_requires_loopback_and_write_evidence(
    tmp_path: Path, config_dir: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, config_dir, peer_dirs)
    app = create_app(
        data_root=tmp_path / "data", dist_dir=tmp_path / "missing", control_plane=plane
    )
    remote = TestClient(app, client=REMOTE, headers=WRITE_HEADERS)
    assert remote.post("/api/control/peers/import", json=SAMPLE).status_code == 403

    local = TestClient(app, client=LOOPBACK)
    assert local.post("/api/control/peers/import", json=SAMPLE).status_code == 403
