"""FinanceCoffer 增强包（DEC-086）的六条决策落到实处。

这些用例钉的是「用户点下去会发生什么」，不是内部实现：

1. embedding 由用户勾选，勾与不勾的后果都写在向导结果里；
2. Redis 按平台分四份目录、共用一份 conf、只有 `redis_service` 一个入口；
3. 体积必须先确认，没确认不动盘；
4. 数据只在安装目录内，系统盘默认拒绝；
5. 卸载整目录删除，结果写明数据不可恢复；
6. 没有轻量模式：状态机只有一条完整包路径。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.modules.financecoffer import (
    installer,
    layout,
    manifest,
    redis_service,
    services,
    state,
)

# pytest 的 tmp_path 在系统盘上，所以除「系统盘拦截」用例外，其余显式允许系统盘落盘


@pytest.fixture(autouse=True)
def _no_real_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    """安装流程会装 Python 依赖：单测里把 pip 换成假命令，既不联网也不下 GB 级包。"""

    class _Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: _Done())


def test_two_bundle_sizes_are_stated_upfront() -> None:
    """体积两档口径按**磁盘实际占用**：核心 ≈1.0GB，加 embedding ≈3.8GB。

    不能用下载包体积（PG 的 zip 315MB，解开 848MB），也不能只算源码
    （API 源码 3MB，跑起来还要 37MB 依赖；embedding 权重 2.14GB + torch 635MB）。
    """
    assert 950 <= manifest.CORE_MB <= 1150
    assert 3600 <= manifest.FULL_MB <= 4200
    assert manifest.FULL_MB - manifest.CORE_MB >= 2500  # embedding 才是体积大头


def test_embedding_is_user_choice_with_consequences() -> None:
    without = manifest.plan(False)
    with_embedding = manifest.plan(True)

    assert [c.key for c in without.components if c.key == "embedding"] == []
    assert "向量检索长期不可用" in without.consequences[0]
    assert any("30–60 秒" in item for item in with_embedding.consequences)
    assert with_embedding.total_mb > without.total_mb


def test_plan_blocks_system_drive_by_default() -> None:
    probe = installer.plan(Path("C:/ProgramData/__ldst_probe__"), with_embedding=False)

    assert probe.ok is False
    assert any("系统盘" in item for item in probe.errors)


def test_plan_allows_system_drive_when_explicitly_permitted(tmp_path: Path) -> None:
    probe = installer.plan(tmp_path, with_embedding=True, allow_system_drive=True)

    assert probe.ok is True
    assert any("首次加载 30–60 秒" in item for item in probe.warnings)
    assert probe.plan.total_mb == manifest.FULL_MB


def test_install_requires_confirmation_and_shows_breakdown(tmp_path: Path) -> None:
    result = installer.install(
        tmp_path, with_embedding=False, confirm=False, allow_system_drive=True
    )

    assert result["status"] == "needs_confirmation"
    assert result["ok"] is False
    assert result["total_mb"] == manifest.CORE_MB
    assert {item["key"] for item in result["components"]} >= {"pg", "redis", "minio", "api"}
    assert any("不带" in c or "不含" in c for c in result["consequences"])


def test_install_runs_full_bundle_one_path(tmp_path: Path) -> None:
    result = installer.install(
        tmp_path,
        with_embedding=False,
        confirm=True,
        allow_system_drive=True,
        fetch=lambda key, target: 2048,
    )

    assert result["status"] == "ready"
    root = layout.module_root(tmp_path)
    for name in ("runtime", "conf", "logs", "run", "pgdata", "miniodata", "redisdata"):
        assert (root / name).is_dir(), name
    for plat in layout.REDIS_PLATFORMS:
        assert (root / "runtime" / "redis" / plat).is_dir()
    conf = layout.conf_dir(tmp_path) / "redis.conf"
    assert conf.is_file() and "port 16379" in conf.read_text(encoding="utf-8")
    assert state.read_state(tmp_path)["state"] == "ready"
    assert {item["key"] for item in result["fetched"]} == {"pg", "redis", "minio", "api"}


def test_install_failure_lands_in_state_file(tmp_path: Path) -> None:
    def _boom(key: str, target: Path) -> int:
        raise RuntimeError(f"{key} 下载中断")

    result = installer.install(
        tmp_path,
        with_embedding=False,
        confirm=True,
        allow_system_drive=True,
        fetch=_boom,
    )

    assert result["status"] == "failed"
    assert "下载中断" in result["detail"]
    saved = state.read_state(tmp_path)
    assert saved["state"] == "failed"
    assert saved["failed_step"] == "install"


def test_default_fetch_does_not_fake_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有任何本地来源时，必须明确报「取件失败」，不能写占位文件冒充装好。"""
    from ladderstock.modules.financecoffer import fetcher

    def _missing(component: str, target: Path, *, root: Path | None = None) -> int:
        raise fetcher.FetchError(f"{component} 没有可用来源")

    monkeypatch.setattr(fetcher, "fetch", _missing)

    result = installer.install(
        tmp_path, with_embedding=False, confirm=True, allow_system_drive=True
    )

    assert result["status"] == "failed"
    assert "没有可用来源" in result["detail"]
    assert not (layout.pg_dir(tmp_path) / "bin").exists()


