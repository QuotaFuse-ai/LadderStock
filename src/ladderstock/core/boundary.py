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
4. `assert_provenance()` —— 数字必须有出处；没有出处的数值字段进不了 `verified` 集。

任何越界统一抛 `BoundaryViolation`（属于施工期契约错误，不落到对外退出码）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

Mode = Literal["append", "replace"]


class BoundaryViolation(RuntimeError):
    """破坏四层边界：自建采集、重复合议、或让 AI 覆盖程序算数结果。"""


# --------------------------------------------------------------------------- #
# 生态三件套
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Peer:
    """一个生态 peer 的注册信息。

    QUOT-403：注册表的唯一真源是 `config/sources.yaml` 的 `peers` 块；
    本 dataclass 只是它的内存形态。`mcp_name` 可为 None（该 peer 没有 MCP 面，
    如 FinanceCoffer 只提供 HTTP 服务）。
    """

    key: str
    product: str
    package: str
    cli: str
    mcp_name: str | None
    default_port: int
    role: str
    consumption: str
    #: 该 peer 真实提供的健康检查路径（探测用；QUOT-403 起不再写死 /healthz）
    health_path: str = "/healthz"
    #: GitHub 仓库名（owner/name），QUOT-402 版本三态检测用；None 表示不检测
    repo: str | None = None


#: 代码内置的最小默认注册表：只在 sources.yaml 缺失 / 写坏时兜底（QUOT-403 验收 2：
#: 服务不许崩、PEERS 不许变空）。**新增 peer 的正路是改 yaml，不是改这张表**——
#: 这里的条目只保证「配置坏了控制面还能亮出基线卡片」。
_PEER_FALLBACKS: dict[str, Peer] = {
    "tickhub": Peer(
        key="tickhub",
        product="TickHub-CN",
        package="tickhub-cn",
        cli="th-cn",
        mcp_name="tickhub-cn",
        default_port=8000,
        role="数据底座：行情 / 涨停池 / 龙虎榜 / 情绪指标 / 资金",
        consumption="只消费",
        health_path="/healthz",
        repo="QuotaFuse-ai/TickHub-CN",
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
        health_path="/healthz",
        repo="QuotaFuse-ai/TickHub-US",
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
        health_path="/api/health",
        repo="QuotaFuse-ai/quotafuse-wire",
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
        health_path="/healthz",
        repo="QuotaFuse-ai/StockSenate",
    ),
    "financecoffer": Peer(
        key="financecoffer",
        product="FinanceCoffer",
        package="financecoffer",
        cli="fc",
        mcp_name=None,
        default_port=8100,
        role="数据存储：结构化宽表 / 公告检索 / 风险",
        consumption="只消费",
        health_path="/health",
        repo="QuotaFuse-ai/FinanceCoffer",
    ),
    "mem0": Peer(
        key="mem0",
        product="Mem0",
        package="qf-mem0",
        cli="docker",
        mcp_name=None,
        default_port=9100,
        role="助手记忆：add / search / 历史回放（本地 bge-small-zh 向量，云骏 LLM）",
        consumption="基础设施",
        health_path="/configure",
        repo=None,
    ),
    # QUOT-485：ProxyFleet 升级登记为 peer（控制面纳管 + MCP 薄封装）。
    # 它仍不是取数通道——DATASET_CATALOG 不给它任何数据集，sources 不许 via 它，
    # 四层边界的保护点在数据集目录（DEC-050/051 的只读口径由此单升级为纳管）。
    "proxyfleet": Peer(
        key="proxyfleet",
        product="ProxyFleet",
        package="proxyfleet",
        cli="pfctl",
        mcp_name=None,
        default_port=8788,
        role="代理池：节点采集 / 健康检查 / 出口网关（8788 控制台 · 8789 网关）",
        consumption="基础设施",
        health_path="/readyz",
        repo=None,
    ),
    "qdrant": Peer(
        key="qdrant",
        product="Qdrant",
        package="qf-qdrant",
        cli="docker",
        mcp_name=None,
        default_port=6333,
        role="知识库向量层：档案向量存储 / 语义检索（REST 6333 · MCP 9300）",
        consumption="基础设施",
        health_path="/collections",
        repo=None,
    ),
}


def build_peers(
    peers_block: Any,
    *,
    fallbacks: Mapping[str, Peer] | None = None,
) -> tuple[dict[str, Peer], list[str]]:
    """从 sources.yaml 的 peers 块构造注册表（纯函数，测试与模块加载共用）。

    返回 `(peers, warnings)`：块缺失/为空/整体写坏 → 整表回退内置默认并给 warning；
    单条写坏 → 跳过该条、其余照常、给 warning。任何情形都不抛异常、不返回空表
    （QUOT-403 验收：不许崩、不许变空）。

    yaml 未携带的字段（consumption、缺省的 default_port / health_path / role）
    从内置默认按 key 回填；未知 key 的 role 缺省为空串、consumption 缺省「只消费」。
    """
    base = _PEER_FALLBACKS if fallbacks is None else fallbacks
    warnings: list[str] = []
    if not isinstance(peers_block, Mapping) or not peers_block:
        return dict(base), ["config/sources.yaml 的 peers 块缺失或为空，回退内置默认注册表"]

    peers: dict[str, Peer] = {}
    for key, item in peers_block.items():
        key = str(key)
        if not isinstance(item, Mapping):
            warnings.append(f"peers.{key} 不是键值映射，已跳过该条")
            continue
        product = str(item.get("product") or "").strip()
        package = str(item.get("package") or "").strip()
        # cli 只用于文档/手工启动提示，控制面不执行它；设置页导入（QUOT-420）
        # 不允许携带命令字段，因此这里允许缺省为空，不把已登记的新 peer 丢掉。
        cli = str(item.get("cli") or "").strip()
        if not (product and package):
            warnings.append(
                f"peers.{key} 缺 product / package 中的必填字段，已跳过该条"
            )
            continue
        fallback = base.get(key)
        mcp_name = item.get("mcp_name")
        port = item.get("default_port")
        try:
            default_port = int(port) if port is not None else (
                fallback.default_port if fallback else 0
            )
        except (TypeError, ValueError):
            warnings.append(f"peers.{key}.default_port 不是整数，回退默认值")
            default_port = fallback.default_port if fallback else 0
        role = str(item.get("role") or "").strip() or (fallback.role if fallback else "")
        health_path = str(item.get("health_path") or "").strip() or (
            fallback.health_path if fallback else "/healthz"
        )
        repo = item.get("repo")
        peers[key] = Peer(
            key=key,
            product=product,
            package=package,
            cli=cli,
            mcp_name=str(mcp_name) if mcp_name else None,
            default_port=default_port,
            role=role,
            consumption=fallback.consumption if fallback else "只消费",
            health_path=health_path,
            repo=str(repo) if repo else None,
        )
    if not peers:
        return dict(base), [*warnings, "peers 块没有任何合法条目，回退内置默认注册表"]
    return peers, warnings


def load_peers_from_dir(config_path: Any) -> tuple[dict[str, Peer], list[str]]:
    """读 `<config_path>/sources.yaml` 的 peers 块并构造注册表。

    文件缺失 / YAML 解析失败都按「块缺失」处理：回退内置默认 + warning 带原因
    （QUOT-403 验收 2）。模块加载与本测试共用这一个入口。
    """
    import yaml

    path = Path(config_path) / "sources.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return dict(_PEER_FALLBACKS), [f"config/sources.yaml 读取失败（{exc}），回退内置默认注册表"]
    block = document.get("peers") if isinstance(document, dict) else None
    return build_peers(block)


