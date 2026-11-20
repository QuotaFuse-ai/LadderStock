"""`_install_deps` 幂等：已装齐的包不该再联网重装。

2026-11-19 事故原样：`runtime/embedding-deps` 里 torch 2.15.0+cpu 与
transformers 5.23.0 都在、`import torch` 正常，但安装器每次安装都无条件
`pip install torch --index-url download.pytorch.org/whl/cpu`。网络连不上就
`pip 退出码 1`，于是 `install_state.json` 留下「增强包已就绪，但依赖未装齐」
——UI 上就成了一直修不掉的告警。

`pip install --target` 不认目标目录里已装的包，这是根因所在；所以本文件钉死：
**已满足就不调用 pip，只补真缺的那一段。**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import installer, layout


def _touch_dist(target: Path, dist: str, version: str = "0.0.1") -> None:
    (target / f"{dist}-{version}.dist-info").mkdir(parents=True, exist_ok=True)


def _runtime(tmp_path: Path) -> Path:
    """`_install_deps` 要求 `runtime/api` 存在，否则直接按「API 未装」提前返回。"""
    runtime = layout.module_root(tmp_path) / "runtime"
    (runtime / "api").mkdir(parents=True, exist_ok=True)
    return runtime


def test_dist_key_strips_version_and_extras() -> None:
    assert installer._dist_key("torch") == "torch"
    assert installer._dist_key("transformers>=4.40") == "transformers"
    assert installer._dist_key("psycopg[binary]>=3.1,<4") == "psycopg"


def test_has_dist_normalizes_separators(tmp_path: Path) -> None:
    """`python-dotenv` 装出来是 `python_dotenv-*.dist-info`，两种写法都要认。"""
    _touch_dist(tmp_path, "python_dotenv", "1.2.4")
    assert installer._has_dist(tmp_path, "python-dotenv") is True
    assert installer._has_dist(tmp_path, "python_dotenv") is True
    assert installer._has_dist(tmp_path, "boto3") is False


def test_has_dist_does_not_match_prefix_only(tmp_path: Path) -> None:
    """`torch` 不能因为目录里有 `torchgen-*` 就被判成已装。"""
    _touch_dist(tmp_path, "torchgen", "2.15.0")
    assert installer._has_dist(tmp_path, "torch") is False


def test_has_dist_missing_dir_is_false(tmp_path: Path) -> None:
    assert installer._has_dist(tmp_path / "nope", "torch") is False


def test_install_deps_makes_no_pip_call_when_everything_present(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime = _runtime(tmp_path)
    api = runtime / "api-deps"
    for pkg in installer.API_DEPS:
        _touch_dist(api, installer._dist_key(pkg))
    emb = runtime / "embedding-deps"
    _touch_dist(emb, "torch", "2.15.0+cpu")
    _touch_dist(emb, "transformers", "5.23.0")

    calls: list[tuple[str, list[str]]] = []

    def fake_pip(target: Path, packages: Any, *, index_url: str | None = None) -> str:
        calls.append((target.name, list(packages)))
        return "ok"

    monkeypatch.setattr(installer, "_pip_install", fake_pip)

    result = installer._install_deps(tmp_path, True)

    assert result["ok"] is True
    assert result["detail"] == ""
    assert result["api"] == "ok"
    assert result["embedding"] == "ok"
    assert calls == [], f"已装齐仍调用了 pip：{calls}"


def test_install_deps_installs_only_the_missing_torch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """只有 torch 缺 → 只装 torch，且走 CPU 专用索引；已装的别重下。"""
    runtime = _runtime(tmp_path)
    api = runtime / "api-deps"
    for pkg in installer.API_DEPS:
        _touch_dist(api, installer._dist_key(pkg))
    emb = runtime / "embedding-deps"
    _touch_dist(emb, "transformers", "5.23.0")

    calls: list[tuple[str, list[str], str | None]] = []

    def fake_pip(target: Path, packages: Any, *, index_url: str | None = None) -> str:
        calls.append((target.name, list(packages), index_url))
        return "ok"

    monkeypatch.setattr(installer, "_pip_install", fake_pip)

    result = installer._install_deps(tmp_path, True)

    assert result["ok"] is True
    assert len(calls) == 1, f"期望只调一次 pip，实际 {calls}"
    name, packages, index_url = calls[0]
    assert name == "embedding-deps"
    assert packages == ["torch"]
    assert index_url == installer.TORCH_INDEX


def test_install_deps_skips_embedding_when_not_requested(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """没勾 embedding 就不碰 embedding 目录，也不判它失败。"""
    runtime = _runtime(tmp_path)
    for pkg in installer.API_DEPS:
        _touch_dist(runtime / "api-deps", installer._dist_key(pkg))

    calls: list[tuple[str, list[str]]] = []

    def fake_pip(target: Path, packages: Any, *, index_url: str | None = None) -> str:
        calls.append((target.name, list(packages)))
        return "ok"

    monkeypatch.setattr(installer, "_pip_install", fake_pip)

    result = installer._install_deps(tmp_path, False)

    assert result["ok"] is True
    assert result["embedding"] is None
    assert calls == []


def test_install_deps_reports_failure_when_pip_fails(tmp_path: Path, monkeypatch: Any) -> None:
    """真缺又装不上时，把 pip 的原话写进 detail——不吞、不假装 ready。"""
    runtime = _runtime(tmp_path)
    for pkg in installer.API_DEPS:
        _touch_dist(runtime / "api-deps", installer._dist_key(pkg))

    def fake_pip(target: Path, packages: Any, *, index_url: str | None = None) -> str:
        return "pip 退出码 1：connection refused"

    monkeypatch.setattr(installer, "_pip_install", fake_pip)

    result = installer._install_deps(tmp_path, True)

    assert result["ok"] is False
    assert "embedding 依赖" in result["detail"]
    assert "pip 退出码 1" in result["detail"]


def test_install_deps_early_return_when_api_component_missing(tmp_path: Path) -> None:
    """api 组件都没取件，依赖安装没有意义，直接说明并返回。"""
    result = installer._install_deps(tmp_path, True)
    assert result["detail"] == "api 组件未装，跳过依赖安装"
