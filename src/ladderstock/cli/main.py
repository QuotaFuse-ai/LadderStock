"""`ldst` 命令入口。31 条 P0 命令见 docs/CLI.md（DEC-003）。

31 条基线命令已全部实现；`config migrate` 的迁移逻辑在
`ladderstock.config.migrate`（旧文件只读、迁移前备份、失败回滚）。
"""

from __future__ import annotations

import functools
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock import __version__
from ladderstock.cli.ai import register as register_ai
from ladderstock.cli.backtest import register as register_backtest
from ladderstock.cli.export import export as export_command
from ladderstock.cli.industry import register as register_industry
from ladderstock.cli.journal import register as register_journal
from ladderstock.cli.legacy_ports import warn_legacy_port
from ladderstock.cli.mcp import register as register_mcp
from ladderstock.cli.news import events_command, news_command
from ladderstock.cli.notify import register as register_notify
from ladderstock.cli.output import OutputOption, emit, error, resolve_format
from ladderstock.cli.paths import CONFIG_FILES, config_dir, data_dir
from ladderstock.cli.pool import register as register_pool
from ladderstock.cli.position import register as register_position
from ladderstock.cli.research import research as research_command
from ladderstock.cli.review import register as register_review
from ladderstock.cli.run import launch as launch_dashboard
from ladderstock.cli.schedule import register as register_schedule
from ladderstock.cli.sources import register as register_sources
from ladderstock.cli.state import register as register_state
from ladderstock.cli.supervisor import register as register_supervisor
from ladderstock.cli.sync import register as register_sync
from ladderstock.cli.watch import register as register_watch
from ladderstock.cli.watchdog import register as register_watchdog
from ladderstock.cli.watchlist import register as register_watchlist
from ladderstock.cli.weekly import register as register_weekly
from ladderstock.core.boundary import DATASET_CATALOG
from ladderstock.core.boundary import audit as boundary_audit
from ladderstock.data.provider_registry import provisional_tool_warnings
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_OK,
    LadderStockError,
)
from ladderstock.mcp.tools import tool_listing

DEFAULT_PORT = 5950
DEFAULT_MCP_PORT = 5951

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="天梯股复盘台 · A 股短线复盘与跟踪工作台（LadderStock）",
)
review_app = typer.Typer(no_args_is_help=False, invoke_without_command=True, help="收盘复盘")
validate_app = typer.Typer(
    no_args_is_help=False, invoke_without_command=True, help="明日验证与对账"
)
sentiment_app = typer.Typer(no_args_is_help=True, help="情绪指标")
ladder_app = typer.Typer(no_args_is_help=True, help="连板天梯")
industry_app = typer.Typer(no_args_is_help=True, help="产业研究（行业景气/资金/估值/动量/拥挤度）")
sync_app = typer.Typer(no_args_is_help=False, invoke_without_command=True, help="增量同步")
sources_app = typer.Typer(no_args_is_help=True, help="数据源")
watch_app = typer.Typer(
    no_args_is_help=True, help="盘中盯盘（start|status|stop）与我的自选（add|rm|list）"
)
notify_app = typer.Typer(no_args_is_help=True, help="通知与提醒")
journal_app = typer.Typer(no_args_is_help=True, help="交易日志（仅本机 SQLite）")
position_app = typer.Typer(no_args_is_help=True, help="我的持仓（仅本机 SQLite，不经 MCP）")
state_app = typer.Typer(no_args_is_help=True, help="前端持久化状态")
ai_app = typer.Typer(no_args_is_help=True, help="AI 编排")
chain_app = typer.Typer(no_args_is_help=True, help="多后端兼容矩阵")
mcp_app = typer.Typer(no_args_is_help=True, help="MCP Server")
tools_app = typer.Typer(no_args_is_help=True, help="MCP 工具枚举")
config_app = typer.Typer(no_args_is_help=True, help="配置")
schedule_app = typer.Typer(no_args_is_help=True, help="定时调度")
backtest_app = typer.Typer(no_args_is_help=True, help="回测（A 股日频策略，M1）")