def _load_module_peers() -> tuple[dict[str, Peer], tuple[str, ...]]:
    """模块加载期构造 PEERS：优先真实配置目录，失败兜底内置默认。"""
    try:
        from ladderstock.cli.paths import config_dir

        peers, warnings = load_peers_from_dir(config_dir())
    except Exception as exc:  # noqa: BLE001 - 注册表加载绝不能让 import 崩掉
        return dict(_PEER_FALLBACKS), (f"peer 注册表加载异常（{exc}），回退内置默认",)
    return peers, tuple(warnings)


PEERS, PEER_LOAD_WARNINGS = _load_module_peers()


# --------------------------------------------------------------------------- #
# 控制面元数据（DEC-014 §1/§3/§5）
# --------------------------------------------------------------------------- #
#
# `Peer` 只回答「生态里是谁」；控制面还要回答「装在哪、跑哪个端口、怎么启停、
# 能改哪些键」。这些**同样是白名单**：控制面只认这张表，URL/请求参数永远落不到
# 自由字符串上（DEC-014 硬约束 2）。
#
# 端口口径以 DEC-013 / DEC-047 为准（TickHub-US 的 8001 被 WSL 转发占用 → 8002；
# 本机构建没有 US admin API → 不分配管理端端口；8788/8789 归 ProxyFleet 网关，
# Senate WebUI 改用 8786）。`import_package` 是 `.venv` 里真正可 import
# 的名字，与分发名 `package`（带连字符）不是一回事。

#: 控制面默认只写这一个文件（DEC-014 硬约束 4：凭据只进 env、0600）。
#: 但各 peer 读取的 dotenv 文件名并不一致（见 `PeerControl.env_filename` 的实测
#: 依据）：CN / US / Senate 只读 `.env`，Wire 读 `.env.local` 且优先级更高，
#: FinanceCoffer 的生效文件在子目录 `deploy/.env`（WSL 内 docker compose 的
#: environment_file 标签实测，容器无仓库挂载、process env 优先）。
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
    #: 分层 dotenv 的底层文件名（QUOT-481 缺陷 2）：非空时按 Wire 的
    #: `config/dotenv.py` 语义读取——先读底稿 `<directory>/<env_layer_base>`
    #: 打底，再用 `env_filename` 覆盖同名键。None = 单文件（其余 peer 的语义）。
    #: 写回永远只改生效层 `env_filename`；底稿只读不进磁盘、也绝不重写。
    env_layer_base: str | None = None
    #: 配置项与目标实现不一致时的说明（如实暴露，不静默）
    config_notes: tuple[str, ...] = ()
    #: GitHub 仓库（`owner/name`），期望版本源（QUOT-402）；None 表示不检测
    repo: str | None = None
    #: 本机分发包名（importlib.metadata 的发现名）；None 则退回 import_package
    dist_name: str | None = None
    #: 控制面是否接管安装/启停（QUOT-403）：False = 只读观测（面板出卡、探健康，
    #: 不给安装/启动/停止按钮）。WSL 里的服务（FinanceCoffer）归此列。
    managed: bool = True
    #: Docker compose 生命周期（QUOT-486 mem0）：启停不走 `.venv/Scripts/` 入口，
    #: 改走 `wsl -d <发行版> -- docker compose -f <directory>/<该文件名> up -d / stop`。
    #: 存 compose 文件名（相对 directory）；None = venv 脚本形态。
    compose_file: str | None = None
    #: Windows 计划任务生命周期（QUOT-485 ProxyFleet）：启停不打进程，改走
    #: `schtasks /change /tn <任务名> /enable|/disable` + `/run|/end` 打 SYSTEM
    #: 垫片任务（先 disable 压住自愈触发再 end，否则 1 分钟内会被自动拉起）。
    #: 存计划任务名；None = 非 task 形态。enable/disable 不改任务账户/触发器。
    task_name: str | None = None
    #: 除 mcp_port / web_port 之外还要探测的端口（QUOT-485 ProxyFleet 的 8789
    #: 出口网关）：面板归属探测与端口冲突检查都要覆盖它们，但不出「打开」按钮。
    extra_ports: tuple[int, ...] = ()
    #: 每条 start_command 负责的服务面（QUOT-481 缺陷 3），与 start_commands 按
    #: 索引一一对齐，取值 "mcp" / "web"。None = 不分面（保持整体口径：要么全
    #: 起、要么全停）。分面后：某一面已在监听时，启动只补跑**未监听面**的命令、
    #: 端口预检也只查那些面——不再要求已服务的面先停下来。
    start_command_faces: tuple[str, ...] | None = None


