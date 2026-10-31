"""`/api/industry`、`/api/industry/{name}`、`/api/signals`：R9 产业三页的只读接口（QUOT-276）。

浏览器读不到本机缓存，R9 的 `/industry`、`/industry/:name`、`/signals` 三页只认这三个接口，
字段名**照 `docs/specs/SPEC-INDUSTRY-PAGES.md` §2 定稿**，前端不改字段即可从演示态切真实通道。

三条取舍（详见 `docs/decisions/DEC-029-industry-http-endpoints.md`）：

- **数字全部来自 R5**：清单与单行业载荷是 `industry/snapshot.py` 的 `industry_list()` /
  `industry_snapshot()`，`history` / `constituents` / `main_net_daily` 是同包 `industry/views.py`
  的投影，规则判定也只消费 `metric_paths()` 里已有的字段。这里只做组装、闸门与错误映射，
  不重算任何一个数字，也不给缺失值补 0（DEC-028）。
- **闸门照研究面，且不受 `LDST_CONTROL_ENABLED` 影响**：产业读取属于数据面，控制面关掉时
  三页仍要出数；来源判据（回环 + `Host` 白名单）与控制面同一套函数。
- **错误码照缺口清单**：配置读不动 503、缓存读取失败 500、参数非法 422；
  名称不在清单**不是错误**——R5 给 `available=false` + `candidates`，HTTP 仍 200，
  只有路由本身没接才 404（前端据此显示「接口未接入」）。

载荷里两处刻意重复，都是为了让前端 `unwrap()`（只下钻 `payload`）读到：
`payload.data_as_of` 与信封同名同值；`payload.related` 恒为空数组时配 `payload.related_reason`
说明「R6 news_stream 未交付」，不 404、不伪造标题。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ladderstock.core.models import Domain, LadderEnvelope, SourceStatus
from ladderstock.core.privacy import strip_private
from ladderstock.errors import EXIT_CONFIG, LadderStockError
from ladderstock.industry import snapshot
from ladderstock.industry.catalog import IndustryCatalog, load_catalog
from ladderstock.industry.views import CacheProjection, evaluate_signals, rule_ids
from ladderstock.web import control as control_plane

#: `_read` 只搬错误码，不改变返回类型（载荷和投影对象都从它走）
_T = TypeVar("_T")

#: 三个接口共用 `/api` 前缀：路径照 SPEC §2，不另开 `/api/industry/list` 之类的变体
INDUSTRY_PREFIX = "/api"

INDUSTRY_DOMAIN: Domain = "sentiment"
INDUSTRY_TOPIC = "industry_snapshot"

#: 行业名上限，与前端 `normalizeIndustryName` 同档（超即 422，不截断后继续查）
NAME_MAX_LEN = 40

#: R6 未交付：`related` 出空数组，原因写在 `related_reason`（缺口 #5 明确要求不 404）
RELATED_REASON = (
    "R6 news_stream 未交付：本期恒返回空数组，页面按「资讯接口未接入」出空态，不伪造条目"
)

#: 缺口 #7 的落点：逐日主力净流入挂在单行业载荷上
#: （`/api/signals` 的载荷保持 §2.3 的六键不变，规则序列要用的话按行业名回详情接口取）
MAIN_NET_DAILY_KEY = "main_net_daily"

INVALID_NAME_EMPTY = "行业名不能为空"
INVALID_NAME_TOO_LONG = f"行业名过长（超过 {NAME_MAX_LEN} 字符）"
INVALID_NAME_SLASH = "行业名不允许包含路径分隔符"
INVALID_RULE_PREFIX = "未知的信号规则 id"

#: 两类服务端失败的文案前缀（`detail` 里只带异常消息，不带堆栈 / 路径）
CONFIG_FAILURE = "行业清单配置不可用（config/industry.yaml）"
READ_FAILURE = "行业数据读取失败"


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #

def _invalid(detail: str) -> HTTPException:
    """参数非法：SPEC 钉的是 422（控制面用 400，这里不能跟着改，前端按 422 写死）。"""
    return HTTPException(status_code=422, detail=detail)


def parse_industry_name(raw: Any) -> str:
    """行业名规范化：去空白、非空、≤40 字符、不含 `/`。非法 422。"""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise _invalid(INVALID_NAME_EMPTY)
    if len(text) > NAME_MAX_LEN:
        raise _invalid(INVALID_NAME_TOO_LONG)
    if "/" in text or "\\" in text:
        raise _invalid(INVALID_NAME_SLASH)
    return text


def parse_optional_industry(raw: Any) -> str | None:
    """`?industry=`：**空串 = 不过滤**（`URLSearchParams` 的默认形态），缺省同样不过滤。

    只写了空白却不为空串（`?industry=%20`）是调用方的笔误，按非法参数 422，
    不悄悄当成「不过滤」把全量清单倒给一个想筛选的人。
    """
    if raw is None:
        return None
    text = raw if isinstance(raw, str) else str(raw)
    if not text:
        return None
    return parse_industry_name(text)


def parse_rule_id(raw: Any) -> str | None:
    """`?rule=`：空串 = 不过滤；取值域 = `views.rule_ids()`，未知 id 直接 422，不静默给全量。"""
    if raw is None:
        return None
    text = raw if isinstance(raw, str) else str(raw)
    if not text:
        return None
    known = rule_ids()
    if text not in known:
        raise _invalid(f"{INVALID_RULE_PREFIX}：{text!r}（可选值：{', '.join(known)}）")
    return text


# --------------------------------------------------------------------------- #
# 载荷组装
# --------------------------------------------------------------------------- #

def _primary_day(payload: dict[str, Any], probe: dict[str, Any]) -> str | None:
    """信封 `data_as_of`：载荷自己声明的时点优先，否则沿用 `ldst industry show` 的规则。

    - 信号载荷自己给了 `as_of`（该条判定用到字段时点的最大值）→ 就是它；
    - 清单 / 详情没有这种标量声明，取归属表（`sw_industry_map`）自己的数据日，与 CLI 同一个口径。

    不用请求日期顶替（METRICS.md §4.3）；不可用时由调用方置 `None`。
    """
    declared = payload.get("as_of")
    if isinstance(declared, str) and declared:
        return declared
    datasets = probe.get("datasets") or {}
    block = datasets.get(snapshot.META_PRIMARY_DATASET) or {}
    day = block.get("as_of")
    return str(day) if day else None


def _source_status(payload: dict[str, Any]) -> SourceStatus:
    """`LadderEnvelope.source_status`：六个输入数据集是否都读到了东西。

    外壳的词表是 `ok / degraded / partial`（没有 `missing`，那是 R5 载荷自己的口径），
    所以「一个数据集都没记录」在这里也是 `partial`——与 `server._envelope` 同一写法。
    """
    blocks = list((payload.get("datasets") or {}).values())
    if not blocks:
        return "partial"
    if any(block.get("error") or not int(block.get("records") or 0) for block in blocks):
        return "partial"
    return "ok"


def _envelope(payload: dict[str, Any], origin: dict[str, Any] | None = None) -> dict[str, Any]:
    """把 R5 载荷套进统一外壳；`payload.data_as_of` 是与信封同值的副本，供前端下钻读取。

    `origin` 是**提供数据集状态**的那份载荷：判定结果里没有 `datasets` / `source_detail`，
    就借筛选时实际读过的那份清单来说明「这次数据从哪来、六个数据集各自读到了什么」，
    而不是在外壳里写一句无源可查的 `ok`。
    """
    probe = origin or payload
    available = bool(payload.get("available"))
    day = _primary_day(payload, probe) if available else None
    body = dict(strip_private(payload))
    body["data_as_of"] = day
    parsed: date | None = None
    if day is not None:
        try:
            parsed = date.fromisoformat(day[:10])
        except ValueError:
            parsed = None
    detail = body.get("source_detail") or probe.get("source_detail") or ""
    envelope = LadderEnvelope(
        domain=INDUSTRY_DOMAIN,
        topic=INDUSTRY_TOPIC,
        data_as_of=parsed,
        payload=body,
        source="ladderstock",
        source_detail=str(detail),
        fetched_at=datetime.now(UTC),
        freshness="cached",
        confidence="medium" if available else "low",
        source_status=_source_status(probe),
    )
    return envelope.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# 读取面
# --------------------------------------------------------------------------- #

class IndustryPlane:
    """三个只读接口的取数：一次请求读一次清单 / 一次缓存，不跨请求缓存。

    本地缓存由采集任务写，接口层再加缓存只会让页面与 CLI 给出两个答案（DEC-006）。
    """

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        config_root: Path | None = None,
    ) -> None:
        self.data_root = Path(data_root) if data_root is not None else None
        self.config_root = Path(config_root) if config_root is not None else None

    # ------------------------------------------------------------------ 读取

    def _catalog(self) -> IndustryCatalog:
        """`config/industry.yaml` 读不动 → 503（三个接口同一个口径，都从 `_read` 走）。"""
        return self._read(lambda: load_catalog(self.config_root))

    def _listing(self) -> dict[str, Any]:
        """缺口 #1 的清单载荷：`/api/industry` 与 `/api/signals` 共用同一次读取。"""
        active = self._catalog()
        return self._read(
            lambda: snapshot.industry_list(root=self.data_root, catalog=active, with_metrics=True)
        )

    def list_view(self) -> dict[str, Any]:
        """`GET /api/industry` 的完整响应（外壳 + 每行 `metrics` / `as_of` + 顶层 `as_of`）。"""
        return _envelope(self._listing())

    def detail_view(self, raw_name: Any) -> dict[str, Any]:
        """缺口 #2/#3/#4/#5/#7：R5 `show` 载荷 + 序列 / 逐股 / 逐日资金 / 关联位。"""
        name = parse_industry_name(raw_name)
        active = self._catalog()
        payload = self._read(
            lambda: snapshot.industry_snapshot(name, root=self.data_root, catalog=active)
        )
        projection = self._read(lambda: CacheProjection(active, self.data_root))
        codes = projection.codes_for(_l1_code(payload))
        payload.update(self._read(lambda: _project(projection, codes)))
        return _envelope(payload)

    def signals_view(self, raw_industry: Any, raw_rule: Any) -> dict[str, Any]:
        """缺口 #6：规则判定在 backend，载荷照 SPEC §2.3 的六键。

        判定吃的是清单那份 `metrics`，所以外壳的数据集状态也借清单那份说（`origin`）。
        """
        industry = parse_optional_industry(raw_industry)
        rule_id = parse_rule_id(raw_rule)
        listing = self._listing()
        verdict = self._read(
            lambda: evaluate_signals(listing, industry=industry, rule_id=rule_id)
        )
        return _envelope(verdict, listing)

    # -------------------------------------------------------------- 错误映射

    def _read(self, thunk: Callable[[], _T]) -> _T:
        """配置读不动 → 503；缓存读取失败 → 500；两者都不把堆栈透出给浏览器。"""
        try:
            return thunk()
        except LadderStockError as exc:
            if exc.code == EXIT_CONFIG:
                raise HTTPException(
                    status_code=503,
                    detail=f"{CONFIG_FAILURE}：{exc.message}",
                ) from None
            raise HTTPException(
                status_code=500,
                detail=f"{READ_FAILURE}：{exc.message}",
            ) from None
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"{READ_FAILURE}：{exc}",
            ) from None


