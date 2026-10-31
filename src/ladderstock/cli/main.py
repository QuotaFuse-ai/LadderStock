"""`ldst` 命令入口。31 条 P0 命令见 docs/CLI.md（DEC-003）。

31 条基线命令已全部实现；`config migrate` 的迁移逻辑在
`ladderstock.config.migrate`（旧文件只读、迁移前备份、失败回滚）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock import __version__
from ladderstock.cli.ai import register as register_ai
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
from ladderstock.cli.sync import register as register_sync
from ladderstock.cli.watch import register as register_watch
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
watch_app = typer.Typer(no_args_is_help=True, help="盘中盯盘")
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


# 实现体在 cli/review.py（指标直出 + 五段编排 + 验证条件；DEC-003 命令名不变）
register_review(review_app, validate_app, sentiment_app, ladder_app)


# ---------------------------------------------------------------- 数据层

# 实现体在 cli/sync.py / cli/sources.py；此处只做注册（DEC-003 命令名不变）
register_sync(sync_app)
register_sources(sources_app)


# ---------------------------------------------------------------- 盘中与提醒

# 实现体在 cli/watch.py / cli/notify.py：离线单轮信号扫描 + 本机渠道通知
# （DEC-006：只消费本机样本，不联网、不新增采集）
register_watch(watch_app)
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
register_schedule(schedule_app)


# ---------------------------------------------------------------- 体检

@app.command(help="环境体检：Python、依赖、端口、目录、配置、数据源")
def doctor(output: OutputOption = None) -> None:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add(
        "python",
        sys.version_info >= (3, 11),
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        f"（需 >= 3.11）",
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
    config_violations = [
        v for v in others if v.startswith(("sources.yaml:", "sync.yaml:"))
    ]
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
    warnings = [*report.warnings, *provisional_tool_warnings()]
    emit({"ok": ok, "checks": checks, "warnings": warnings}, output)
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


def main() -> None:
    try:
        app()
    except LadderStockError as exc:
        error(exc.message)
        raise typer.Exit(exc.code) from exc


if __name__ == "__main__":
    main()