PEER_CONTROL: dict[str, PeerControl] = {
    "tickhub": PeerControl(
        key="tickhub",
        directory=r"[local-path]\TickHub-CN",
        import_package="tickhub_cn",
        repo="QuotaFuse-ai/TickHub-CN",
        dist_name="tickhub-cn",
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
            "日志键实际是 LOG_LEVEL；MCP_HTTP_PORT、TICKHUB_CN_LOG_LEVEL 按 PRD §5 保留"
            "但当前不生效。",
        ),
    ),
    "tickhub_us": PeerControl(
        key="tickhub_us",
        directory=r"[local-path]\TickHub-US",
        import_package="tickhub_us",
        repo="QuotaFuse-ai/TickHub-US",
        dist_name="tickhub-us",
        mcp_port=8002,
        # 本机构建没有 admin API：给一个端口只会把 ProxyFleet 的 8789 探成「本模块」
        web_port=None,
        web_port_label="管理端（本构建未提供）",
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
            "本机 TickHub-US 构建没有 admin API，ADMIN_HTTP_PORT 无处读取（按 PRD §5 保留）；"
            "控制面因此不探测管理端，也不把 8789 展示成 US 的端口（DEC-047）。",
        ),
    ),
    "wire": PeerControl(
        key="wire",
        directory=r"[local-path]\Wire\quotafuse-wire",
        import_package="qf_wire",
        repo="QuotaFuse-ai/quotafuse-wire",
        dist_name="quota-fuse-wire",
        mcp_port=8791,
        web_port=8790,
        web_port_label="HTTP 服务",
        health_path="/api/health",
        start_commands=(("qfw", "serve"), ("qfw", "mcp", "http")),
        # `qfw serve` 只起 Web/HTTP 面（8790），`qfw mcp http` 只起 MCP 面（8791）：
        # 分面后 web 挂了可以只补跑 serve，不要求 MCP 也停下来（QUOT-481 缺陷 3）
        start_command_faces=("web", "mcp"),
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
        # Wire 的 config/dotenv.py 实测语义：先读 .env 打底，再读 .env.local 覆盖
        # （.env.local 优先）。控制面读值分层合并，写回只进生效层 .env.local。
        env_layer_base=".env",
        env_filename=".env.local",
        config_notes=(
            "Wire 的 /api/health 在 8790（web/server.py:46），8791 是 MCP。",
        ),
    ),
    "senate": PeerControl(
        key="senate",
        directory=r"[local-path]\StockSenate",
        import_package="stock_senate",
        repo="QuotaFuse-ai/StockSenate",
        dist_name="stocksenate",
        mcp_port=8765,
        web_port=8786,
        web_port_label="WebUI",
        health_path="/healthz",
        start_commands=(
            ("ssenate", "mcp", "http", "--host", "127.0.0.1", "--port", "8765"),
            ("ssenate", "webui", "--host", "127.0.0.1", "--port", "8786"),
        ),
        # `ssenate mcp http` 只起 MCP 面（8765），`ssenate webui` 只起 WebUI 面（8786）
        start_command_faces=("mcp", "web"),
        # QUOT-481 缺陷 1：llm_provider / deep_think_llm / quick_think_llm 曾经挂在
        # env 白名单里，但 Senate 只从 config/llm_config.toml 读它们
        # （stock_senate/config/loader.py:31 → [llm] 节），写 env 永远不生效——
        # 「能填但无效」比「不能填」更糟，故从白名单移除，归属写进 config_notes。
        config_keys=("MINIMAX_API_KEY",),
        secret_keys=("MINIMAX_API_KEY",),
        env_filename=".env",
        config_notes=(
            "Senate 的 /healthz 在 WebUI（webui/auth.py:34），且强制鉴权例外仅此路径；"
            "8788 本机归 ProxyFleet REST/管理台，WebUI 改用 8786（DEC-047）。",
            "llm_provider / deep_think_llm / quick_think_llm 由 config/llm_config.toml 的"
            " [llm] 节管理（stock_senate/config/loader.py 直接读该文件，不走环境变量），"
            "本控制面不提供这三个键；要改请直接编辑该 toml 后重启 Senate。",
        ),
    ),
    "financecoffer": PeerControl(
        key="financecoffer",
        directory=r"[local-path]\FinanceCoffer",
        import_package="financecoffer",
        repo="QuotaFuse-ai/FinanceCoffer",
        dist_name="financecoffer",
        mcp_port=8100,
        # FC 没有 MCP 面，8100 就是唯一的 HTTP 服务端口；健康检查打 /health
        web_port=None,
        web_port_label="HTTP 服务（WSL）",
        health_path="/health",
        # 服务跑在 WSL 里，Windows 侧的 .venv/Scripts 里没有可拉起的入口：
        # 控制面只做只读观测与健康探测，绝不接管启停（QUOT-403）。
        start_commands=(),
        # 白名单 26 键 = Config.model_fields 全量 34 键扣除 8 个「写 deploy/.env 也不
        # 生效」的键。逐键证据由脚本核对两道关后生成（对照表随 PR 评论交付）：
        # ① FC 侧真的在读 —— core/config.py 的 Config 字段（行号对照）经
        #    config/loader.py ENV_PREFIX=FC_ + _extract_known 读入；
        # ② 部署形态真的收得到 —— deploy/compose.yaml x-app-env 用 ${FC_...:?} /
        #    ${FC_...:-} 引用同键插值，写 deploy/.env 后重建容器即生效。
        config_keys=(
            "FC_EMBEDDING_DIM",
            "FC_EMBEDDING_PROVIDER",
            "FC_EMBEDDING_MODEL",
            "FC_EMBEDDING_URL",
            "FC_EMBEDDING_API_KEY",
            "FC_MINIO_ACCESS_KEY",
            "FC_MINIO_SECRET_KEY",
            "FC_MINIO_BUCKET",
            "FC_EVENT_CONSUMER",
            "FC_EVENT_STREAM",
            "FC_EVENT_GROUP",
            "FC_ETL_CONSUMER_NAME",
            "FC_ETL_DLQ_STREAM",
            "FC_ETL_CONTROL_KEY",
            "FC_ETL_BATCH_SIZE",
            "FC_ETL_BLOCK_MS",
            "FC_ETL_MAX_ATTEMPTS",
            "FC_ETL_BACKOFF_MS",
            "FC_ETL_CLAIM_IDLE_MS",
            "FC_METRICS_PROFILE",
            "FC_COMPLIANCE_ENABLED",
            "FC_RATE_LIMIT_ENABLED",
            "FC_RATE_LIMIT_PER_MINUTE",
            "FC_MCP_TOKEN",
            "FC_LOG_LEVEL",
            "FC_COLD_AFTER_YEARS",
        ),
        # FC 自身 SECRET_FIELDS 四键（core/config.py:19-21）。FC_DB_URL 不在白名单
        # （DSN 由 compose 从 POSTGRES_* 拼装，写本键无效），自然也不按密钥处理。
        secret_keys=(
            "FC_EMBEDDING_API_KEY",
            "FC_MINIO_ACCESS_KEY",
            "FC_MINIO_SECRET_KEY",
            "FC_MCP_TOKEN",
        ),
        has_wheel_dist=False,
        # 生效 env-file 实测是 deploy/.env 而不是仓库根的 .env：FC 跑在 WSL 内的
        # docker compose（project financecoffer），compose 容器 inspect 标签
        # environment_file=/mnt/d/workplace/FinanceCoffer/deploy/.env，容器既没有
        # 挂载仓库、CWD（/app）下也没有 .env，而 process env 优先级高于 .env——
        # 写仓库根 .env 等于没写，故按实测路径落盘到这个子目录文件。
        env_filename="deploy/.env",
        managed=False,
        config_notes=(
            "FinanceCoffer 服务跑在 WSL（127.0.0.1:8100），控制面不接管安装/启停；"
            "启停与治理见 QUOT-404。",
            "/health 回 status=degraded（如 embedding unreachable）时面板如实显示，"
            "不是本单要修的故障。",
            "白名单 26 键 = core/config.py 的 Config.model_fields 全量 34 键，扣除 8 个"
            "容器部署形态下写 deploy/.env 不生效的键（逐键「键名 → config.py:行」对照"
            "表由脚本生成，见 PR 评论的脚本与输出）。",
            "扣除的 8 键及依据（deploy/compose.yaml x-app-env 的值表达式，行号实测）："
            "FC_DB_URL（:24，由 POSTGRES_USER/PASSWORD/DB 拼装，写本键会被覆盖）；"
            "FC_REDIS_URL（:25，字面量 redis://redis:6379/1）；"
            "FC_MINIO_ENDPOINT（:31，字面量 minio:9000）；"
            "FC_MINIO_AUTOSTART（:38，字面量 false）；"
            "FC_MINIO_BINARY/DATA_DIR/LOG_FILE/CONSOLE_ENDPOINT（:41-44，虽经 ${VAR:-} "
            "插值，但仅本地进程形态消费，容器 AUTOSTART 钉死 false 时是 no-op）。",
            "生效路径实测（2026-11）：WSL 内 docker compose 托管，容器 inspect 标签 "
            "environment_file=/mnt/d/workplace/FinanceCoffer/deploy/.env，compose 把它渲染进 "
            "api/worker 的 environment。保存 deploy/.env 后不会即时生效——需在 WSL 侧执行 "
            "docker compose --env-file deploy/.env -f deploy/compose.yaml up -d 重建容器才生效；"
            "该启停不属于控制面（QUOT-403/404）。",
            "密钥口径 = FC 自身 SECRET_FIELDS 四键（FC_EMBEDDING_API_KEY / "
            "FC_MINIO_ACCESS_KEY / FC_MINIO_SECRET_KEY / FC_MCP_TOKEN）；"
            "FC_REDIS_URL 本机 Redis 无认证、也不在白名单，不按密钥处理。",
            "vector/chunking.py:135 直读 FC_TOKENIZER_PATH / FC_EMBEDDING_MODEL_DIR（不走 "
            "loader 的 .env 链路）；FC_WEBHOOK_TOKEN / FC_DRILL_TARGET_DSN / "
            "FC_RESTORE_DSN / FC_STATE_DIR 是运维脚本键、不在 Config.model_fields 内："
            "均不进白名单，写本文件对它们不生效。",
            "config/loader.py 的 DEFAULT_ENV_FILE（.env）只在进程 CWD 下解析；FC 实际经 "
            "compose 的 process env 取值，仓库根 .env 的运行时不读它。",
        ),
    ),
    "mem0": PeerControl(
        key="mem0",
        directory=r"[modules-path]\Mem0",
        # mem0 是容器化服务（OPENAI_API_KEY 只从 Windows 机器级环境变量注入），
        # 没有 pip 分发，也不走 .venv：compose 分支不查 import（见 control.py）。
        import_package="",
        repo=None,
        dist_name=None,
        # 容器映射 9100→8000（mem0 API，本 peer 的服务端口）；3200→3000（dashboard）
        mcp_port=9100,
        web_port=3200,
        web_port_label="Dashboard",
        # 实测（curl http://127.0.0.1:9100/configure 200）：mem0 OSS server 没有
        # 专用 /healthz——/health /healthz /metrics 均 404；/configure 是轻量只读
        # 端点（返回当前 vector_store/llm/embedder 配置），用它当健康探针。
        health_path="/configure",
        start_commands=(),
        config_keys=(
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "MEM0_DEFAULT_LLM_MODEL",
            "MEM0_EMBEDDER_PROVIDER",
            "MEM0_DEFAULT_EMBEDDER_MODEL",
            "HF_ENDPOINT",
            "POSTGRES_COLLECTION_NAME",
            "APP_DB_NAME",
            "MEM0_TELEMETRY",
            "AUTH_DISABLED",
            "JWT_SECRET",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
        ),
        secret_keys=("OPENAI_API_KEY", "JWT_SECRET", "POSTGRES_PASSWORD"),
        has_wheel_dist=False,
        # compose 读取的就是这个 .env（实测 /configure 的 llm.base_url 即出自它）
        env_filename=".env",
        compose_file="docker-compose.yml",
        config_notes=(
            # 对照表口径（2026-11-09 逐键实测复核）：每个键给「注入侧 + 读取侧」两段
            # 可核验位置。注入侧 = 部署目录 .env 的行号（.env 经 docker-compose.yml:29-30
            # env_file 整文件注入）；读取侧 = 镜像内 /app/<file>.py:行——构建上下文是
            # D:\QuotaFuseModules\Mem0\.build\server\，Dockerfile.patch 以 `COPY . /app/`
            # 打进镜像，磁盘文件行号与容器内同行号一致（docker exec sed 复核过）。
            "OPENAI_BASE_URL：.env:14 → /app/main.py:116；MEM0_DEFAULT_LLM_MODEL：.env:15 → "
            "main.py:118；MEM0_EMBEDDER_PROVIDER：.env:21 → main.py:119；"
            "MEM0_DEFAULT_EMBEDDER_MODEL：.env:22 → main.py:120。",
            "OPENAI_API_KEY：.env 不写（.env:16-18 注明），经 docker-compose.yml:34 的 "
            "environment 段从 Windows 机器级 LDST_LLM_API_KEY 透传 → main.py:115（密钥键）。",
            "POSTGRES_COLLECTION_NAME：.env:28 → main.py:113；APP_DB_NAME：.env:29 + "
            "docker-compose.yml:36 → db.py:12；MEM0_TELEMETRY：.env:11 → telemetry.py:27；"
            "AUTH_DISABLED：.env:10 → auth.py:20；JWT_SECRET：.env:9 → auth.py:15（密钥键）。",
            "POSTGRES_USER：.env:5 + docker-compose.yml:56 → main.py:111 / db.py:10；"
            "POSTGRES_PASSWORD：.env:6 + docker-compose.yml:57 → main.py:112 / db.py:11"
            "（密钥键）。",
            "HF_ENDPOINT：.env:24 → 容器内 huggingface_hub/constants.py:69"
            "（ENDPOINT = os.getenv(\"HF_ENDPOINT\", ...)，镜像 site-packages 固化）。",
            "MEM0_DEFAULT_EMBEDDER_DIM 已移出白名单（2026-11-09 实测）：容器内 mem0ai 2.2.1 "
            "包与 /app server 源码 grep 均无读取处，dims 由 main.py:126 在 huggingface 分支"
            "硬编码 512——.env 保留该键仅为部署形态留档，改它不生效，不允许面板可改。",
            "启停形态：`wsl -d <默认发行版> -- docker compose -f "
            "<directory>/docker-compose.yml up -d / stop`，compose project 名 qf-mem0"
            "（与 Multica 的 mem0-dev 物理隔离：独立端口 9100/3200/8532、独立卷、"
            "集合 ladderstock_memories）。发行版名不写死：LDST_WSL_DISTRO > wsl -l -v 默认。",
            "supervisor 拉起（QUOT-486 终审补齐）：机器级计划任务「LadderStock Modules」的 "
            "start_all.cmd 以 :ensure 9100 幂等分支调用 ops/peers/start_mem0.cmd（wsl compose "
            "up -d，发行版动态解析），重启后由 supervisor 拉起，非本模块开机自启（D6）。",
            "embedder 必须留在本地 huggingface BAAI/bge-small-zh-v1.5（云骏无 embedding "
            "接口，强改 OpenAI embedding 会 403）；LLM 走云骏 deepseek-v4.1-flash-1017。",
        ),
    ),
    # QUOT-485：ProxyFleet 从「只读观测的组件」升级为「纳管 peer」——控制面出卡、
    # 起停走 SYSTEM 计划任务垫片（task 形态），见 PEER_CONTROL 的 task_name 分支。
    "proxyfleet": PeerControl(
        key="proxyfleet",
        directory=r"[local-path]\ProxyFleet",
        # 生命周期走 Windows 计划任务垫片：控制面不查 import、不装 wheel，
        # 目录在位即视为已安装（与 compose 分支同构，QUOT-485）。
        import_package="",
        repo=None,
        dist_name=None,
        # 8788 同时是 REST API 与浏览器控制台（api.py："/" 挂控制台 HTML、/v1/* 是 API），
        # 8789 是常驻出口网关（config.py gateway.port）。8788 给 web_port 以出「打开」按钮，
        # 8789 只探测归属、不打开（进 extra_ports）。
        mcp_port=8788,
        web_port=8788,
        web_port_label="控制台 / REST",
        extra_ports=(8789,),
        # 实测（curl 127.0.0.1:8788/readyz）：ready = biz_ok >= 1，未就绪回 503，
        # 免鉴权；/healthz /health 均 404。控制面探针据此判「活着」。
        health_path="/readyz",
        start_commands=(),
        # 配置真源是 config.json + PF_<SECTION>_<KEY> 环境变量覆盖（proxyfleet/config.py:184-186），
        # 没有 dotenv 加载链，控制面的 env 白名单模型套不上 → 按 PRD §5「核实不了就空白名单」
        # 置空；键名→文件:行 对照见 config_notes。配置编辑在本 peer 的控制台完成。
        config_keys=(),
        secret_keys=(),
        has_wheel_dist=False,
        env_filename="config.json",
        task_name="ProxyFleet",
        config_notes=(
            "启停形态（QUOT-485）：Windows 计划任务 ProxyFleet（SYSTEM，开机自启 +"
            " 1 分钟自愈重复触发）。起 = schtasks /change /tn ProxyFleet /enable"
            " 再 /run；停 = /change /disable 再 /end——先 disable 压住自愈触发再 end，"
            "否则纯 /end 会在 1 分钟内被自动拉起。enable/disable 不动任务账户、"
            "触发器与权限，不触碰它的运行身份。",
            "账本口径：由垫片拉起的服务在启停账本里记 origin=\"spawned-via-task\"——"
            "控制面只是打了一下计划任务，进程归属属于 SYSTEM 垫片，不冒充直接子进程。",
            "config_keys 白名单置空的原因：ProxyFleet 不读任何 dotenv，配置真源是"
            " config.json（proxyfleet/config.py DEFAULTS）叠加 PF_<SECTION>_<KEY>"
            " 环境变量覆盖（config.py:184-186），控制面的 env 白名单模型不适用；"
            "按 PRD §5「核实不了就空白名单 + 置灰」处理。",
        ),
    ),
    "qdrant": PeerControl(
        key="qdrant",
        directory=r"[modules-path]\Qdrant",
        # 容器化服务（qdrant/qdrant:v1.20.0 + mcp-server-qdrant==0.8.1 docker build）：
        # 没有 pip 分发，compose 分支不查 import（见 control.py），字段留空。
        import_package="",
        repo=None,
        dist_name=None,
        # mcp-server-qdrant 的 streamable-http 面（FASTMCP_SERVER_PORT 默认 9300）
        mcp_port=9300,
        # Qdrant REST 6333 自带 Dashboard Web UI（静态页在容器里）
        web_port=6333,
        web_port_label="Dashboard",
        # 实测：/ 回版本号、/collections 回集合列表 JSON（轻量只读）；
        # mem0 那种没有专用 /healthz 的情形同理，用 /collections 当健康探针。
        health_path="/collections",
        start_commands=(),
        config_keys=(
            "QDRANT_URL",
            "QDRANT_API_KEY",
            "QDRANT_LOCAL_PATH",
            "COLLECTION_NAME",
            "EMBEDDING_PROVIDER",
            "EMBEDDING_MODEL",
            "HF_ENDPOINT",
            "FASTMCP_SERVER_HOST",
            "FASTMCP_SERVER_PORT",
            "QDRANT_SEARCH_LIMIT",
            "QDRANT_READ_ONLY",
        ),
        secret_keys=("QDRANT_API_KEY",),
        has_wheel_dist=False,
        env_filename=".env",
        compose_file="docker-compose.yml",
        config_notes=(
            "启停形态：`wsl -d <默认发行版> -- docker compose -f "
            "<directory>/docker-compose.yml up -d / stop`，compose project 名 qf-qdrant"
            "（与 mem0 的 qf-mem0 物理隔离：独立卷 qf_qdrant_storage / qf_qdrant_hf_cache，"
            "不复用 mem0 的 pgvector）。发行版名不写死：LDST_WSL_DISTRO > wsl -l -v 默认。",
            "集合契约（实测自 mcp-server-qdrant 0.8.1 源码）：①集合必须用具名向量 "
            "fast-bge-small-zh-v1.5（server 按 fast-<模型名> 查询；匿名集合报 "
            "Not existing vector name 400）；②payload 必须是 document/metadata 结构"
            "（payload['document'] KeyError 即结构不符）；③EMBEDDING_MODEL 必须是 "
            "fastembed 注册名 BAAI/bge-small-zh-v1.5（Qdrant/ 前缀同名模型不被 "
            "TextEmbedding 支持）。512 维 / Cosine 建集合时显式指定。",
            "READ_ONLY=true 时 tools/list 只剩 qdrant-find（qdrant-store 被移除）——"
            "助手的只读检索链路；档案层的写入走程序直连 REST upsert，不经 MCP。",
            "本机 WSL docker 实测容器内嵌 DNS 故障（bridge 网络解析 Temporary "
            "failure in name resolution）：docker build / 临时容器跑 fastembed 需加 "
            "--network host 与 HF_ENDPOINT=https://hf-mirror.com；compose 常规启停不受影响。",
        ),
    ),
}