for parent, child, name in (
    (app, review_app, "review"),
    (app, validate_app, "validate"),
    (app, sentiment_app, "sentiment"),
    (app, ladder_app, "ladder"),
    (app, industry_app, "industry"),
    (app, sync_app, "sync"),
    (app, sources_app, "sources"),
    (app, watch_app, "watch"),
    (app, notify_app, "notify"),
    (app, journal_app, "journal"),
    (app, position_app, "position"),
    (app, state_app, "state"),
    (app, ai_app, "ai"),
    (ai_app, chain_app, "chain"),
    (app, mcp_app, "mcp"),
    (mcp_app, tools_app, "tools"),
    (app, config_app, "config"),
    (app, schedule_app, "schedule"),
    (app, backtest_app, "backtest"),
):
    parent.add_typer(child, name=name)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _port_owner(port: int) -> str:
    """占用端口的本机进程（尽力而为）：Windows 读 netstat，POSIX 尝试 lsof。

    DEC-004：端口被占用不自动换端口，所以 doctor 必须告诉人「是谁占的」，
    否则 `--port` 提示就只是让人瞎猜。
    """
    try:
        if os.name == "nt":
            listing = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for line in listing.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != "TCP":
                    continue
                if not parts[1].endswith(f":{port}") or parts[3].upper() != "LISTENING":
                    continue
                return f"{parts[1]} · PID {parts[4]}"
        else:
            listing = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            name = pid = ""
            for line in listing.splitlines():
                if line.startswith("c"):
                    name = line[1:]
                elif line.startswith("p"):
                    pid = line[1:]
            if pid:
                return f"{name or '进程'} · PID {pid}"
    except Exception:  # noqa: BLE001 - 归属信息只求尽力，拿不到不影响体检
        pass
    return "占用进程未知"


def _dir_writable(path: Path) -> tuple[bool, str]:
    """数据目录可写性：能建目录、能写能删一个探针文件。"""
    probe = path / ".ldst-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{path} 不可写：{exc}"
    return True, f"{path} 可写"


# ---------------------------------------------------------------- 启动与复盘


@app.command(help="启动本地工作台（看板 + 本机 API），默认仅监听回环")
def run(
    port: Annotated[int, typer.Option(help="看板端口")] = DEFAULT_PORT,
    host: Annotated[str, typer.Option(help="监听地址，默认仅回环")] = "127.0.0.1",
    output: OutputOption = None,
) -> None:
    # 显式敲旧端口时补一句迁移说明，然后照旧执行（DEC-004：不接管、不回退）
    warn_legacy_port(port)
    if not _port_free(port):
        error(f"端口 {port} 已被占用；请用 --port 指定其他端口（不会自动换端口，见 DEC-004）")
        raise typer.Exit(EXIT_CONFIG)
    # 实现体在 cli/run.py（DEC-004 端口约定、只读 API、优雅退出）
    launch_dashboard(host=host, port=port)


# 独立看门狗（QUOT-443 / DEC-063）：5952 探活 + 重启入口；主服务挂了它仍活着。
# 实现体在 cli/watchdog.py（只绑回环、Origin 白名单、只结束自己记录的 PID）。
register_watchdog(app)

# 单入口 supervisor（QUOT-523）：不进看板也能全起 / 全停 / 单模块重启。
register_supervisor(app)


# 实现体在 cli/review.py（指标直出 + 五段编排 + 验证条件；DEC-003 命令名不变）
register_review(review_app, validate_app, sentiment_app, ladder_app)

# R11 周度复盘（QUOT-149）：实现体在 cli/weekly.py + weekly/{week,facts,checkpoint,pipeline}.py。
# 事实包只聚合本机每日产物与本机缓存，AI 只写叙述（DEC-006），口径见 docs/specs/SPEC-WEEKLY.md
register_weekly(app)


# ---------------------------------------------------------------- 数据层

# 实现体在 cli/sync.py / cli/sources.py；此处只做注册（DEC-003 命令名不变）
register_sync(sync_app)
register_sources(sources_app)


# ---------------------------------------------------------------- 盘中与提醒

# 实现体在 cli/watch.py / cli/notify.py：离线单轮信号扫描 + 本机渠道通知
# （DEC-006：只消费本机样本，不联网、不新增采集）
register_watch(watch_app)
# 自选清单（`watch add|rm|list`）与上面的盘中盯盘共用一个组名，实现体在 cli/watchlist.py；
# 写面只落本机 SQLite，不经 HTTP（DEC-052 §2.3）
register_watchlist(watch_app)
register_notify(notify_app)


# ---------------------------------------------------------------- 交易日志

# 实现体在 cli/journal.py（仅本机 SQLite，隐私边界见 DEC-006）；此处只做注册
register_journal(journal_app)