def test_install_pulls_api_deps_into_module_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """装完包要能直接起 API：依赖必须装进模块目录，不能指望用户自己去 pip。"""
    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return _Done()

    monkeypatch.setattr(installer.subprocess, "run", _fake_run)

    result = installer.install(
        tmp_path, with_embedding=False, confirm=True, allow_system_drive=True,
        fetch=lambda key, target: 1,
    )

    assert result["status"] == "ready"
    dep_cmds = [cmd for cmd in seen if "pip" in cmd]
    assert dep_cmds, "没有调用 pip 装依赖，部署到别人机器上 API 起不来"
    api_cmd = dep_cmds[0]
    assert "--target" in api_cmd
    assert str(layout.module_root(tmp_path) / "runtime" / "api-deps") in api_cmd
    assert any("sqlalchemy" in part for part in api_cmd)
    # 没勾 embedding 就不该装 torch：省 635MB
    assert not any("torch" in part for cmd in seen for part in cmd)


def test_install_deps_failure_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """依赖装不上时，装机本身仍算 ready，但 detail 必须写明，否则用户以为能直接用。"""

    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "ERROR: network unreachable"

    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: _Fail())

    result = installer.install(
        tmp_path, with_embedding=False, confirm=True, allow_system_drive=True,
        fetch=lambda key, target: 1,
    )

    assert result["status"] == "ready"
    assert result["deps"]["ok"] is False
    assert "api 依赖" in result["detail"]


def test_uninstall_demands_confirmation_and_states_data_loss(tmp_path: Path) -> None:
    installer.install(
        tmp_path, with_embedding=False, confirm=True, allow_system_drive=True,
        fetch=lambda key, target: 1,
    )

    result = installer.uninstall(tmp_path, confirm=False)

    assert result["status"] == "needs_confirmation"
    assert "不可恢复" in result["detail"]
    assert str(layout.data_dir(tmp_path, "pgdata")) in result["data_dirs"]
    assert layout.module_root(tmp_path).exists()


def test_uninstall_removes_whole_directory(tmp_path: Path) -> None:
    installer.install(
        tmp_path, with_embedding=False, confirm=True, allow_system_drive=True,
        fetch=lambda key, target: 1,
    )
    # 数据目录按 env 分份：测试环境（或未设 env 的 CI）落到的名字由 layout 决定，
    # 这里不能硬写 pgdata，也不能假设 install 已经建好目录。
    data = layout.data_dir(tmp_path, "pgdata")
    data.mkdir(parents=True, exist_ok=True)
    (data / "PG_VERSION").write_text("17\n", encoding="utf-8")

    result = installer.uninstall(tmp_path, confirm=True)

    assert result["ok"] is True
    assert result["data_destroyed"] is True
    assert "不可恢复" in result["detail"]
    assert not layout.module_root(tmp_path).exists()


def test_uninstall_refuses_out_of_bounds_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """删除必须落在 <安装根>/file/modules 之内：路径被算歪就拒绝，绝不删别的目录。"""
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "keep.txt").write_text("user data", encoding="utf-8")
    monkeypatch.setattr(layout, "module_root", lambda root=None: evil)
    monkeypatch.setattr(layout, "install_root", lambda: tmp_path)

    result = installer.uninstall(tmp_path, confirm=True)

    assert result["status"] == "refused"
    assert evil.exists() and (evil / "keep.txt").is_file()


def test_state_machine_rejects_shortcut_to_ready(tmp_path: Path) -> None:
    with pytest.raises(state.IllegalTransition):
        state.write_state(tmp_path, "ready", detail="假装装好了")