#: 控制面 peer 目录的覆盖入口：固定键，不接受任意字符串（DEC-014 白名单约束）。
PEER_DIR_ENV: dict[str, str] = {
    "tickhub": "LDST_PEER_DIR_TICKHUB",
    "tickhub_us": "LDST_PEER_DIR_TICKHUB_US",
    "wire": "LDST_PEER_DIR_WIRE",
    "senate": "LDST_PEER_DIR_SENATE",
    "financecoffer": "LDST_PEER_DIR_FINANCECOFFER",
    "mem0": "LDST_PEER_DIR_MEM0",
    "proxyfleet": "LDST_PEER_DIR_PROXYFLEET",
    "qdrant": "LDST_PEER_DIR_QDRANT",
}

#: 默认按同级目录探测：把 peer 仓库 clone 到 LadderStock 旁边即可直接安装。
PEER_SIBLING_DIRS: dict[str, str] = {
    "tickhub": "TickHub-CN",
    "tickhub_us": "TickHub-US",
    # Wire 实际嵌套一层：D:\workplace\Wire\quotafuse-wire
    "wire": os.path.join("Wire", "quotafuse-wire"),
    "senate": "StockSenate",
    "financecoffer": "FinanceCoffer",
    # mem0 是独立部署单元：D:\QuotaFuseModules\Mem0（机器级变量 LDST_PEER_DIR_MEM0）
    "mem0": "Mem0",
    # ProxyFleet 与 LadderStock 同级：D:\workplace\ProxyFleet
    "proxyfleet": "ProxyFleet",
    "qdrant": "Qdrant",
}

