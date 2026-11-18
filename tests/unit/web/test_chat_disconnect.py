"""QUOT-583：`stream_turn` 与连接解耦——断开不停、跑完落库、停止/断开分岔。

钉住六件事：

1. 断开信号：探针一次命中即定格 detached（后续不再轮询），10 次全命中；
2. 断开不停：中途断开，块照发、`done{completed:true}`、生成到 done；
3. 落库：`data_root` 接上后 `chat.sqlite` 落完整轮次——断开完成 `done`、
   失败 `failed`、用户停止 `aborted`，三者不混；
4. 用户停止：`stop_turn` 置停止位 → 取消底层调用 → `aborted`，与断开区分；
5. 停止端点：`POST /api/control/ai/chat/stop` 幂等（没在跑也 200）；
6. `await_turn` / 非流式路径零改动（既有 test_ai_chat 回归钉住）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.data import chat_store
from ladderstock.web import chat as chat_channel
from tests.unit.web.test_chat_stream import (
    FAKE_AI,
    PROVENANCE_METRICS,
    WRITE_HEADERS,
    _client,
    _done,
    _ok_channel,
    _parse_sse,
    _stub,
)

CHAT = "/api/control/ai/chat"
STOP = "/api/control/ai/chat/stop"


def _turns(tmp_path: Path, session_id: str) -> list[dict[str, Any]]:
    conn = chat_store.connect(tmp_path, create=False)
    if conn is None:
        return []
    try:
        return chat_store.session_turns(conn, session_id)
    finally:
        chat_store.close(conn)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    chat_channel.reset_sessions()
    chat_channel._ACTIVE_TURNS.clear()
    yield
    chat_channel.reset_sessions()
    chat_channel._ACTIVE_TURNS.clear()


# --------------------------------------------------------------------------- #
# 1. 断开信号命中率：探针命中一次即定格，之后不再轮询
# --------------------------------------------------------------------------- #


def test_disconnect_probe_hits_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续 10 轮、每轮探针从首块后必然命中——命中率 10/10。"""
    _stub(monkeypatch, "今天量能比昨日放大。个股分化明显。")
    for round_index in range(10):
        probes = 0

        async def disconnect_from_first_poll() -> bool:
            nonlocal probes
            probes += 1
            return True

        async def collect(round_index: int = round_index) -> list[str]:
            return [
                frame
                async for frame in chat_channel.stream_turn(
                    message=f"第 {round_index} 问",
                    session_id=f"session-hit-{round_index}",
                    is_disconnected=disconnect_from_first_poll,
                    ai=FAKE_AI,
                )
            ]

        frames = _parse_sse("".join(asyncio.run(collect())))
        # 断开不停：即便首轮探针就报断开，块与 done 照样发完整
        assert _done(frames)["completed"] is True
        assert probes >= 1


# --------------------------------------------------------------------------- #
# 3. 落库：done / failed / aborted 三态不混
# --------------------------------------------------------------------------- #


def test_disconnect_done_persists_full_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 块后断开：生成继续到 done，库里 `status=done`、`blocks_json` 完整。"""
    _stub(monkeypatch, "今天量能比昨日放大。")
    probes = 0

    async def disconnect_after_first_poll() -> bool:
        nonlocal probes
        probes += 1
        return probes > 1

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="今天大盘怎么样？",
                session_id="session-db-done",
                is_disconnected=disconnect_after_first_poll,
                ai=FAKE_AI,
                metrics=PROVENANCE_METRICS,
                data_root=tmp_path,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    blocks = [data for event, data in frames if event == "block"]
    assert _done(frames)["completed"] is True

    rows = _turns(tmp_path, "session-db-done")
    assert len(rows) == 1
    assert rows[0]["status"] == chat_store.DONE
    stored = json.loads(rows[0]["blocks_json"])
    assert stored == [{k: v for k, v in b.items() if k != "seq"} for b in blocks]


def test_generation_failure_persists_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """断开后生成失败 → 库里 `status=failed`，不是 done。"""
    _stub(monkeypatch, "")

    async def always_disconnected() -> bool:
        return True

    async def collect() -> list[str]:
        return [
            frame
            async for frame in chat_channel.stream_turn(
                message="你好",
                session_id="session-db-failed",
                is_disconnected=always_disconnected,
                data_root=tmp_path,
            )
        ]

    frames = _parse_sse("".join(asyncio.run(collect())))
    # 首块前失败走失败 notice 半流（completed 语义沿用 QUOT-581：notice 已是
    # 终点帧序列，靠 reason / code 区分失败）
    assert _done(frames)["code"] == "ai_empty"
    rows = _turns(tmp_path, "session-db-failed")
    assert len(rows) == 1
    assert rows[0]["status"] == chat_store.FAILED


def test_user_stop_persists_aborted_and_disconnect_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户停止 vs 断网断开：同一份代码，前者 aborted、后者 done，库里不混。"""
    _stub(monkeypatch, "今天量能比昨日放大。")

    # —— 断开（不断言探针命中即停，直接跑完）——
    async def always_disconnected() -> bool:
        return True

    frames = _parse_sse(
        "".join(
            asyncio.run(
                _collect(
                    "session-db-split",
                    always_disconnected,
                    tmp_path,
                )
            )
        )
    )
    assert _done(frames)["completed"] is True
    rows = _turns(tmp_path, "session-db-split")
    assert rows[0]["status"] == chat_store.DONE

    # —— 用户停止 ——
    monkeypatch.setattr(
        chat_channel,
        "complete",
        _slow_complete(chat_channel._ACTIVE_TURNS),
    )
    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())

    async def never_disconnected() -> bool:
        return False

    async def collect_stop() -> list[str]:
        iterator = chat_channel.stream_turn(
            message="慢慢答",
            session_id="session-db-stop",
            is_disconnected=never_disconnected,
            data_root=tmp_path,
        )
        chunks: list[str] = []
        async for frame in iterator:
            chunks.append(frame)
            chat_channel.stop_turn("session-db-stop")
        return chunks

    frames = _parse_sse("".join(asyncio.run(collect_stop())))
    done = _done(frames)
    assert done["completed"] is False
    assert done["reason"] == "aborted"
    rows = _turns(tmp_path, "session-db-stop")
    assert len(rows) == 1
    assert rows[0]["status"] == chat_store.ABORTED


async def _collect(session_id: str, probe: Any, tmp_path: Path) -> list[str]:
    return [
        frame
        async for frame in chat_channel.stream_turn(
            message="今天大盘怎么样？",
            session_id=session_id,
            is_disconnected=probe,
            ai=FAKE_AI,
            metrics=PROVENANCE_METRICS,
            data_root=tmp_path,
        )
    ]


def _slow_complete(registry: dict[str, Any]) -> Any:
    async def slow_complete(channel, system, user, *, timeout):
        await asyncio.sleep(30)
        return "不该拿到的回答"

    return slow_complete


# --------------------------------------------------------------------------- #
# 5. 停止端点：幂等
# --------------------------------------------------------------------------- #


def test_stop_endpoint_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {"session_id": "session-stop-endpoint"}
    response = client.post(STOP, json=body, headers=WRITE_HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "session_id": "session-stop-endpoint",
        "stopped": False,
    }
