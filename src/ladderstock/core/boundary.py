"""四层边界的唯一裁判。

LadderStock 是生态第 4 层（短线复盘与纪律执行），**不采集、不合议、不做多源聚合**：

| 层 | 产品 | LadderStock 的关系 |
|---|---|---|
| 数据底座 | TickHub-CN（`th-cn`）/ TickHub-US（`th-us`） | 只消费，可本地缓存回放 |
| 资讯感知 | QuotaFuse Wire（`qfw`） | 可选消费（题材段叙事） |
| 投研合议 | StockSenate（`ssenate`） | 只读消费，不做合议 |
| 短线复盘 | LadderStock（`ldst`） | 本项目 |

本模块把这条边界变成**可执行的约束**，而不只是文档里的一句话：

1. `DATASET_CATALOG` —— 每个数据集都必须登记归属 peer 与工具，没有归属就不许同步。
2. `scan_no_collection()` —— 源码里出现采集库或行情站点直连即违规（测试与 `ldst doctor` 共用）。
3. `merge_narrative()` / `assert_narrative_only()` —— AI 只能写叙述，不能改数字。

任何越界统一抛 `BoundaryViolation`（属于施工期契约错误，不落到对外退出码）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["append", "replace"]


class BoundaryViolation(RuntimeError):
    """破坏四层边界：自建采集、重复合议、或让 AI 覆盖程序算数结果。"""


# --------------------------------------------------------------------------- #
# 生态三件套
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Peer:
    key: str
    product: str
    package: str
    cli: str
    mcp_name: str
    default_port: int
    role: str
    consumption: str


PEERS: dict[str, Peer] = {
    "tickhub": Peer(
        key="tickhub",
        product="TickHub-CN",
        package="tickhub-cn",
        cli="th-cn",
        mcp_name="tickhub-cn",
        default_port=8000,
        role="数据底座：行情 / 涨停池 / 龙虎榜 / 情绪指标 / 资金",
        consumption="只消费",
    ),
    "tickhub_us": Peer(
        key="tickhub_us",
        product="TickHub-US",
        package="tickhub-us",
        cli="th-us",
        mcp_name="tickhub-us",
        default_port=8002,
        role="数据底座：美股行情 / 财报 / 监管披露",
        consumption="只消费",
    ),
    "wire": Peer(
        key="wire",
        product="QuotaFuse Wire",
        package="quotafuse-wire",
        cli="qfw",
        mcp_name="qf-wire",
        default_port=8790,
        role="资讯感知：新闻 / 政策 / 产业链 / 多源聚合",
        consumption="可选消费",
    ),
    "senate": Peer(
        key="senate",
        product="StockSenate",
        package="stocksenate",
        cli="ssenate",
        mcp_name="stock-senate",
        default_port=8765,
        role="投研合议：7 分析师 → 辩论 → 风控 → 终审 / 回测",
        consumption="只读消费",
    ),
}


# --------------------------------------------------------------------------- #
# 控制面元数据（DEC-014 §1/§3/§5）
# --------------------------------------------------------------------------- #
#
# `Peer` 只回答「生态里是谁」；控制面还要回答「装在哪、跑哪个端口、怎么启停、
# 能改哪些键」。这些**同样是白名单**：控制面只认这张表，URL/请求参数永远落不到
# 自由字符串上（DEC-014 硬约束 2）。
#
# 端口口径以 DEC-013 为准（TickHub-US 的 8001 被 WSL 转发占用 → 8002；
# admin 8787 与 CN 撞车 → 8789）。`import_package` 是 `.venv` 里真正可 import
# 的名字，与分发名 `package`（带连字符）不是一回事。

#: 控制面默认只写这一个文件（DEC-014 硬约束 4：凭据只进 env、0600）。
#: 但各 peer 读取的 dotenv 文件名并不一致（见 `PeerControl.env_filename` 的实测
#: 依据）：CN / US / Senate 只读 `.env`，Wire 读 `.env.local` 且优先级更高。
#: 写错文件名 = 用户填了 key 却静默不生效，属于 DEC-014 禁止的「静默降级」。
PEER_ENV_FILENAME = ".env.local"


@dataclass(frozen=True)
class PeerControl:
    """一个 peer 的安装/启停/配置元数据（控制面唯一白名单）。"""

    key: str
    directory: str
    import_package: str
    mcp_port: int
    #: 浏览器可打开的端口（管理端 / WebUI / HTTP 服务）；没有则为 None
    web_port: int | None
    web_port_label: str
    health_path: str
    #: 启动命令：第一项是 `.venv/Scripts/` 下的入口名，其余是参数（参数数组，不拼 shell）
    start_commands: tuple[tuple[str, ...], ...]
    #: 允许读写的键（PRD §5）
    config_keys: tuple[str, ...]
    #: 其中的密钥类键：GET 只回打码预览，日志同样脱敏
    secret_keys: tuple[str, ...] = ()
    #: 安装形态：有 wheel 用 wheel，没有就 `pip install -e .`（DEC-014 §4）
    has_wheel_dist: bool = True
    #: 该 peer 真正会加载的 dotenv 文件名（实测自各仓库源码，不是猜的）
    env_filename: str = PEER_ENV_FILENAME
    #: 配置项与目标实现不一致时的说明（如实暴露，不静默）
    config_notes: tuple[str, ...] = ()


PEER_CONTROL: dict[str, PeerControl] = {
    "tickhub": PeerControl(
        key="tickhub",
        directory=r"[local-path]\TickHub-CN",
        import_package="tickhub_cn",
        mcp_port=8000,
        web_port=8787,
        web_port_label="管理端",
        health_path="/healthz",
        start_commands=(("th-cn", "start-all"),),
        config_keys=("MCP_HTTP_PORT", "ADMIN_PORT", "TICKHUB_CN_LOG_LEVEL"),
        env_filename=".env",
        config_notes=(
            "TickHub-CN 的 admin 健康检查在 /healthz（admin_api/app.py:291）。",
            "CN 源码只读 ADMIN_PORT / LOG_LEVEL 等环境变量：MCP 端口走 CLI --port（默认 8000），"
            "日志键实际是 LOG_LEVEL；MCP_HTTP_PORT、TICKHUB_CN_LOG_LEVEL 按 PRD §5 保留但当前不生效。",
        ),
    ),
    "tickhub_us": PeerControl(
        key="tickhub_us",
        directory=r"[local-path]\TickHub-US",
        import_package="tickhub_us",
        mcp_port=8002,
        web_port=8789,
        web_port_label="管理端（DEC-013 预留）",
        health_path="/healthz",
        # 无 dist/*.whl：只能 `pip install -e .`（PRD §2 已知冲突 3）
        start_commands=(
            ("th-us", "mcp", "http", "--host", "127.0.0.1", "--port", "8002"),
            ("th-us-collector",),
        ),
        config_keys=(
            "MCP_HTTP_PORT",
            "ADMIN_HTTP_PORT",
            "SEC_CONTACT",
            "POLYGON_API_KEY",
            "TICKHUB_US_ALLOW_TIER_B_SOURCES",
        ),
        secret_keys=("POLYGON_API_KEY",),
        has_wheel_dist=False,
        env_filename=".env",
        config_notes=(
            "US 的 MCP HTTP 自带匿名 /healthz（mcp/security.py:28），健康检查打 8002。",
            "本机 TickHub-US 构建没有 admin API，8789 只是 DEC-013 预留；"
            "ADMIN_HTTP_PORT 目前无处读取（按 PRD §5 保留）。",
        ),
    ),
    "wire": PeerControl(
        key="wire",
        directory=r"[local-path]\Wire\quotafuse-wire",
        import_package="qf_wire",
        mcp_port=8791,
        web_port=8790,
        web_port_label="HTTP 服务",
        health_path="/api/health",
        start_commands=(("qfw", "serve"), ("qfw", "mcp", "http")),
        config_keys=(
            "QF_WIRE_HOST",
            "QF_WIRE_PORT",
            "QF_WIRE_MCP_PORT",
            "QF_WIRE_DIGEST_PROVIDER",
            "QF_WIRE_API_BASE",
            "QF_WIRE_DIGEST_MODEL",
            "QF_WIRE_API_KEY",
        ),
        secret_keys=("QF_WIRE_API_KEY",),
        # Wire 的 config/dotenv.py 明确读 .env / .env.local，且 .env.local 优先
        env_filename=".env.local",
        config_notes=(
            "Wire 的 /api/health 在 8790（web/server.py:46），8791 是 MCP。",
        ),
    ),
    "senate": PeerControl(
        key="senate",
        directory=r"[local-path]\StockSenate",
        import_package="stock_senate",
        mcp_port=8765,
        web_port=8788,
        web_port_label="WebUI",
        health_path="/healthz",
        start_commands=(
            ("ssenate", "mcp", "http", "--host", "127.0.0.1", "--port", "8765"),
            ("ssenate", "webui", "--host", "127.0.0.1", "--port", "8788"),
        ),
        config_keys=("llm_provider", "deep_think_llm", "quick_think_llm", "MINIMAX_API_KEY"),
        secret_keys=("MINIMAX_API_KEY",),
        env_filename=".env",
        config_notes=(
            "Senate 的 /healthz 在 WebUI 8788（webui/auth.py:34），且强制鉴权例外仅此路径。",
            "llm_provider / deep_think_llm / quick_think_llm 实际是 config/llm_config.toml 的键，"
            "写 env 不生效（PRD §5 注明「映射到 llm_config.toml」，待 P4 落地）。",
        ),
    ),
}


# --------------------------------------------------------------------------- #
# 数据集归属（唯一真源）
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DatasetBinding:
    """一个数据集只能有一个合法来源族：peer + 该 peer 的工具。"""

    dataset: str
    peer: str
    tool: str
    mode: Mode
    watermark_field: str
    fallback_tools: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.peer not in PEERS:
            raise BoundaryViolation(f"数据集 {self.dataset} 归属未知 peer：{self.peer}")
        if not self.tool:
            raise BoundaryViolation(f"数据集 {self.dataset} 未绑定工具，等于自建采集")


def _b(
    dataset: str,
    peer: str,
    tool: str,
    mode: Mode,
    watermark_field: str,
    fallback_tools: tuple[str, ...] = (),
    note: str = "",
) -> DatasetBinding:
    return DatasetBinding(dataset, peer, tool, mode, watermark_field, fallback_tools, note)


DATASET_CATALOG: dict[str, DatasetBinding] = {
    b.dataset: b
    for b in (
        # ---- TickHub-CN：行情与短线数据（工具名见 th-cn tools/list）----
        _b("daily_k", "tickhub", "tencent_kline", "append", "trade_date",
           ("tdx_daily_package",), "日 K 前复权；盘后全市场包为备源"),
        _b("limit_pool", "tickhub", "limit_up_pool", "replace", "trade_date",
           ("ths_limit_up_pool",), "涨停池：连板数参与天梯，封板时间随个股透传（缺失为 null）"),
        _b("broken_board", "tickhub", "broken_board_pool", "replace", "trade_date", note="炸板池"),
        _b("yesterday_limit_up", "tickhub", "yesterday_limit_up_pool", "replace", "trade_date",
           note="晋级率输入"),
        _b("sentiment", "tickhub", "market_sentiment", "replace", "trade_date",
           note="情绪温度 / 晋级率，程序计算，AI 不改数"),
        _b("dragon_tiger", "tickhub", "dragon_tiger", "replace", "trade_date",
           ("dragon_tiger_backup",), "备源为交易所官方榜"),
        _b("dragon_tiger_seats", "tickhub", "dragon_tiger_seats", "replace", "trade_date",
           note="席位结构"),
        _b("industry_board", "tickhub", "industry_boards", "replace", "trade_date",
           note="行业板块排行"),
        _b("concept_board", "tickhub", "concept_boards", "replace", "trade_date",
           note="概念板块排行"),
        _b("fund_flow", "tickhub", "stock_fund_flow", "replace", "trade_date", note="个股资金流"),
        _b("hot_rank", "tickhub", "ths_hot_rank", "replace", "trade_date",
           ("eastmoney_hot_rank",), "热榜"),
        # ---- QuotaFuse Wire：题材段叙事（可选）----
        # 摘要为主、时间线为同 peer 备源（DEC-008）：两者都是 Wire 的只读工具，
        # 不跨供应商，符合 DEC-006「主备同 peer」。
        _b("theme_news", "wire", "wire_get_digest", "replace", "data_as_of",
           ("wire_get_timeline",), "只做叙事与时间线，不产出任何数值指标"),
        # ---- StockSenate：合议结果只读（可选）----
        _b("council_memo", "senate", "report_query", "replace", "data_as_of",
           note="只读报告，不在本项目内复现辩论/风控流程"),
    )
}

# 工具名尚未在真实 peer 上确认的数据集：`ldst doctor` 会探**一次** tools/list，
# 连得上就按实际工具名下结论（在列表里 = 已联调、不在 = 明确报工具不存在），
# 连不上只说「peer 不可达」。本集合只影响 doctor 的 warning 措辞，不影响 ok 与退出码。
PROVISIONAL_TOOLS: frozenset[str] = frozenset({"report_query"})


def owner_of(dataset: str) -> DatasetBinding:
    """取数据集归属；未登记直接拒绝，防止有人顺手写个新采集器。"""
    try:
        return DATASET_CATALOG[dataset]
    except KeyError:
        raise BoundaryViolation(
            f"数据集 {dataset!r} 未在 DATASET_CATALOG 登记来源——"
            "请先确认由 TickHub-CN / Wire / StockSenate 哪个工具提供，不要自建采集"
        ) from None


def unregistered(datasets: Any) -> list[str]:
    return [name for name in datasets if name not in DATASET_CATALOG]


# --------------------------------------------------------------------------- #
# 采集禁令：源码里不许出现自建采集
# --------------------------------------------------------------------------- #

# 采集类第三方库：出现即视为重复实现数据底座
FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "akshare",
    "tushare",
    "baostock",
    "efinance",
    "adata",
    "pywencai",
    "pytdx",
    "mootdx",
    "easyquotation",
    "qstock",
    "jqdatasdk",
)

# 行情站点：直连即绕过生态（配置与测试 fixture 里也不该出现）
FORBIDDEN_HOSTS: tuple[str, ...] = (
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "qt.gtimg.cn",
    "web.ifzq.gtimg.cn",
    "hq.sinajs.cn",
    "money.finance.sina.com.cn",
    "api.tushare.pro",
    "quotes.money.163.com",
)


@dataclass
class ScanResult:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 链路配置问题（catalog ↔ yaml 双向一致性），doctor 单独成一项检查
    chain: list[ChainProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def detail(self) -> str:
        return "未发现自建采集" if self.ok else "；".join(self.violations[:3])


def scan_no_collection(
    root: Any, *, skip_parts: tuple[str, ...] = ("tests", ".venv")
) -> ScanResult:
    """扫描源码树，找采集库导入与行情站点直连。

    导入检测走 AST（`import` / `from ... import` / `importlib.import_module()` /
    `__import__()`），不再靠子串匹配——`importlib.import_module("akshare")`
    `__import__('tushare')` 这类写法原先能直接绕过（audit R-4）。源码无法解析
    时退回到子串匹配（宁可误报，不可漏报）。站点直连仍按子串检查（URL 是字符串，
    不经过 import 语义）。

    只扫 `.py`；跳过测试与虚拟环境（测试里需要引用域名做禁令断言），
    也跳过本模块自身——禁用清单就写在这里，否则等于自己举报自己。
    """
    from pathlib import Path

    base = Path(root)
    own = Path(__file__).resolve()
    result = ScanResult()
    for path in base.rglob("*.py"):
        if any(part in path.parts for part in skip_parts):
            continue
        if path.resolve() == own:
            continue
        try:
            label = str(path.relative_to(base))
        except ValueError:  # pragma: no cover - base 之外的路径
            label = path.name
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()

        modules = _imported_modules(text, filename=label)
        if modules is None:  # 无法解析：退回子串匹配
            for lib in FORBIDDEN_IMPORTS:
                if f"import {lib}" in lowered or f"from {lib}" in lowered:
                    result.violations.append(f"{label}: 导入了采集库 {lib}")
        else:
            for module, lineno in modules:
                hit = _forbidden_library(module)
                if hit is not None:
                    result.violations.append(f"{label}:{lineno}: 导入了采集库 {hit}")

        for host in FORBIDDEN_HOSTS:
            if host in lowered:
                result.violations.append(f"{label}: 直连行情站点 {host}")
    return result


def _imported_modules(text: str, *, filename: str = "<unknown>") -> list[tuple[str, int]] | None:
    """AST 提取所有导入的模块名（含动态导入）；语法错误返回 None。"""
    import ast

    try:
        tree = ast.parse(text, filename=filename)
    except (SyntaxError, ValueError):
        return None
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.append((node.module, node.lineno))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in {"importlib.import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((first.value, node.lineno))
    return found


def _call_name(func: Any) -> str:
    """还原调用目标名：`importlib.import_module` → 点分名，`f()` → `f`。"""
    import ast

    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _forbidden_library(module: str) -> str | None:
    """模块名（含子模块）是否命中采集禁令：`akshare.stock` 也算 `akshare`。"""
    root = module.split(".", 1)[0].strip().lower()
    return root if root in FORBIDDEN_IMPORTS else None


# --------------------------------------------------------------------------- #
# 程序算数、AI 解读：AI 只能写叙述
# --------------------------------------------------------------------------- #

# AI 允许写入的键（叙述与证据），数字一律由 core 计算
AI_WRITABLE: frozenset[str] = frozenset(
    {"narrative", "commentary", "highlights", "risks", "evidence", "degraded", "model"}
)


def assert_narrative_only(ai_payload: Mapping[str, Any], metrics: Mapping[str, Any]) -> None:
    """AI 输出只能落在 AI_WRITABLE 且不得与指标键重名。"""
    illegal = set(ai_payload) - AI_WRITABLE
    if illegal:
        raise BoundaryViolation(
            "AI 输出含非叙述字段：" + ", ".join(sorted(illegal)) + "（数字必须由程序计算）"
        )
    clash = set(ai_payload) & set(metrics)
    if clash:
        raise BoundaryViolation(
            "AI 试图覆盖程序计算结果：" + ", ".join(sorted(clash))
        )


def merge_narrative(metrics: Mapping[str, Any], ai_payload: Mapping[str, Any]) -> dict[str, Any]:
    """合并：指标原样保留，AI 只追加叙述块。"""
    assert_narrative_only(ai_payload, metrics)
    merged: dict[str, Any] = dict(metrics)
    merged["ai"] = dict(ai_payload)
    return merged


# --------------------------------------------------------------------------- #
# 边界自检（doctor / 测试共用）
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ChainProblem:
    """一条链路配置问题：定位到「哪份文件、哪条记录、哪个字段」。

    配置类问题必须能直接改，所以不用「某处不一致」这种笼统说法（DEC-008）。
    """

    where: str
    record: str
    field: str
    detail: str

    def __str__(self) -> str:
        return f"{self.where}: {self.record}.{self.field} {self.detail}"

    def as_dict(self) -> dict[str, str]:
        return {
            "where": self.where,
            "record": self.record,
            "field": self.field,
            "detail": self.detail,
        }


def chain_problems(
    sources_doc: Mapping[str, Any], sync_doc: Mapping[str, Any]
) -> list[ChainProblem]:
    """校验 `sources.yaml` / `sync.yaml` 与 `DATASET_CATALOG` 的**双向**一致性。

    这是「配置里写的链路」与「门禁允许的链路」的唯一比较口径：
    `ldst doctor`、`ldst sources validate` 与一致性测试都调这一个函数（DEC-008）。

    正向（每条 yaml 记录）：
    1. `dataset` 必须在 `DATASET_CATALOG` 登记；
    2. `via` 必须等于该数据集登记的 peer（主备同 peer，DEC-006 闸门 3）；
    3. `tool` 必须在该数据集允许的工具集合内（登记主工具 + 登记备工具）；
    4. `primary: true` 的记录，工具必须是登记主工具；
    5. `fallback` 引用的 id 必须存在、同 dataset、且落在登记 peer 内。

    反向（每个目录条目 / 每个同步声明）：
    6. `DATASET_CATALOG` 每个数据集的登记主工具，必须在 `sources.yaml` 有一条 primary 记录；
    7. `sync.yaml` 的数据集必须已登记，主源必须存在且属于同一数据集。
    """
    problems: list[ChainProblem] = []
    sources = list(sources_doc.get("sources") or [])
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in sources:
        sid = str(item.get("id") or "")
        if not sid:
            problems.append(ChainProblem("sources.yaml", "(无 id)", "id", "记录缺少 id"))
            continue
        if sid in by_id:
            problems.append(ChainProblem("sources.yaml", sid, "id", "source id 重复定义"))
        by_id[sid] = item

    primaries: dict[str, list[str]] = {}
    for sid, item in by_id.items():
        dataset = str(item.get("dataset") or "")
        via = str(item.get("via") or "")
        tool = str(item.get("tool") or "")
        if dataset not in DATASET_CATALOG:
            problems.append(
                ChainProblem(
                    "sources.yaml", sid, "dataset",
                    f"{dataset or '(空)'} 未在 DATASET_CATALOG 登记生态归属",
                )
            )
            continue
        binding = DATASET_CATALOG[dataset]
        if via != binding.peer:
            problems.append(
                ChainProblem(
                    "sources.yaml", sid, "via",
                    f"写了 {via or '(空)'}，但 {dataset} 归属 {binding.peer}",
                )
            )
        allowed = (binding.tool, *binding.fallback_tools)
        if tool not in allowed:
            problems.append(
                ChainProblem(
                    "sources.yaml", sid, "tool",
                    f"{tool or '(空)'} 不在 {dataset} 的登记工具 {list(allowed)} 内",
                )
            )
        if item.get("primary"):
            primaries.setdefault(dataset, []).append(sid)
            if tool != binding.tool:
                problems.append(
                    ChainProblem(
                        "sources.yaml", sid, "primary",
                        f"标为主源但工具是 {tool or '(空)'}，{dataset} 的主工具应为 "
                        f"{binding.tool}",
                    )
                )
        for fb in item.get("fallback") or []:
            fid = str(fb)
            other = by_id.get(fid)
            if other is None:
                problems.append(
                    ChainProblem("sources.yaml", sid, "fallback", f"引用的 {fid} 不存在")
                )
                continue
            other_dataset = str(other.get("dataset") or "")
            if other_dataset != dataset:
                problems.append(
                    ChainProblem(
                        "sources.yaml", sid, "fallback",
                        f"引用的 {fid} 属于 {other_dataset or '(空)'}，不是 {dataset}",
                    )
                )
            other_via = str(other.get("via") or "")
            if other_via != binding.peer:
                problems.append(
                    ChainProblem(
                        "sources.yaml", sid, "fallback",
                        f"引用的 {fid} 走 {other_via or '(空)'}，跨 peer（{dataset} 归属 "
                        f"{binding.peer}）",
                    )
                )

    for dataset, ids in primaries.items():
        if len(ids) > 1:
            problems.append(
                ChainProblem(
                    "sources.yaml", dataset, "primary",
                    f"有多个主源：{', '.join(sorted(ids))}",
                )
            )

    # 反向：目录里声明的主工具必须在配置里有 primary 记录，否则门禁允许的链路
    # 实际从未被配置使用（正是本次 theme_news 事故的形态）。
    for dataset, binding in DATASET_CATALOG.items():
        owners = [
            sid for sid, item in by_id.items()
            if str(item.get("dataset") or "") == dataset and item.get("primary")
        ]
        tools = [str(by_id[sid].get("tool") or "") for sid in owners]
        if binding.tool not in tools:
            problems.append(
                ChainProblem(
                    "DATASET_CATALOG", dataset, "tool",
                    f"登记主工具 {binding.tool} 在 sources.yaml 没有 primary 记录"
                    f"（现有：{', '.join(tools) or '无'}）",
                )
            )

    declared = (sync_doc.get("sync") or {}).get("datasets") or {}
    for name, cfg in declared.items():
        if name not in DATASET_CATALOG:
            problems.append(
                ChainProblem("sync.yaml", str(name), "dataset", "无生态归属")
            )
            continue
        primary = str((cfg or {}).get("primary") or "")
        item = by_id.get(primary)
        if item is None:
            problems.append(
                ChainProblem(
                    "sync.yaml", str(name), "primary",
                    f"主源 {primary or '(空)'} 不在 sources.yaml",
                )
            )
        elif str(item.get("dataset") or "") != name:
            problems.append(
                ChainProblem(
                    "sync.yaml", str(name), "primary",
                    f"{primary} 属于 {item.get('dataset')}，不能用于 {name}",
                )
            )

    return problems


def audit_sources(sources_doc: Mapping[str, Any], sync_doc: Mapping[str, Any]) -> list[str]:
    """`chain_problems()` 的字符串视图（doctor 与测试共用的断言形态）。"""
    return [str(problem) for problem in chain_problems(sources_doc, sync_doc)]


def audit(config_root: Any = None) -> ScanResult:
    """体检项：源码无采集 + 配置里数据集都有归属、主备不越界。"""
    from pathlib import Path

    import yaml

    from ladderstock.cli.paths import config_dir, repo_root

    root = Path(config_root) if config_root is not None else repo_root()
    result = scan_no_collection(root)

    base = config_dir()
    docs: dict[str, Any] = {}
    for name in ("sources", "sync"):
        path = base / f"{name}.yaml"
        if path.is_file():
            docs[name] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "sources" in docs and "sync" in docs:
        result.chain.extend(chain_problems(docs["sources"], docs["sync"]))
        result.violations.extend(str(problem) for problem in result.chain)
    elif "sync" in docs:
        unknown = unregistered((docs["sync"].get("sync") or {}).get("datasets") or {})
        result.violations.extend(f"sync.yaml: 数据集 {name} 无生态归属" for name in unknown)

    # 待联调工具（PROVISIONAL_TOOLS）**不在这里**下结论：本函数保持离线，
    # 「联调到底成没成」由 `ldst doctor` 探一次 tools/list 后判定
    # （`data.provider_registry.provisional_tool_warnings`）。
    return result