# --------------------------------------------------------------------------- #
# 工具组件（不是 peer：不提供数据集，只提供生态里的公共能力）
# --------------------------------------------------------------------------- #
#
# peer 是「向谁取数」的边界对象；工具组件是「生态里的基础设施」。历史上二者分开
# 登记：组件只读观测、不代管启停。QUOT-485 起 ProxyFleet 升级为**纳管 peer**
# （进 PEER_CONTROL + sources.yaml 的 peers 块），因此本表当前为空——但机制保留
# （`component_snapshot` / 控制面 components 卡 / 本表的键白名单都还在），
# 未来「只观测不纳管」的基础设施仍可登记到这里。
#
# 注：升级为 peer 不等于让它变成「合法数据来源」——四层边界的保护点在
# DATASET_CATALOG：代理池不被任何数据集绑定，sources 不许写 `via: proxyfleet`，
# 取证走 boundary 校验。DEC-050/051 的「不登记为取数通道」由此单升级为「纳管」。


@dataclass(frozen=True)
class ComponentControl:
    """一个工具组件的只读观测元数据。"""

    key: str
    product: str
    role: str
    directory: str
    console_port: int
    gateway_port: int | None
    health_path: str
    #: 谁负责它的生命周期（控制面只展示，不接管）
    managed_by: str
    notes: tuple[str, ...] = ()


COMPONENTS: dict[str, ComponentControl] = {}

#: 组件目录覆盖入口：与 peer 一样是固定键白名单。
COMPONENT_DIR_ENV: dict[str, str] = {}

COMPONENT_SIBLING_DIRS: dict[str, str] = {}


def _peer_base() -> Path:
    """peer 同级目录探测的基准根（QUOT-569）：`LDST_PEER_ROOT` > `install_root()` >
    已知部署根探测。源码态 install_root 指到仓库父目录，而真实部署可能落在
    别的根（如 D:\\QuotaFuseModules）——探测细节收口在 `core.runtime.peer_root`。
    """
    from ladderstock.core import runtime

    return runtime.peer_root()


def resolve_components(env: Mapping[str, str] | None = None) -> dict[str, ComponentControl]:
    """解析组件目录：环境变量 > LadderStock 同级目录 > 占位符。"""
    values = os.environ if env is None else env
    resolved: dict[str, ComponentControl] = {}
    for key, ctl in COMPONENTS.items():
        directory = (values.get(COMPONENT_DIR_ENV[key]) or "").strip()
        if not directory:
            sibling = _peer_base() / COMPONENT_SIBLING_DIRS[key]
            directory = str(sibling) if sibling.is_dir() else ctl.directory
        resolved[key] = ctl if directory == ctl.directory else replace(ctl, directory=directory)
    return resolved