# ---------------------------------------------------------------- 候选池与我的持仓

# 候选池打分实现在 cli/pool.py + core/pool.py（R7）：只读本机缓存，公式见
# docs/specs/SPEC-CANDIDATE-POOL.md。持仓实现在 cli/position.py + journal/position.py：
# 同样只写本机 `data/journal.sqlite`，**不进 MCP 工具清单、不进 AI 上下文**（DEC-006）。
# 两者互不引用：持仓不参与打分，打分结果里也不可能出现持仓字段。
register_pool(app)
register_position(position_app)


# ---------------------------------------------------------------- 产业研究（R5）

# 实现体在 cli/industry.py + industry/{catalog,metrics,snapshot}.py（QUOT-143）：
# 只读本机缓存回放，五维口径见 docs/specs/SPEC-INDUSTRY-METRICS.md，
# 行业清单与窗口参数在 config/industry.yaml（申万一级，可按 enabled 裁剪）。
register_industry(industry_app)


# ---------------------------------------------------------------- 导出与状态

# 实现体在 cli/export.py；此处只做注册（DEC-003 命令名不变）
app.command(help="导出复盘报告（PDF 失败自动降级 Markdown）")(export_command)

# 实现体在 cli/state.py；此处只做注册（Python 不读 IndexedDB，见 DEC-001）
register_state(state_app)


# ---------------------------------------------------------------- AI

# 实现体在 cli/ai.py；此处只做注册（模型链 / 段落重试 / 兼容矩阵）
register_ai(ai_app, chain_app)


# ---------------------------------------------------------------- 个股研究（R1）

# 实现体在 cli/research.py + research/pipeline.py；`research` 是叶子命令，
# `<code>` 为位置参数，因此不能再挂子命令（见 cli/research.py 模块说明）
app.command(help="个股六段研究管线（company→financial→forecast→valuation→risk→report）")(
    research_command
)


# ---------------------------------------------------------------- 资讯雷达（R6）

# 实现体在 cli/news.py + news/ 包；两个都是顶层叶子命令（DEC-003 命令名口径）。
# 只从 DATASET_CATALOG 登记过的 peer 工具取数，只落本机 data/news.sqlite（DEC-006）
app.command("news", help="资讯雷达：聚合公告/研报/快讯/政策，规则抽取事件")(news_command)
app.command("events", help="事件日历：财报披露日 / 解禁日 / 股东大会 / 政策发布窗口")(
    events_command
)


# ---------------------------------------------------------------- MCP

# 实现体在 cli/mcp.py；此处只做注册（stdio 的 stdout 只写协议）
register_mcp(mcp_app)


@tools_app.command("list", help="枚举 MCP 工具与输入 schema")
def mcp_tools_list(output: OutputOption = None) -> None:
    fmt = resolve_format(output)
    tools = tool_listing()
    if fmt == "table":
        # 表格视图去掉完整 schema，只留契约关键字段，便于人读
        tools = [
            {
                "name": t["name"],
                "kind": "只读" if t["read_only"] else "写入",
                "domain": t["domain"],
                "params": ",".join(t.get("input_schema", {}).get("required", [])) or "-",
                "description": t["description"],
            }
            for t in tools
        ]
    emit(tools, fmt)


# ---------------------------------------------------------------- 配置与调度


@config_app.command("validate", help="校验配置与 JSON Schema")
def config_validate(
    file: Annotated[str | None, typer.Option(help="只校验指定文件")] = None,
    output: OutputOption = None,
) -> None:
    import yaml
    from jsonschema import Draft202012Validator

    base = config_dir()
    targets = [Path(file)] if file else [base / name for name in CONFIG_FILES]
    results: list[dict[str, Any]] = []
    ok = True
    for path in targets:
        item: dict[str, Any] = {"file": str(path), "ok": True, "errors": []}
        if not path.is_file():
            item["ok"] = False
            item["errors"].append("文件不存在")
        else:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001 - 报错要原样交给用户
                item["ok"] = False
                item["errors"].append(f"YAML 解析失败: {exc}")
                data = None
            schema_path = path.parent / "schema" / f"{path.stem}.schema.json"
            if data is not None and schema_path.is_file():
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                for err in sorted(
                    Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)
                ):
                    item["ok"] = False
                    item["errors"].append(f"/{'/'.join(map(str, err.path))}: {err.message}")
        ok = ok and item["ok"]
        results.append(item)
    emit({"ok": ok, "config_dir": str(base), "files": results}, output)
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