def _l1_code(payload: dict[str, Any]) -> str | None:
    block = payload.get("industry") or {}
    code = block.get("l1_code")
    return str(code) if code else None


def _project(projection: CacheProjection, codes: list[str]) -> dict[str, Any]:
    """R9 增补的四块；读不动的序列降级成空，不让整页 500（R5 载荷本身仍然完整）。

    没有成分代码时序列是**空数组而不是缺键**：缺键在 SPEC §2.2 里的含义是「后端没提供」，
    而这里提供了、只是本机缓存里没有这批数据，两者不能混。
    """
    out: dict[str, Any] = {
        "history": projection.history(codes),
        "constituents": projection.constituent_rows(codes),
        MAIN_NET_DAILY_KEY: projection.main_net_daily(codes),
        "related": [],
        "related_reason": RELATED_REASON,
    }
    if projection.errors:
        out["history_reason"] = "部分数据集本机读不动，序列按已读到的数据出：" + "；".join(
            projection.errors
        )
    return out


# --------------------------------------------------------------------------- #
# 闸门与路由
# --------------------------------------------------------------------------- #

async def _guard(request: Request) -> None:
    """产业读取的来源闸门：回环 + `Host` 白名单，判据与控制面 / 研究面同一套函数。

    **不看 `LDST_CONTROL_ENABLED`**：三个接口全是只读，属于数据面，控制面关掉时页面仍要出数。
    """
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="产业接口只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )


def build_router(plane: IndustryPlane) -> APIRouter:
    """构造 SPEC §2 的三个路由（`/api/industry` 必须先于 `/api/industry/{name}` 注册）。"""
    router = APIRouter(prefix=INDUSTRY_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/industry")
    def industry_list() -> dict[str, Any]:
        return plane.list_view()

    @router.get("/signals")
    def signals(
        industry: Annotated[str | None, Query(description="按行业名过滤")] = None,
        rule: Annotated[str | None, Query(description="规则 id，见载荷 rules[]")] = None,
    ) -> dict[str, Any]:
        return plane.signals_view(industry, rule)

    @router.get("/industry/{name}")
    def industry_detail(name: str) -> dict[str, Any]:
        return plane.detail_view(name)

    return router


__all__ = [
    "CONFIG_FAILURE",
    "INDUSTRY_DOMAIN",
    "INDUSTRY_PREFIX",
    "INDUSTRY_TOPIC",
    "MAIN_NET_DAILY_KEY",
    "READ_FAILURE",
    "RELATED_REASON",
    "IndustryPlane",
    "build_router",
    "parse_industry_name",
    "parse_optional_industry",
    "parse_rule_id",
]