def resolve_peer_control(env: Mapping[str, str] | None = None) -> dict[str, PeerControl]:
    """解析 peer 目录：环境变量 > LadderStock 同级目录 > 占位符。

    占位符目录不存在时控制面仍只读展示该 peer，只是不出安装按钮。
    """
    values = os.environ if env is None else env
    resolved: dict[str, PeerControl] = {}
    for key, ctl in PEER_CONTROL.items():
        directory = (values.get(PEER_DIR_ENV[key]) or "").strip()
        if not directory:
            sibling = _peer_base() / PEER_SIBLING_DIRS[key]
            directory = str(sibling) if sibling.is_dir() else ctl.directory
        resolved[key] = ctl if directory == ctl.directory else replace(ctl, directory=directory)
    return resolved


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
           ("tdx_daily_package", "em_kline"),
           "日 K 前复权；盘后全市场包与东财长窗口（≥5 年回补）为备源"),
        # K 线图端点（QUOT-410）：与 daily_k 同一工具，但 period / adjust 是
        # **运行时参数**（前端三档周期 × 三档复权按请求透传），不是 sync 写死的
        # `daily + qfq` 口径，所以是独立数据集、独立登记，不共用 daily_k 的链路。
        # 不进 sync.yaml、不落库（现取现回，失败显式空态，见 web/research 的 kline）。
        # 无备源：daily_k 的备工具 `tdx_daily_package` 是盘后全市场**日线**包，
        # 不收 period / adjust 入参，周 / 月 / 复权档挂它当备源必然一起失败
        # （同 DEC-043 拒绝 wire_timeline 进按日链路的理由），fallback 留空。
        _b("kline_chart", "tickhub", "tencent_kline", "replace", "trade_date",
           note="K 线图端点专用出口（period/adjust 运行时透传）；不进 sync 台账、不落库"),
        _b("limit_pool", "tickhub", "limit_up_pool", "replace", "trade_date",
           ("ths_limit_up_pool",), "涨停池：连板数参与天梯，封板时间随个股透传（缺失为 null）"),
        _b("broken_board", "tickhub", "broken_board_pool", "replace", "trade_date", note="炸板池"),
        _b("yesterday_limit_up", "tickhub", "yesterday_limit_up_pool", "replace", "trade_date",
           note="晋级率输入"),
        _b("sentiment", "tickhub", "market_sentiment", "replace", "trade_date",
           note="情绪温度 / 晋级率，程序计算，AI 不改数"),
        # 盘中 6 时点快照（QUOT-286）：只给 `schedule` 的 kind=snapshot 取现价，
        # 不进 `sync.yaml`（快照不落台账）。`timestamp` 是行内行情时间
        # （Tencent 的 YYYYMMDDHHMMSS），**不是交易日水位**——实时报价没有「一天
        # 一条」的水位，硬套 `trade_date` 会让它看起来像可按日增量同步的数据集。
        _b("spot_quote", "tickhub", "tencent_quote_batch", "replace", "timestamp",
           note="实时批量报价（盘中快照用）；不进 sync 台账，timestamp 不作交易日水位"),
        # 指数实时报价（QUOT-380 / QUOT-379 盘点 A-1）：与 `spot_quote` **同构**——
        # 行内没有 `trade_date`，行情时间是 `timestamp`（`YYYYMMDDHHMMSS`），硬套
        # `trade_date` 会让它看起来像可按日增量同步的数据集，所以同样**不进 sync.yaml**。
        # 入参与 `spot_quote` 有一个实测出来的差别：必须带 `sh`/`sz` 前缀（`000001.SH`
        # 与裸 `000001` 都会被解析成平安银行且照旧标 `is_index: true`），
        # 返回行的 `code` 又是**不带前缀**的 6 位，所以按代码匹配要看 `(market, code)`。
        # 口径由调用方 `core/indices.py` 钉住，登记本身不做任何过滤。
        _b("index_quote", "tickhub", "index_quote", "replace", "timestamp",
           note="指数实时报价（首页指数条用）；不进 sync 台账，timestamp 不作交易日水位"),
        # 美股指数实时报价（QUOT-471）：首页指数条的美股 2 格，由 TickHub-US 的
        # `th_us_get_quote` 逐 symbol 取（`DJI` / `IXIC`，一次一个 symbol）。
        # 与 CN 的 `index_quote` **同构**：现取现显、不进 sync.yaml / 不落库，
        # `timestamp`/`fetched_at` 不作交易日水位。回包带 `compliance_tier` 与
        # `warnings`（sina = Tier C），由 `data/adapters/tickhub_us.py` 落进信封。
        _b("index_quote_us", "tickhub_us", "th_us_get_quote", "replace", "timestamp",
           note="美股指数实时报价（首页指数条美股格）；逐 symbol 取，不进 sync 台账"),
        # 港股指数实时报价（QUOT-560）：首页指数条的港股 2 格，同一个 peer 工具
        # `th_us_get_quote` 逐 symbol 取（规范形 `HSI.HK` / `HSTECH.HK`，一次一个）。
        # peer 侧 QUOT-560 加了港股指数别名表后，tencent/sina 免费公开源可取（零 key，
        # Tier C 非商用），回包 `market=HK`；其余口径与 `index_quote_us` 完全同构：
        # 现取现显、不进 sync.yaml / 不落库，`timestamp` 不作交易日水位。
        _b("index_quote_hk", "tickhub_us", "th_us_get_quote", "replace", "timestamp",
           note="港股指数实时报价（首页指数条港股格）；逐 symbol 取，不进 sync 台账"),
        _b("dragon_tiger", "tickhub", "dragon_tiger", "replace", "trade_date",
           ("dragon_tiger_backup",), "备源为交易所官方榜"),
        _b("dragon_tiger_seats", "tickhub", "dragon_tiger_seats", "replace", "trade_date",
           note="席位结构"),
        _b("industry_board", "tickhub", "industry_boards", "replace", "trade_date",
           note="行业板块排行"),
        _b("concept_board", "tickhub", "concept_boards", "replace", "trade_date",
           note="概念板块排行"),
        # 个股↔概念板块归属（QUOT-362）：`stock_concept_blocks(code)` 是**逐只实时快照**，
        # 上游 TTL 60 秒，没有 `since` 入参、也没有「一天一条」的水位——照
        # `_b("spot_quote", ...)` 先例把水位字段写成 `timestamp`（它只表取数时刻，
        # **不作交易日增量起点**），所以 `sync.yaml` 里它是 `optional` 的整批替换项，
        # 代码池来自当日涨停池归档（`codes_from`），不扩全市场。
        # 无备工具：同 peer 的 `em_hot_concept` 是「热门概念榜」不是逐只归属，
        # 换过去等于换口径，不是换源（DEC-006 主备同口径要求）。
        _b("concept_membership", "tickhub", "stock_concept_blocks", "replace", "timestamp",
           note="个股归属的概念板块（逐只快照）；timestamp 只表取数时刻，不作交易日水位"),
        _b("fund_flow", "tickhub", "stock_fund_flow", "replace", "trade_date", note="个股资金流"),
        # 当日实时资金流（QUOT-371）：`stock_fund_flow_batch` 一次 ≤150 只，超出自动分批。
        # 与上面的日线 `fund_flow` **是两个数据集**，口径不混用（DEC-049）：
        # 日线做历史对比，这条供打榜「资金」分项的当日实时值。
        # `timestamp` 只表取数时刻，不当交易日水位——口径同 `spot_quote`。
        _b("fund_flow_realtime", "tickhub", "stock_fund_flow_batch", "replace", "timestamp",
           note="当日实时资金流（批量）；不进 sync 台账，timestamp 不作交易日水位"),
        # ---- R5 产业研究（QUOT-143）：行业画像的四路输入 ----
        _b("sw_industry_map", "tickhub", "sw_industry_history", "append", "trade_date",
           note="申万一级归属（成分表懒缓存，DEC-018）；行内 trade_date = 分类计入日期"),
        _b("finance_indicators", "tickhub", "eastmoney_finance_indicators", "append", "trade_date",
           note="景气聚合输入；行内 trade_date = report_date"),
        _b("valuation_history", "tickhub", "baostock_valuation_history", "append", "trade_date",
           note="估值 / 换手分位输入（北交所无源，实测缺）"),
        _b("industry_fund_flow", "tickhub", "board_fund_flow", "replace", "trade_date",
           note="板块资金流：caliber 随上游 source（东财 BK / 新浪行业），DEC-018 要求逐字段标注"),
        _b("hot_rank", "tickhub", "ths_hot_rank", "replace", "trade_date",
           ("eastmoney_hot_rank",), "热榜"),
        # ---- 资讯雷达（R6 / QUOT-144）：公告 / 快讯 / 新闻 / 研报 ----
        # 全部是 TickHub-CN 已注册的只读工具，本层不采集（DEC-006 闸门 4）。
        # 模式统一 `append`：资讯的时间轴是「越来越多」，不是「按日整替」；
        # 幂等靠 `news.store` 的 (id upsert + url/标题归一化去重)，不靠整日覆盖。
        _b("announcement_cninfo", "tickhub", "cninfo_announcements", "append", "data_as_of",
           ("announcements_backup",), "巨潮公告（沪/深/北）：code 必填"),
        _b("announcement_szse", "tickhub", "szse_announcements", "append", "data_as_of",
           ("announcements_backup",), "深交所公告：仅深市 code"),
        _b("flash_cls", "tickhub", "cls_telegraph", "append", "data_as_of",
           note="财联社 7x24 快讯：无 code 维度，related_codes 靠正文抽取"),
        _b("flash_wallstreetcn", "tickhub", "wallstreetcn_lives", "append", "data_as_of",
           note="华尔街见闻 Live（a-stock 频道）"),
        _b("flash_eastmoney", "tickhub", "eastmoney_global_news", "append", "data_as_of",
           note="东财 7x24 全球快讯"),
        _b("news_eastmoney_stock", "tickhub", "eastmoney_stock_news", "append", "data_as_of",
           note="东财个股新闻：code 必填"),
        _b("news_cctv", "tickhub", "cctv_news", "append", "data_as_of",
           note="新闻联播逐日条目：政策口径的主来源"),
        _b("research_report", "tickhub", "eastmoney_reports", "append", "data_as_of",
           ("sina_research_reports",), "券商研报；新浪研报为同 peer 备源（东财故障时用）"),
        # ---- 事件日历的两个结构化数据源（R6）----
        _b("lockup_calendar", "tickhub", "lockup_expiry", "replace", "trade_date",
           note="限售解禁日历：日历「解禁」段的唯一来源"),
        _b("earnings_preview", "tickhub", "earnings_forecast", "replace", "notice_date",
           note="业绩预告（notice_date 为披露日）——不是「财报预约披露日」，两者不混用（DEC-018）"),
        # ---- 财报前瞻 / 事件回看的数据面（QUOT-446）----
        # 一致预期是**整包快照**（data 里是 consensus / history 两个嵌套数组，不是行列表），
        # 一行 = 一只票某次取数；`replace` 按 trade_date（= 上游 as_of）整日覆盖。
        _b("consensus_forecast", "tickhub", "ths_eps_forecast", "replace", "trade_date",
           note="机构一致预期快照（同花顺）：当期/下期 EPS、区间、机构数、评级；整包一行"),
        # 三只事件型数据集都是「行列表 + 公告日」，`replace` 按公告日分区——
        # 同一天可能有多行（多个股东 / 多次调研 / 多份回购方案），不能用 append 的
        # (code, trade_date) 折叠，否则同一天的事件会被压成一条。
        _b("holder_trades", "tickhub", "holder_trades", "replace", "trade_date",
           note="股东增减持：trade_date = 公告日（notice_date），方向在 payload.direction"),
        _b("institution_survey", "tickhub", "institution_survey", "replace", "trade_date",
           note="机构调研：trade_date = 公告日（notice_date）"),
        _b("share_buyback", "tickhub", "share_buyback", "replace", "trade_date",
           note="股票回购：trade_date = 最新公告日（latest_notice）"),
        # 指数日线（沪深300 等）：事件窗口超额收益的基准。`append` + (code, trade_date)
        # 幂等，增量入参是 `days`（上游按根数回溯），水位仍是 trade_date。
        _b("index_daily", "tickhub", "index_kline", "append", "trade_date",
           ("em_kline",),
           note="指数日线（事件窗口超额收益基准）；code 为指数代码，如 000300"),
        # ---- QuotaFuse Wire：题材段叙事（可选）----
        # 摘要为主、时间线为同 peer 备源（DEC-008）：两者都是 Wire 的只读工具，
        # 不跨供应商，符合 DEC-006「主备同 peer」。
        _b("theme_news", "wire", "wire_get_digest", "replace", "data_as_of",
           ("wire_get_timeline",), "只做叙事与时间线，不产出任何数值指标"),
        # ---- StockSenate：合议结果只读（可选）----
        _b("council_memo", "senate", "stock_senate_report_query", "replace", "data_as_of",
           note="只读报告，不在本项目内复现辩论/风控流程"),
    )
}

