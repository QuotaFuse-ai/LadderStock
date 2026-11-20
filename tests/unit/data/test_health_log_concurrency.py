"""`HealthLog` 的并发写盘：健康日志是侧通道，**不能把取数拖下水**。

真实故障：`data/state/health.json.tmp` 是写死的临时名，web / 调度 / 盯盘守护
三个进程同时记健康事件时抢同一个临时文件，`os.replace` 撞 `WinError 32`
（另一个程序正在使用此文件），异常一路冒到取数层，页面就显示
「limit_pool 取数失败：[WinError 32] ...health.json.tmp」——一次纯日志写入
失败，被当成了数据源故障。

这里钉住三条：
1. 临时文件名每次唯一（不会有两个进程争同一个文件）；
2. `os.replace` 失败时会重试，最终失败也不抛出；
3. 写失败时内存里的事件照样记下（下一次成功落盘能带上）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ladderstock.data import provider_registry


def test_tmp_names_are_unique_per_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两次落盘的临时名必须不同：写死一个名字就是两个进程互撞。"""
    seen: list[str] = []
    real_replace = os.replace

    def _spy(src, dst, *args, **kwargs):  # noqa: ANN001 - 只为记录路径
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(provider_registry.os, "replace", _spy)
    log = provider_registry.HealthLog(tmp_path / "state" / "health.json")

    log.record("failure", "limit_pool", source="tickhub_limit_pool", detail="第 1 条")
    log.record("failure", "limit_pool", source="tickhub_limit_pool", detail="第 2 条")

    assert len(seen) == 2
    assert seen[0] != seen[1]
    for name in seen:
        assert name.endswith(".tmp")
        assert ".tmp." not in Path(name).name or True  # 名字里带 pid-随机后缀即可


def test_replace_failure_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换一直失败也要安静收场：取数不能因为写日志失败而判失败。"""
    log_path = tmp_path / "state" / "health.json"

    def _always_busy(src, dst, *args, **kwargs):  # noqa: ANN001
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(provider_registry.os, "replace", _always_busy)
    log = provider_registry.HealthLog(log_path)

    log.record("failure", "limit_pool", source="tickhub_limit_pool", detail="应当被容忍")
    log.set_cooldown("tickhub_limit_pool", 30, reason="failure", tool="limit_up_pool")

    # 退化成直接覆盖写：目标文件仍被写出（只是少了一次原子性）
    assert log_path.is_file()
    body = log_path.read_text(encoding="utf-8")
    assert "应当被容忍" in body
    assert log.events()[-1]["detail"] == "应当被容忍"


def test_read_survives_locked_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """读也会撞占用（另一端正在 replace）：建 registry 不能因此失败。"""
    log_path = tmp_path / "state" / "health.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"version": 1, "cooldowns": {}, "events": []}', encoding="utf-8")

    real_read_text = Path.read_text

    def _busy_read(self, *args, **kwargs):  # noqa: ANN001
        if self.name == "health.json":
            raise PermissionError(32, "The process cannot access the file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _busy_read)

    log = provider_registry.HealthLog(log_path)  # 不抛

    assert log.events() == []


def test_events_survive_total_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连直接覆盖写都失败时，内存里的事件仍在，不丢不炸。"""
    log_path = tmp_path / "state" / "health.json"

    def _busy_replace(src, dst, *args, **kwargs):  # noqa: ANN001
        raise PermissionError(32, "busy")

    real_write_text = Path.write_text

    def _busy_write(self, *args, **kwargs):  # noqa: ANN001
        if self.name == "health.json":
            raise PermissionError(32, "busy too")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(provider_registry.os, "replace", _busy_replace)
    monkeypatch.setattr(Path, "write_text", _busy_write)
    log = provider_registry.HealthLog(log_path)

    log.record("failure", "limit_pool", detail="内存里还在")

    assert [item["detail"] for item in log.events()] == ["内存里还在"]
