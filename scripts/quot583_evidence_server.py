"""QUOT-583 真服务器验收（uvicorn 0.54 + Starlette 1.10 真实断开信号）。

最小 FastAPI 应用：`/ai/chat` 直连 `chat_channel.dispatch`（与 control.py 同一
条入口链），AI 用慢速 fake（逐块 ~0.3s，整个回答 ~2s），`data_root` 落到临时目录。

    PYTHONPATH=src python scripts/quot583_evidence_server.py <port> <data_root>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ladderstock.web import chat as chat_channel  # noqa: E402

DATA_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def _install_fake_ai() -> None:
    """把通道与模型调用打桩成慢速假流（与单测同一注入点：模块级函数）。"""

    def fake_load_channel(_root=None):
        return chat_channel.Channel(kind="http", label="fake:sentences", config=None, target=None)

    async def fake_complete_iter(channel, system, user, *, timeout):
        for sentence in [
            "今天量能比昨日明显放大。",
            "个股分化，权重护盘。",
            "短线注意节奏。",
        ]:
            await asyncio.sleep(0.3)
            yield sentence

    async def fake_complete(channel, system, user, *, timeout):
        await asyncio.sleep(0.3)
        return "今天量能比昨日明显放大。个股分化，权重护盘。短线注意节奏。"

    chat_channel.load_channel = fake_load_channel
    chat_channel.complete_iter = fake_complete_iter
    chat_channel.complete = fake_complete


_install_fake_ai()


class ChatBody(BaseModel):
    message: str
    session_id: str | None = None
    context: dict[str, Any] | None = None
    timeout_sec: float | None = None


app = FastAPI()


@app.post("/ai/chat")
async def ai_chat(body: ChatBody, request: Request) -> Any:
    return await chat_channel.dispatch(
        chat_channel.ChatRequest(
            message=body.message,
            session_id=body.session_id,
            context=body.context,
            timeout_sec=body.timeout_sec,
        ),
        request,
        data_root=DATA_ROOT,
    )


@app.post("/ai/chat/stop")
def ai_chat_stop(body: chat_channel.ChatStopRequest) -> dict[str, Any]:
    return chat_channel.stop_turn_result(body.session_id)


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5973
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