#: 资讯雷达（R6 / QUOT-144）统一流的 8 个栏目：`ldst news --source` 的合法取值。
#: 顺序即默认抓取顺序，也是「同稿多源合并」时保留哪一条的优先级——
#: 公告类（交易所/巨潮，置信度 A）优先于快讯类（财联社/见闻，置信度 C）。
NEWS_STREAM_DATASETS: tuple[str, ...] = (
    "announcement_cninfo",
    "announcement_szse",
    "research_report",
    "news_eastmoney_stock",
    "news_cctv",
    "flash_cls",
    "flash_wallstreetcn",
    "flash_eastmoney",
)

#: 事件日历的结构化数据源（不是资讯流成员，按 code + 时间窗直接取）。
NEWS_CALENDAR_DATASETS: tuple[str, ...] = ("lockup_calendar", "earnings_preview")

# 工具名尚未在真实 peer 上确认的数据集：`ldst doctor` 会探**一次** tools/list，
# 连得上就按实际工具名下结论（在列表里 = 已联调、不在 = 明确报工具不存在），
# 连不上只说「peer 不可达」。本集合只影响 doctor 的 warning 措辞，不影响 ok 与退出码。
# QUOT-389：senate 的 `stock_senate_report_query` 已实测在列（5 个工具），全部清空。
PROVISIONAL_TOOLS: frozenset[str] = frozenset()


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
# 数字级出处（R3）：谁给的数、什么时候给的、能不能当已核实
# --------------------------------------------------------------------------- #
#
# `merge_narrative` 只保证「AI 不改数」，不保证「这个数字有出处」。一个没有出处的
# 数字即使逐字正确也不可审计——出了问题追不回是谁给的。所以出处是**独立的一道闸门**：
# 数值字段只有带合法 `Provenance` 才允许进 `verified` 集，其余一律未核实。
#
# 出处由程序写：`provenance` 不在 `AI_WRITABLE` 里，模型既改不了指标，也不能给自己
# 发引用（`assert_narrative_only` 天然挡住，R3 不需要额外机制）。

#: 合法出处族。前四个是生态 peer（`PEERS` 的对外名，带连字符），
#: `computed` / `user` 是本机自有（程序算的 / 人敲的），`web` 是尚未批准的联网通道。
PROVENANCE_SOURCES: tuple[str, ...] = (
    "tickhub-cn",
    "tickhub-us",
    "wire",
    "senate",
    "computed",
    "user",
    "web",
)

#: 走 peer 的出处必须点名工具/端点，否则等于「听说 TickHub 给的」
_PEER_SOURCES: frozenset[str] = frozenset({"tickhub-cn", "tickhub-us", "wire", "senate"})