@config_app.command("seed", help="从随包模板补齐缺失配置与 schema（已存在不覆盖，DEC-071）")
def config_seed(output: OutputOption = None) -> None:
    from ladderstock.config.seed import seed_config

    emit(seed_config().to_dict(), output)
    raise typer.Exit(EXIT_OK)


@config_app.command("migrate", help="旧版配置与数据目录迁移（旧文件只读保留，迁移前自动备份）")
def config_migrate(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只输出迁移计划，不改动任何文件")
    ] = False,
    output: OutputOption = None,
) -> None:
    from ladderstock.config.migrate import run_migration

    try:
        result = run_migration(dry_run=dry_run)
    except LadderStockError as exc:
        error(exc.message)
        raise typer.Exit(exc.code) from exc
    emit(result, output)
    raise typer.Exit(EXIT_OK)


# 实现体在 cli/schedule.py（APScheduler；任务表见 config/schedule.yaml）
# `schedule run` 看退出标记自退（见 `_stop_when_marked`）
def _stop_when_marked(command: Callable[..., Any]) -> Callable[..., Any]:
    """`schedule run` 的收尾：看板写的退出标记出现就让调度器停下，长驻进程自行退出。

    Windows 上无控制台的 python 收不到优雅终止（taskkill 只能 /F），
    所以由看板写标记、这里每 2 秒看一眼，走正常收尾再退。
    """

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        from apscheduler.schedulers.base import BaseScheduler  # type: ignore[import-untyped]

        from ladderstock.supervisor.page_boot import CHILD_STOP_ARG_ENV

        raw = os.environ.get(CHILD_STOP_ARG_ENV)
        marker = Path(raw) if raw else None
        if marker is None:
            return command(*args, **kwargs)
        wake = threading.Event()
        original_start = BaseScheduler.start

        def start(scheduler: BaseScheduler) -> None:
            scheduler.add_job(
                lambda: wake.set(),
                "interval",
                seconds=2,
                id="page-boot-stop",
                replace_existing=True,
            )

            def watch() -> None:
                wake.wait()
                scheduler.shutdown(wait=False)

            threading.Thread(target=watch, name="page-boot-stop", daemon=True).start()
            original_start(scheduler)

        BaseScheduler.start = start  # type: ignore[method-assign, unused-ignore]
        try:
            return command(*args, **kwargs)
        finally:
            BaseScheduler.start = original_start  # type: ignore[method-assign, unused-ignore]

    functools.update_wrapper(wrapped, command)
    return wrapped


# 页面自启拉起的 `schedule run` 看退出标记自退（见 `_stop_when_marked`）
for _entry in schedule_app.registered_commands:
    if _entry.name == "run" and _entry.callback is not None:
        _entry.callback = _stop_when_marked(_entry.callback)


register_schedule(schedule_app)


# ---------------------------------------------------------------- 回测（QUOT-500）

# 实现体在 cli/backtest.py + backtest/ 包（feed/universe/strategy/match/score/guard/card/runner）：
# 只读本机 daily_k，运行卡落 <data_dir>/backtests/<run_id>/；口径见 docs/specs/SPEC-BACKTEST.md
register_backtest(backtest_app)


# ---------------------------------------------------------------- 体检

# ---------------------------------------------------------------- 页面自启


@app.command(
    "__watch-guard",
    hidden=True,
    help="页面自启的内部入口：周期跑盯盘扫描（由看板启动时拉起，不是用户命令）",
)
def watch_guard(tick: float = 30.0, output: OutputOption = None) -> None:
    """QUOT-522：看板打开时拉起的自有 watch 扫描守护（周期跑 `watch start`）。"""
    del output  # 长驻进程没有结构化输出；保留参数是为了全局 --output 契约
    from ladderstock.supervisor.page_boot import TICK_ENV, watch_guard_main

    os.environ[TICK_ENV] = f"{tick:g}"
    sys.exit(watch_guard_main())