def test_state_allows_reinstall_from_ready(tmp_path: Path) -> None:
    """装完再装一次是正常路径（补装 embedding / 换二进制）。

    本机实测：从设置页点「重新安装」曾因为 `ready → planning` 被拒而直接失败。
    """
    for step in ("planning", "fetching", "initializing", "configuring", "ready"):
        state.write_state(tmp_path, step, detail=step)

    # 重装：回到 planning 必须放行
    again = state.write_state(tmp_path, "planning", detail="补装 embedding")
    assert again["state"] == "planning"

    # 但「直接跳 ready」仍然禁止：重装也要走完整条链路
    with pytest.raises(state.IllegalTransition):
        state.write_state(tmp_path, "ready", detail="跳过取件直接 ready")


def test_redis_entrypoint_reports_missing_runtime(tmp_path: Path) -> None:
    result = redis_service.status(tmp_path)

    assert result["state"] == "not_installed"
    assert result["ok"] is False
    assert "redis-server" in result["detail"]
    assert result["platform"] in layout.REDIS_PLATFORMS


def test_redis_platform_matrix_covers_three_ends(tmp_path: Path) -> None:
    # 必须传 root：不传会读真实安装根，真机装过之后断言就随环境飘
    matrix = redis_service.platform_matrix(tmp_path)

    assert set(matrix["platforms"]) == set(layout.REDIS_PLATFORMS)
    assert matrix["current"] in layout.REDIS_PLATFORMS
    for info in matrix["platforms"].values():
        assert "redis" in info["dir"].replace("\\", "/")
        assert info["installed"] is False


def test_redis_conf_shared_across_platforms(tmp_path: Path) -> None:
    """三端共用同一份 conf 模板；文件名按 env 分份（正式服 -prod），路径规则一致。"""
    conf = redis_service.conf_path(tmp_path)
    assert conf == tmp_path / "file" / "modules" / "financecoffer" / "conf" / f"redis{layout.env_suffix()}.conf"
    assert conf.parent.parent == layout.module_root(tmp_path)


def test_start_api_does_not_relaunch_when_already_running(monkeypatch, tmp_path: Path) -> None:
    """已健康就别再起第二个：否则两个进程抢 8100，日志里就是 10048。"""

    def _running(root=None, **_kwargs):
        return {"ok": True, "state": "running", "port": 8100, "detail": "已在运行"}

    monkeypatch.setattr(services, "api_status", _running)

    def _boom(*args, **kwargs):
        raise AssertionError("已经在跑，不该再 Popen 一次")

    monkeypatch.setattr(services.subprocess, "Popen", _boom)

    result = services.start_api(tmp_path)

    assert result["ok"] is True
    assert "未重复启动" in result["detail"]


def test_doctor_treats_embedding_as_optional(monkeypatch, tmp_path: Path) -> None:
    """七件体检里只有 embedding / etl 可选：它们没起不算「不能用」。

    pg / redis / minio / api / mcp 五件缺一件就算不可用；pgvector 扩展缺位
    单独报（PG 起着但查询全表不存在是真实踩过的坑）。
    """
    monkeypatch.setattr(services, "pg_status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(services, "minio_status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(services.redis_service, "status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(services, "mcp_status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(services, "etl_status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(
        "ladderstock.modules.financecoffer.pg_runtime.pgvector_status",
        lambda root=None: {"ok": True, "extension": "vector"},
    )
    monkeypatch.setattr(services, "api_status", lambda root=None, **_k: {"ok": False, "state": "stopped"})
    monkeypatch.setattr(
        services, "embedding_status", lambda root=None, **_k: {"ok": False, "state": "missing_deps"}
    )

    result = services.doctor(tmp_path)

    assert result["total"] == 7
    assert result["ready"] == 5
    assert result["optional"] == ["embedding", "etl"]
    # API 是必需件，缺了就是「不可用」；embedding 缺了只影响向量检索
    assert result["missing_required"] == ["api"]
    assert result["usable"] is False


def test_doctor_usable_when_only_embedding_missing(monkeypatch, tmp_path: Path) -> None:
    for name in ("pg_status", "minio_status", "api_status", "mcp_status", "etl_status"):
        monkeypatch.setattr(services, name, lambda root=None, **_k: {"ok": True, "state": "running"})
    monkeypatch.setattr(services.redis_service, "status", lambda root=None: {"ok": True, "state": "running"})
    monkeypatch.setattr(
        "ladderstock.modules.financecoffer.pg_runtime.pgvector_status",
        lambda root=None: {"ok": True, "extension": "vector"},
    )
    monkeypatch.setattr(
        services, "embedding_status", lambda root=None, **_k: {"ok": False, "state": "missing_deps"}
    )

    result = services.doctor(tmp_path)

    assert result["ready"] == 6
    assert result["missing_required"] == []
    assert result["usable"] is True  # 不勾 embedding 的机器也算装了、能用
