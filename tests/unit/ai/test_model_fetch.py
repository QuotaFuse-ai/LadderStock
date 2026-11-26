"""模型后下载契约：默认不随包、按需下载、SHA256 校验、失败清半成品。

联网部分一律打桩：单测不许真下 92MB 权重，也不许依赖 hf-mirror 可达。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ladderstock.ai import model_fetch


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把下载落点指到 tmp，并给一份只含本模型目录的校验清单。"""
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setenv("LDST_MEMORY_MODELS_DIR", str(root))
    monkeypatch.setattr(model_fetch, "bundled_models_root", lambda: None)
    payload = b"fake weights"
    digest = hashlib.sha256(payload).hexdigest()
    rel = f"{model_fetch.MODEL_DIRNAME}/model.safetensors"
    (root / model_fetch.MANIFEST_FILENAME).write_text(
        f"{digest} *{model_fetch.MODEL_DIRNAME}/model.safetensors\n", encoding="utf-8"
    )
    (root / model_fetch.MODEL_DIRNAME).mkdir()
    (root / rel).write_bytes(payload)
    return root


def test_ready_when_all_hashes_match(sandbox: Path) -> None:
    assert model_fetch.is_ready(sandbox) is True
    assert model_fetch.verify(sandbox) == []
    assert model_fetch.status()["ready"] is True


def test_missing_file_is_not_ready(sandbox: Path) -> None:
    (sandbox / model_fetch.MODEL_DIRNAME / "model.safetensors").unlink()
    assert model_fetch.is_ready(sandbox) is False
    problems = model_fetch.verify(sandbox)
    assert len(problems) == 1 and "缺失" in problems[0]


def test_tampered_file_is_not_ready(sandbox: Path) -> None:
    (sandbox / model_fetch.MODEL_DIRNAME / "model.safetensors").write_bytes(b"tampered")
    assert model_fetch.is_ready(sandbox) is False
    assert any("哈希不一致" in item for item in model_fetch.verify(sandbox))


def test_fetch_skips_download_when_ready(sandbox: Path) -> None:
    result = model_fetch.fetch()
    assert result["ok"] is True
    assert result["status"] == "already_ready"


def test_fetch_cleans_partial_on_failure(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载失败必须删掉半成品目录，不留「装了一半」的状态。"""
    target = sandbox / model_fetch.MODEL_DIRNAME / "model.safetensors"
    target.unlink()

    def boom(*_args: object, **_kwargs: object) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"half")
        raise OSError("connection reset")

    monkeypatch.setattr(model_fetch, "_download", boom)
    result = model_fetch.fetch()
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert not (sandbox / model_fetch.MODEL_DIRNAME).exists()
    assert model_fetch.snapshot()["status"] == "failed"


def test_manifest_only_covers_this_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """清单里别的模型目录条目不参与本模型的下载与校验。"""
    import os

    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setenv("LDST_MEMORY_MODELS_DIR", str(root))
    digest = hashlib.sha256(b"x").hexdigest()
    (root / model_fetch.MANIFEST_FILENAME).write_text(
        f"{digest} *fastembed/model.onnx\n{digest} *{model_fetch.MODEL_DIRNAME}/config.json\n",
        encoding="utf-8",
    )
    entries = model_fetch.load_manifest()
    assert [item.rel for item in entries] == [
        model_fetch.MODEL_DIRNAME + os.sep + "config.json"
    ]


def test_manifest_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_MEMORY_MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(model_fetch, "bundled_models_root", lambda: None)
    with pytest.raises(model_fetch.ModelFetchError):
        model_fetch.load_manifest()


def test_memory_prefers_complete_bundled_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """半装的下载目录不能盖掉完好的随包副本。"""
    from ladderstock.ai import memory

    bundled = tmp_path / "bundled" / "file" / "models"
    (bundled / memory.BAAI_MODEL_DIRNAME).mkdir(parents=True)
    half = tmp_path / "downloaded"
    half.mkdir()
    # 不设 LDST_MEMORY_MODELS_DIR：显式覆盖优先级最高，会短路目录探测
    monkeypatch.delenv("LDST_MEMORY_MODELS_DIR", raising=False)
    monkeypatch.setattr(model_fetch, "download_root", lambda: half)
    monkeypatch.setattr("ladderstock.core.runtime.install_root", lambda: tmp_path / "bundled")
    monkeypatch.setattr("ladderstock.core.runtime.project_root", lambda: tmp_path / "none")
    assert memory._install_models_dir() == bundled