@app.command(
    "__desktop",
    hidden=True,
    help="打包双击入口：启动看板并在端口就绪后打开默认浏览器（QUOT-525）",
)
def desktop_entry(
    port: Annotated[int, typer.Option(help="看板端口")] = DEFAULT_PORT,
    host: Annotated[str, typer.Option(help="监听地址，默认仅回环")] = "127.0.0.1",
) -> None:
    """PyInstaller / py2app 双击启动：等价 `ldst run` + 打开浏览器。"""
    warn_legacy_port(port)
    if not _port_free(port):
        error(f"端口 {port} 已被占用；请用 --port 指定其他端口（不会自动换端口，见 DEC-004）")
        raise typer.Exit(EXIT_CONFIG)
    # 冒烟/CI 环境置 LDST_NO_BROWSER=1，避免弹出真实浏览器窗口。
    open_browser = (os.environ.get("LDST_NO_BROWSER") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }
    launch_dashboard(host=host, port=port, open_browser=open_browser)


@app.command(
    "proxypool_forwarder",
    hidden=True,
    help="内部入口：按需代理池单映射转发器（打包后由 supervisor 拉起）",
)
def proxypool_forwarder(
    mapping_id: Annotated[str, typer.Option("--id", help="映射 Id")],
    listen_port: Annotated[int, typer.Option("--listen-port", help="监听端口")],
    upstream_host: Annotated[str, typer.Option("--upstream-host", help="上游主机")],
    upstream_port: Annotated[int, typer.Option("--upstream-port", help="上游端口")],
    listen_host: Annotated[str, typer.Option("--listen-host", help="监听地址")] = "127.0.0.1",
) -> None:
    """冻结态没有可执行的 `.py`，转发器改由自身 exe 的隐藏子命令承载。"""
    from ladderstock.supervisor.proxypool_forwarder import main as forward_main

    raise SystemExit(
        forward_main(
            [
                "--id",
                mapping_id,
                "--listen-host",
                listen_host,
                "--listen-port",
                str(listen_port),
                "--upstream-host",
                upstream_host,
                "--upstream-port",
                str(upstream_port),
            ]
        )
    )


@app.command(
    "__runtime-info",
    hidden=True,
    help="打包自检：输出冻结态、随包资源与安装根（不读写配置/数据）",
)
def runtime_info(output: OutputOption = None) -> None:
    """QUOT-525：验收「双击即跑、路径相对」的机读证据。"""
    from ladderstock.core import runtime

    dist = runtime.frontend_dist_dir()
    config = runtime.bundle_config_dir()
    emit(
        {
            "ok": True,
            "frozen": runtime.is_frozen(),
            "executable": str(runtime.executable()),
            "bundle_root": str(runtime.bundle_root()),
            "install_root": str(runtime.install_root()),
            "app_dir": str(runtime.app_dir()),
            "frontend_dist": str(dist),
            "frontend_dist_ready": (dist / "index.html").is_file(),
            "bundle_config": str(config),
            "bundle_config_ready": (config / "sources.yaml").is_file(),
            "web_static": str(runtime.web_static_dir()),
            "python": sys.version.split()[0],
        },
        output,
    )