#: `web` 可以登记出处，但**永远不能当已核实**：联网检索是 DEC-006 / PRD §9 的硬边界，
#: 未获人类批准前，把 web 数值放进 verified 集等于给未批准的通道背书。
_NEVER_VERIFIED_SOURCES: frozenset[str] = frozenset({"web"})

#: 判定「已核实」最少要有的引用要素：何时取 + 数据截至何时
_REQUIRED_WHEN: tuple[str, ...] = ("fetched_at", "as_of")


@dataclass(frozen=True)
class Provenance:
    """一个数字的出处。`value` 必须与它标注的指标值逐字相等（不等即判伪造引用）。"""

    value: float | int | str
    unit: str
    source: str
    fetched_at: str
    as_of: str
    endpoint: str | None = None
    url: str | None = None
    verified: bool = False
    #: 这条出处属于哪个指标键（`values` 反查用，也让 sources 条目自解释）
    metric: str = ""

    def __post_init__(self) -> None:
        if self.source not in PROVENANCE_SOURCES:
            raise BoundaryViolation(
                f"出处族 {self.source!r} 不在登记清单 {list(PROVENANCE_SOURCES)} 内——"
                "新增出处族要先改 DATASET_CATALOG / PEERS，不要在调用处随手填一个字符串"
            )
        if self.value is None:
            raise BoundaryViolation(f"指标 {self.metric or '(未命名)'} 的出处没有值可标")
        if not self.verified:
            return
        if self.source in _NEVER_VERIFIED_SOURCES:
            raise BoundaryViolation(
                f"出处族 {self.source} 未获批准（DEC-006：联网检索属 R4），"
                "不得标记为已核实"
            )
        missing = [name for name in _REQUIRED_WHEN if not str(getattr(self, name) or "").strip()]
        if missing:
            raise BoundaryViolation(
                f"指标 {self.metric or '(未命名)'} 声称已核实但缺引用要素："
                + "、".join(missing)
            )
        if self.source in _PEER_SOURCES and not str(self.endpoint or "").strip():
            raise BoundaryViolation(
                f"指标 {self.metric or '(未命名)'} 走 peer {self.source}，"
                "已核实必须点名 endpoint（工具名），否则追不回是哪次调用给的"
            )
        if self.source not in _PEER_SOURCES and not (
            str(self.url or "").strip() or str(self.endpoint or "").strip()
        ):
            raise BoundaryViolation(
                f"指标 {self.metric or '(未命名)'} 的出处（{self.source}）既无 endpoint 也无 url，"
                "无法点回落点，不得标 verified"
            )

    def as_dict(self) -> dict[str, Any]:
        """落盘 / 注入 AI 上下文的形态：键顺序即 `Provenance` 的定义顺序。"""
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "endpoint": self.endpoint,
            "fetched_at": self.fetched_at,
            "as_of": self.as_of,
            "url": self.url,
            "verified": self.verified,
        }


#: `Provenance.as_dict()` 的键：出现在指标值的位置上就按出处记录解读
PROVENANCE_FIELDS: frozenset[str] = frozenset(
    {"value", "unit", "source", "endpoint", "fetched_at", "as_of", "url", "verified"}
)


def is_provenance_payload(payload: Any) -> bool:
    """一个 mapping 是否是出处记录（从 JSON 读回来的形态，不必是 dataclass）。"""
    return isinstance(payload, Mapping) and "source" in payload and "value" in payload


def _as_record(key: str, payload: Any) -> Provenance:
    if isinstance(payload, Provenance):
        return payload if payload.metric else replace(payload, metric=key)
    if not is_provenance_payload(payload):
        raise BoundaryViolation(f"指标 {key} 的出处记录形状非法（缺 value / source）")
    return Provenance(
        value=payload["value"],
        unit=str(payload.get("unit") or ""),
        source=str(payload["source"]),
        endpoint=payload.get("endpoint"),
        fetched_at=str(payload.get("fetched_at") or ""),
        as_of=str(payload.get("as_of") or ""),
        url=payload.get("url"),
        verified=bool(payload.get("verified")),
        metric=key,
    )


def numeric_value(value: Any) -> float | None:
    """能当数字看的才返回 float：`45` / `'45.2'` / `'45%'` / `'1,234'` 是同一个数。

    `bool` 不算数字（口径与 METRICS.md §4.1 一致：布尔位不是指标）。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%％").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class ProvenanceAudit:
    """一次出处审计的结果：谁能进 `verified`，谁只能挂〔未核实〕。"""

    verified: frozenset[str]
    unverified: frozenset[str]
    records: Mapping[str, Provenance] = field(default_factory=dict)
    #: 非数值字段（字符串口径、嵌套结构）：不参与「数字必须有出处」的判定
    non_numeric: frozenset[str] = frozenset()
    #: 出处与值解开后的纯指标载荷：进 `merge_narrative` 与产物 `facts` 的就是这一份
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """所有数值字段都有合法出处（R3 之后新写的产物应当成立）。"""
        return not self.unverified


def assert_provenance(
    metrics: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
) -> ProvenanceAudit:
    """数值字段 → 出处；**没有 provenance 的数值字段不得进入 `verified` 集**。

    `metrics` 的取值有三种形态：标量（程序算出的指标值）、`Provenance`、
    或出处记录 dict（从 `<stage>.json` 读回来的）。后两种把出处与值绑在一起，
    第一种要求出处从 `provenance` 参数旁路传入。

    抛 `BoundaryViolation` 的三种情形（施工期契约错误，与 `merge_narrative` 同级）：
    出处族未登记、声称已核实但引用要素不全、**出处记的值与指标值不一致**（伪造引用）。
    仅仅「没有出处」不抛——那是要挂〔未核实〕的正常状态，不是契约错误。
    """
    records: dict[str, Provenance] = {}
    values: dict[str, Any] = {}
    for key, raw in dict(metrics or {}).items():
        if isinstance(raw, Provenance) or is_provenance_payload(raw):
            record = _as_record(key, raw)
            values[key] = record.value
            records[key] = record
        else:
            values[key] = raw

    for key, payload in dict(provenance or {}).items():
        if key in records:
            raise BoundaryViolation(
                f"指标 {key} 的出处写了两处（既内联在指标值里又从 provenance 参数传入）"
            )
        records[key] = _as_record(key, payload)

    stray = sorted(set(records) - set(values))
    if stray:
        raise BoundaryViolation(
            "出处记录指向不存在的指标：" + "、".join(stray) + "（先确认指标键名）"
        )

    verified: set[str] = set()
    unverified: set[str] = set()
    non_numeric: set[str] = set()
    for key, value in values.items():
        found = records.get(key)
        if found is not None and not _same_value(found.value, value):
            raise BoundaryViolation(
                f"指标 {key} 的值（{value!r}）与出处记录的 value（{found.value!r}）不一致——"
                "引用被换过，这个数字不可信"
            )
        if numeric_value(value) is None:
            non_numeric.add(key)
        if found is None:
            # 唯一的硬规则：没有出处的数值字段进不了 verified，只能挂〔未核实〕
            if key not in non_numeric:
                unverified.add(key)
            continue
        (verified if found.verified else unverified).add(key)

    return ProvenanceAudit(
        verified=frozenset(verified),
        unverified=frozenset(unverified),
        records=dict(sorted(records.items())),
        non_numeric=frozenset(non_numeric),
        metrics=values,
    )


def _same_value(record_value: Any, value: Any) -> bool:
    """出处记的数与指标当前的数是不是同一个：能按数字比就按数字比（`45%` == 45）。"""
    if record_value == value:
        return True
    left, right = numeric_value(record_value), numeric_value(value)
    return left is not None and right is not None and left == right


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
