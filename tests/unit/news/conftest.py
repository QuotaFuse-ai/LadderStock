"""资讯雷达单测的公共桩：完全离线（不拨网络）、完全落 tmp（不污染仓库 `data/`）。

`ldst news` / `ldst events` 的真实链路是 `ProviderRegistry` → TickHub-CN peer。
这里只换掉传输层（`build_transport` 的官方测试缝），其余——sources.yaml / sync.yaml
的源链、降级顺序、冷却、短窗缓存——都按真实配置跑，所以桩替掉的是网络，不是逻辑。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from fixtures.fake_peers import FakeTransport, make_news_registry
from fixtures.news_samples import news_handlers, news_tools

from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.news.stream import collect_stream

WINDOW_START = "2026-10-29"
WINDOW_END = "2026-10-31"


@pytest.fixture()
def news_transport_factory() -> Callable[..., FakeTransport]:
    def build(*, failing: Sequence[str] = ()) -> FakeTransport:
        return FakeTransport(handlers=news_handlers(failing=tuple(failing)), tools=news_tools())

    return build


@pytest.fixture()
def news_registry(tmp_path: Path) -> Callable[..., tuple[FakeTransport, ProviderRegistry]]:
    """`news_registry(failing=[...], root=tmp_path)` → (桩, 用假资讯配置的注册表)。"""

    def build(
        *, failing: Sequence[str] = (), root: Path | None = None, ttl_sec: float = 0.0
    ) -> tuple[FakeTransport, ProviderRegistry]:
        store_root = root if root is not None else tmp_path
        transport = FakeTransport(
            handlers=news_handlers(failing=tuple(failing)), tools=news_tools()
        )
        return transport, make_news_registry(transport, store_root, ttl_sec=ttl_sec)

    return build


@pytest.fixture()
def stream_of(tmp_path: Path, news_registry):
    """按真实源链跑一次 `collect_stream`，返回 (结果, 桩)。

    `root` 一律钉在 tmp：注册表的冷却文件和资讯库都会落盘，落到仓库 `data/`
    会让后面一个用例读到上一个用例的冷却状态，失败原因就没法判断了。
    """

    def run(
        *,
        start: str = WINDOW_START,
        end: str = WINDOW_END,
        codes: Sequence[str] = ("600519",),
        datasets: Sequence[str] | None = None,
        failing: Sequence[str] = (),
        persist: bool = False,
        root: Path | None = None,
        limit: int = 100,
        ttl_sec: float = 0.0,
    ):
        from datetime import date

        store_root = root if root is not None else tmp_path
        transport, registry = news_registry(failing=failing, root=store_root, ttl_sec=ttl_sec)
        result = collect_stream(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            datasets=list(datasets) if datasets else None,
            codes=list(codes),
            registry=registry,
            root=store_root,
            persist=persist,
            limit=limit,
        )
        return result, transport

    return run