@app.command(help="环境体检：Python、依赖、端口、目录、配置、数据源")
def doctor(
    peers: bool = typer.Option(
        False,
        "--peers",
        help="只看 peer 版本三态（本机版 vs GitHub 最新）；有落后时退出码非 0，断网/未知不算失败",
    ),
    output: OutputOption = None,
) -> None:
    if peers:
        # 版本三态复用控制面快照（QUOT-402）：current 探各 peer 自己的 venv，
        # expected 读后台线程维护的 GitHub Release 缓存；只读，绝不代跑升级。
        # CLI 是短命进程，后台线程来不及首刷：缓存为空的 repo 在这里同步补刷，
        # 但给 15s 总预算——断网时最多等 15s，剩下的如实显示「未知」（退出码仍 0）。
        import time as _time

        from ladderstock.web.control import ControlPlane

        plane = ControlPlane(data_root=data_dir())
        empty = [
            ctl.repo
            for ctl in plane.peers.values()
            if ctl.repo and plane._release_cache.expected(ctl.repo) is None
        ]
        budget_start = _time.monotonic()
        for repo in empty:
            if _time.monotonic() - budget_start > 15.0:
                break
            plane._release_cache.refresh(repo)
        rows = []
        for key, ctl in plane.peers.items():
            if ctl.repo is None:
                continue
            snap = plane.snapshot(key)
            rows.append(
                {
                    "name": snap["product"] or key,
                    "current": snap.get("version"),
                    "expected": snap.get("version_expected"),
                    "status": snap.get("version_status"),
                    "upgrade_hint": snap.get("upgrade_hint"),
                }
            )
        behind = any(row["status"] == "behind" for row in rows)
        emit({"peers": rows, "behind": behind}, output)
        raise typer.Exit(EXIT_CONFIG if behind else EXIT_OK)

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add(
        "python",
        sys.version_info >= (3, 11),
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}（需 >= 3.11）",
    )
    for mod in ("pydantic", "typer", "yaml", "jsonschema", "duckdb", "httpx", "apscheduler"):
        try:
            __import__(mod)
            add(f"dep:{mod}", True, "已安装")
        except Exception as exc:  # noqa: BLE001
            add(f"dep:{mod}", False, f"导入失败: {exc}")
    for port in (DEFAULT_PORT, DEFAULT_MCP_PORT):
        free = _port_free(port)
        add(
            f"port:{port}",
            free,
            "可用" if free else f"被占用（{_port_owner(port)}）；按 DEC-004 不自动换端口",
        )
    base = config_dir()
    add("config_dir", base.is_dir(), str(base) if base.is_dir() else f"{base} 不存在")
    for name in CONFIG_FILES:
        present = (base / name).is_file()
        add(f"config:{name}", present, "存在" if present else "缺失")
    # peer 注册表（QUOT-403）：唯一真源是 sources.yaml 的 peers 块；写坏时回退
    # 内置默认并给出原因（服务照常起，不算失败，但 warning 必须可见）。
    from ladderstock.core.boundary import PEER_LOAD_WARNINGS, PEERS

    add(
        "peers:registry",
        True,
        "；".join(PEER_LOAD_WARNINGS)
        if PEER_LOAD_WARNINGS
        else f"peers 块加载正常（{len(PEERS)} 个 peer）",
    )
    cache = data_dir()
    existed = cache.is_dir()
    # 先探可写（会按需建目录），再报 data_dir，避免「目录不存在但可写」的自相矛盾
    writable, detail = _dir_writable(cache)
    add("data_dir", cache.is_dir(), str(cache) if existed else f"{cache}（本次已创建）")
    add("data_dir:writable", writable, detail)
    # 四层边界：源码不许有采集，配置里的数据集必须有生态归属且主备同 peer（DEC-006）
    report = boundary_audit()
    chain_texts = {str(problem) for problem in report.chain}
    others = [v for v in report.violations if v not in chain_texts]
    config_violations = [v for v in others if v.startswith(("sources.yaml:", "sync.yaml:"))]
    code_violations = [v for v in others if v not in config_violations]
    add(
        "boundary:no_collection",
        not code_violations,
        "未发现自建采集" if not code_violations else "；".join(code_violations[:3]),
    )
    add(
        "boundary:datasets",
        not config_violations,
        f"{len(DATASET_CATALOG)} 个数据集均归属生态 peer"
        if not config_violations
        else "；".join(config_violations[:3]),
    )
    # 链路配置合法性：catalog ↔ sources.yaml/sync.yaml 双向一致、主备同 peer、工具已登记。
    # 只看配置，不探测 peer 可达性（那是 sources validate / sources health 的职责，DEC-008）。
    chain = report.chain
    add(
        "sources:chain",
        not chain,
        "链路配置与数据集登记一致" if not chain else "；".join(str(p) for p in chain[:3]),
    )
    add("version", True, __version__)
    ok = all(c["ok"] for c in checks)
    # 待联调工具只在这里探测一次（短超时、失败不重试）：要么 warning 消失，
    # 要么措辞说准「peer 不可达 / 工具不在列表里」，不留「待联调」这种含糊说法。
    # 探测结果不参与 ok 判定，检查项数只由 `CONFIG_FILES` 与依赖表决定
    # （DEC-008：可达性≠配置合法性）。
    warnings = [*report.warnings, *PEER_LOAD_WARNINGS, *provisional_tool_warnings()]
    emit({"ok": ok, "checks": checks, "warnings": warnings}, output)
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


def main() -> None:
    try:
        app()
    except LadderStockError as exc:
        error(exc.message)
        # 不能用 typer.Exit：`app()` 已经退出它自己的上下文，在外面抛 Exit 没人接，
        # 结果是 traceback + 退出码 1——带码的业务异常等于白包装（QUOT-234 实测）。
        sys.exit(exc.code)


if __name__ == "__main__":
    main()
