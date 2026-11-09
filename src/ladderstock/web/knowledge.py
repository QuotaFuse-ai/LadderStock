"""`GET /api/knowledge/{market}/{symbol}`：标的档案只读接口（QUOT-488）。

返回档案正文 + `as_of` + `status` + `sources`，供 QUOT-487 的注入通道一次性
取出（注入顺序：档案优先于对话记忆——487 的单里有同一约定，两边一致）。

三条硬口径：

- **market 枚举即守门**：`SH | SZ | BJ` 之外的值直接 400（不是 404）——
  非法输入在进路径解析前就拒掉；
- **safePath 防目录穿越**：symbol 里的 `../`、反斜杠等先过 `normalize_symbol`
  （只放行 6 位数字），路径再过 `safe_path()`（词法前缀 + 逐级禁符号链接）；
- **回环独占 + `Host` 白名单**：闸门同研究面/标的字典面（`web/symbols.py`
  的 `_guard` 形状）；档案里带「这台机器研究过什么」的信号，不对外网开放。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ladderstock.knowledge.gate import find_secret_hits
from ladderstock.knowledge.model import (
    KnowledgeError,
    normalize_market,
    normalize_symbol,
    sh_today,
)
from ladderstock.knowledge.recall import recall
from ladderstock.knowledge.store import (
    PathEscape,
    load_profile,
)
from ladderstock.web import control as control_plane

KNOWLEDGE_PREFIX = "/api/knowledge"


class KnowledgePlane:
    """档案只读视图：`data_root` 可注入，便于测试。"""

    def __init__(self, data_root: Any) -> None:
        self.data_root = data_root

    def get(self, market: str, symbol: str, *, include_recalled: bool = True) -> dict[str, Any]:
        """接口主逻辑：校验 → 读档案 → 判状态 →（可选）附召回结果。"""
        market_norm = normalize_market(market)  # 非法枚举 → KnowledgeError → 400
        symbol_norm = normalize_symbol(symbol)
        dossier = load_profile(self.data_root, market_norm, symbol_norm)
        if dossier is None:
            raise HTTPException(status_code=404, detail=f"档案不存在：{market_norm}:{symbol_norm}")
        # gate 只读出口复用写入的密钥形态清单：档案被外部改坏时，密钥不出口
        hits = find_secret_hits(dossier.body)
        if hits:
            raise HTTPException(
                status_code=500,
                detail=f"档案内容命中疑似密钥形态，拒绝返回：{'、'.join(hits)}",
            )
        status = dossier.computed_status(sh_today())
        payload: dict[str, Any] = {
            "market": market_norm,
            "symbol": symbol_norm,
            "name": dossier.name,
            "as_of": dossier.as_of,
            "status": status,
            "valid_days": dossier.valid_days,
            "sources": dossier.sources,
            "body": dossier.body,
            "recalled": None,
        }
        if include_recalled and status != "refuted":
            recalled = recall(self.data_root, market_norm, symbol_norm)
            payload["recalled"] = (
                {
                    "as_of": recalled.as_of,
                    "status": recalled.status,
                    "valid_days": recalled.valid_days,
                    "truncated": recalled.truncated,
                    "text": recalled.text,
                }
                if recalled
                else None
            )
        return payload


async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单（同 `web/symbols.py` 的形状）。"""
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="档案接口只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )


def build_router(plane: KnowledgePlane) -> APIRouter:
    """构造 `/api/knowledge` 一个只读路由。"""
    router = APIRouter(prefix=KNOWLEDGE_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/{market}/{symbol}")
    def knowledge_get(
        market: str,
        symbol: str,
        include_recalled: Annotated[bool, Query(description="是否附召回结果")] = True,
    ) -> dict[str, Any]:
        try:
            return plane.get(market, symbol, include_recalled=include_recalled)
        except KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathEscape as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = [
    "KNOWLEDGE_PREFIX",
    "KnowledgePlane",
    "build_router",
]
