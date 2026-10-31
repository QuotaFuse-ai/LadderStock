#!/usr/bin/env python3
"""隐私 / 凭据 / 边界闸门静态自检（QUOT-99，可重复运行、只读、不联网）。

用法：

    ./.venv/Scripts/python.exe -X utf8 scripts/check_privacy.py

退出码：0 = 全部 PASS（WARN 不影响退出码）；1 = 至少一项 FAIL。

检查分五组：

1. `config-secrets`       config/*.yaml 不得出现真实密钥，只允许 `*_env` 指向环境变量名；
2. `credential-printing`  src 里把凭据 / Token 送进 print / log / error 的代码行；
3. `privacy-surfaces`     MCP 工具清单、web 私有字段、journal 库不被 mcp/web/ai 引用
                         （唯一例外：DEC-015 授权的 `web/server.py` → `journal.stats`，
                         且该文件必须同时具备回环闸门与顶层键白名单）；
4. `boundary-guards`      用反例验证 `scan_no_collection()` / `audit_sources()` 拦得住越界；
5. `listener-defaults`    web / MCP 默认只绑回环；非回环必须显式开关 + 鉴权 Token。

WARN 项是「已知缺口 / 需要跨模块改动」的提示，详见历史安全审计记录。"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CONFIG = ROOT / "config"
sys.path.insert(0, str(SRC))

# --------------------------------------------------------------------------- #
# 正则
# --------------------------------------------------------------------------- #

#: 输出汇：把内容写出去的地方（stdout / stderr / 日志）
SINK = re.compile(
    r"(?<![\w.])(?:print|error|_log)\(|(?<![\w.])logger\.|(?<![\w.])logging\.|"
    r"sys\.(?:stdout|stderr)\.write\("
)

#: 凭据类变量名
SECRET_NAME = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|authorization|cookie|credential)\b"
)

#: 配置里出现即视为「真实密钥」的形态
TOKEN_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})"
)

#: 配置键名里的凭据词
SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|private[_-]?key|credential)"
)
ENV_KEY = re.compile(r"(?i).*_env$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


# --------------------------------------------------------------------------- #
# 1. 配置：不得出现真实密钥
# --------------------------------------------------------------------------- #

def check_config_secrets() -> list[str]:
    problems: list[str] = []
    for path in sorted(CONFIG.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        hit = TOKEN_VALUE.search(text)
        if hit:
            problems.append(f"{path.name}: 疑似真实 Token（{hit.group(0)[:6]}…）")

        import yaml

        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:  # pragma: no cover - 配置损坏由 schema 校验兜底
            problems.append(f"{path.name}: YAML 解析失败（{exc}）")
            continue
        problems.extend(_walk_config(path.name, doc, ""))
    return problems


def _walk_config(name: str, node: object, trail: str) -> list[str]:
    problems: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            label = f"{trail}.{key}" if trail else str(key)
            key_text = str(key)
            if isinstance(value, str):
                if ENV_KEY.search(key_text):
                    # `*_env` 只允许写环境变量名，不许写值
                    if value.strip() and not ENV_NAME.match(value.strip()):
                        problems.append(
                            f"{name}: {label}={value!r} 不是环境变量名"
                        )
                    continue
                if SECRET_KEY.search(key_text) and value.strip():
                    problems.append(
                        f"{name}: {label} 写入了字面值（应改用 * _env 指向环境变量）"
                    )
                    continue
            problems.extend(_walk_config(name, value, label))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            problems.extend(_walk_config(name, item, f"{trail}[{index}]"))
    return problems


# --------------------------------------------------------------------------- #
# 2. 凭据不得进日志 / 报错
# --------------------------------------------------------------------------- #

def _scrub(line: str) -> str:
    """去掉「安全引用」后再判断：`*_env`、环境变量名、os.environ 取值。"""
    line = re.sub(r"[A-Za-z0-9_]+_env\b", "", line)
    line = re.sub(r"os\.environ(?:\.get)?\([^)]*\)", "", line)
    line = re.sub(r"\"[A-Z][A-Z0-9_]{3,}\"", "", line)
    line = re.sub(r"'[A-Z][A-Z0-9_]{3,}'", "", line)
    return line


def check_credential_printing() -> list[str]:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
        ):
            if not SINK.search(line):
                continue
            if SECRET_NAME.search(_scrub(line)):
                rel = path.relative_to(ROOT).as_posix()
                problems.append(f"{rel}:{lineno} 疑似把凭据送进输出：{line.strip()[:80]}")
    return problems


# --------------------------------------------------------------------------- #
# 3. 隐私面：MCP / web / AI 都不碰交易日志
# --------------------------------------------------------------------------- #

#: 对 `ladderstock.journal` 的 import（模块级，取到完整子模块名）。
JOURNAL_IMPORT = re.compile(
    r"^[ \t]*(?:from[ \t]+(ladderstock\.journal(?:\.[A-Za-z_]\w*)*)[ \t]+import\b"
    r"|import[ \t]+(ladderstock\.journal(?:\.[A-Za-z_]\w*)*)\b)",
    re.MULTILINE,
)

#: `ladderstock.journal` 导入白名单（DEC-015 Tier 1 / QUOT-196 / QUOT-202）。
#: 只放行「哪个文件的哪个子模块」，且该文件必须同时具备：回环独占闸门
#: `_journal_guard`（来源回环 + Host 白名单）与顶层键白名单 `JOURNAL_STATS_FIELDS`。
#: **其它任何对 `ladderstock.journal` 的引用仍判 FAIL**，规则整体不放开。
JOURNAL_IMPORT_ALLOWLIST: dict[str, dict[str, tuple[str, ...]]] = {
    "src/ladderstock/web/server.py": {
        "ladderstock.journal.stats": (
            "def _journal_guard(",
            "is_loopback_host(",
            "host_header_allowed(",
            "JOURNAL_STATS_FIELDS",
        ),
    },
}


def _journal_import_problems(rel: str, text: str) -> list[str]:
    """返回 `text` 里越界的 journal 引用；白名单命中但闸门缺失同样算越界。"""
    allowed = JOURNAL_IMPORT_ALLOWLIST.get(rel, {})
    problems: list[str] = []
    for match in JOURNAL_IMPORT.finditer(text):
        module = match.group(1) or match.group(2)
        required = allowed.get(module)
        if required is None:
            problems.append(f"{rel} 引用了 {module}（隐私库不得进 MCP / web / AI）")
            continue
        absent = [piece for piece in required if piece not in text]
        if absent:
            problems.append(
                f"{rel} 引用了 {module}，但缺少 DEC-015 要求的闸门：{absent}"
            )
    return problems


def _journal_rule_selftest() -> list[str]:
    """反例自检：规则必须有牙，白名单不能变成后门。"""
    problems: list[str] = []

    def caught(label: str, rel: str, text: str) -> bool:
        if _journal_import_problems(rel, text):
            return True
        problems.append(f"journal 规则漏放反例 {label}")
        return False

    def passed(label: str, rel: str, text: str) -> None:
        if _journal_import_problems(rel, text):
            problems.append(f"journal 规则误报正例 {label}：{_journal_import_problems(rel, text)}")

    # 反例 1：越界子模块（白名单之外的 journal 子模块）
    caught("from-journal-store", "src/ladderstock/web/server.py",
           "from ladderstock.journal.store import JournalStore\n")
    # 反例 2：包级 import
    caught("import-journal-package", "src/ladderstock/web/server.py",
           "import ladderstock.journal\n")
    # 反例 3：白名单模块挪到别的文件（mcp / ai）
    caught("stats-in-mcp", "src/ladderstock/mcp/server.py",
           "from ladderstock.journal.stats import compute_stats\n")
    # 反例 4：白名单文件里出现白名单外的 journal 子模块
    caught("mixed-in-web", "src/ladderstock/web/server.py",
           "from ladderstock.journal.stats import compute_stats\n"
           "from ladderstock.journal.ledger import Ledger\n")
    # 反例 5：白名单模块在册，但闸门 / 顶层键白名单缺一不可
    caught("no-loopback-guard", "src/ladderstock/web/server.py",
           "from ladderstock.journal.stats import compute_stats\n"
           "JOURNAL_STATS_FIELDS = ()\n"
           "is_loopback_host(x)\n"
           "host_header_allowed(r)\n")

    # 正例：白名单文件 + 白名单模块 + 闸门齐全
    passed("web-server-stats", "src/ladderstock/web/server.py",
           "from ladderstock.journal.stats import DEFAULT_DEVIATION_THRESHOLD, compute_stats\n"
           "JOURNAL_STATS_FIELDS: tuple[str, ...] = ()\n"
           "def _journal_guard(request) -> None:\n"
           "    if not is_loopback_host(client_host(request.client)): raise ...\n"
           "    if not host_header_allowed(request): raise ...\n")
    return problems


def check_privacy_surfaces() -> list[str]:
    problems: list[str] = []

    from ladderstock.mcp.tools import FORBIDDEN_KEYWORDS, tool_listing

    listing = json.dumps(tool_listing(), ensure_ascii=False).lower()
    for keyword in FORBIDDEN_KEYWORDS:
        if keyword.lower() in listing:
            problems.append(f"MCP 工具清单出现隐私关键字 {keyword!r}")

    from ladderstock.mcp.server import PRIVATE_KEYS as MCP_KEYS
    from ladderstock.web.server import PRIVATE_KEYS as WEB_KEYS

    missing = sorted(set(MCP_KEYS) - set(WEB_KEYS))
    if missing:
        problems.append(f"web 私有字段少于 MCP，缺少：{missing}")

    for package in ("mcp", "web", "ai"):
        for path in sorted((SRC / "ladderstock" / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            problems.extend(
                _journal_import_problems(
                    rel, path.read_text(encoding="utf-8", errors="ignore")
                )
            )
    problems.extend(_journal_rule_selftest())
    return problems


# --------------------------------------------------------------------------- #
# 4. 边界闸门：反例必须拦得住
# --------------------------------------------------------------------------- #

def _audit(sources: dict, sync: dict | None = None) -> list[str]:
    from ladderstock.core.boundary import audit_sources

    return [str(item) for item in audit_sources(sources, sync or {})]


def check_boundary_guards() -> list[str]:
    from ladderstock.core.boundary import scan_no_collection

    problems: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        clean = base / "clean"
        clean.mkdir()
        (clean / "ok.py").write_text("import json\n", encoding="utf-8")
        result = scan_no_collection(clean)
        if not result.ok:
            problems.append(f"scan_no_collection 误报干净目录：{result.violations}")

        cases = {
            "import-akshare": ("import akshare\n", "导入了采集库"),
            "from-tushare": ("from tushare import pro_api\n", "导入了采集库"),
            "vendor-host": ("URL = 'https://push2.eastmoney.com/api'\n", "直连行情站点"),
            # AST 检测：子串匹配漏掉的动态导入必须被拦住（audit R-4）
            "dynamic-importlib": (
                "import importlib\nimportlib.import_module('akshare')\n",
                "导入了采集库",
            ),
            "dynamic-dunder": ("__import__('tushare')\n", "导入了采集库"),
            "submodule-import": ("import akshare.stock\n", "导入了采集库"),
        }
        for label, (body, expected) in cases.items():
            folder = base / label
            folder.mkdir()
            (folder / "bad.py").write_text(body, encoding="utf-8")
            violations = scan_no_collection(folder).violations
            if not any(expected in item for item in violations):
                problems.append(f"scan_no_collection 未拦住反例 {label}：{violations}")

    # audit_sources 反例：跨 peer 备源
    cross_peer = {
        "sources": [
            {
                "id": "pool_main",
                "dataset": "limit_pool",
                "via": "tickhub",
                "tool": "limit_up_pool",
                "primary": True,
                "fallback": ["wire_news"],
            },
            {
                "id": "wire_news",
                "dataset": "theme_news",
                "via": "wire",
                "tool": "wire_get_digest",
            },
        ]
    }
    problems.extend(
        _expect(_audit(cross_peer), "跨 peer", "audit_sources 未拦住跨 peer 备源")
    )

    # 反例：未登记数据集
    unknown = {"sources": [{"id": "x", "dataset": "my_scraper", "via": "tickhub", "tool": "x"}]}
    problems.extend(
        _expect(_audit(unknown), "未在 DATASET_CATALOG 登记", "audit_sources 未拦住未登记数据集")
    )

    # 反例：fallback 指向不存在的 id
    dangling = {
        "sources": [
            {
                "id": "pool_main",
                "dataset": "limit_pool",
                "via": "tickhub",
                "tool": "limit_up_pool",
                "primary": True,
                "fallback": ["ghost"],
            }
        ]
    }
    problems.extend(_expect(_audit(dangling), "不存在", "audit_sources 未拦住悬空 fallback"))

    # 反例：主源工具写错
    wrong_tool = {
        "sources": [
            {
                "id": "pool_main",
                "dataset": "limit_pool",
                "via": "tickhub",
                "tool": "ths_limit_up_pool",
                "primary": True,
            }
        ]
    }
    problems.extend(_expect(_audit(wrong_tool), "主工具应为", "audit_sources 未拦住主源工具错配"))

    # 正例不得误报「跨 peer」
    same_peer = {
        "sources": [
            {
                "id": "pool_main",
                "dataset": "limit_pool",
                "via": "tickhub",
                "tool": "limit_up_pool",
                "primary": True,
                "fallback": ["pool_alt"],
            },
            {
                "id": "pool_alt",
                "dataset": "limit_pool",
                "via": "tickhub",
                "tool": "ths_limit_up_pool",
            },
        ]
    }
    if any("跨 peer" in item for item in _audit(same_peer)):
        problems.append("audit_sources 把同 peer 主备误判为跨 peer")

    return problems


def _expect(items: list[str], needle: str, message: str) -> list[str]:
    return [] if any(needle in item for item in items) else [f"{message}（实际：{items}）"]


# --------------------------------------------------------------------------- #
# 5. 监听：默认回环，非回环必须显式 + 鉴权
# --------------------------------------------------------------------------- #

def check_listener_defaults() -> list[str]:
    problems: list[str] = []

    from ladderstock.cli import mcp as mcp_cli
    from ladderstock.cli import run as run_cli

    if run_cli.DEFAULT_HOST != "127.0.0.1":
        problems.append(f"ldst run 默认监听 {run_cli.DEFAULT_HOST}，应为 127.0.0.1")
    if mcp_cli.DEFAULT_HOST != "127.0.0.1":
        problems.append(f"ldst mcp 默认监听 {mcp_cli.DEFAULT_HOST}，应为 127.0.0.1")

    from ladderstock.core.listener import require_binding_policy, token_matches
    from ladderstock.web import server as web

    if not hasattr(web, "require_binding_policy"):
        problems.append("web.server 缺少 require_binding_policy（非回环绑定闸门）")
        return problems
    if web.require_binding_policy("127.0.0.1") is not None:
        problems.append("回环地址不应要求 Token")

    # web 与 MCP 共用同一个判据；两条监听面各跑一遍四种组合（audit R-1）
    surfaces = (
        ("web", web.REMOTE_ALLOW_ENV, web.REMOTE_TOKEN_ENV, web.require_binding_policy),
        (
            "mcp",
            mcp_cli.REMOTE_ALLOW_ENV,
            mcp_cli.REMOTE_TOKEN_ENV,
            lambda host: require_binding_policy(
                host,
                allow_env=mcp_cli.REMOTE_ALLOW_ENV,
                token_env=mcp_cli.REMOTE_TOKEN_ENV,
                surface="MCP 服务",
            ),
        ),
    )
    saved = {
        name: os.environ.pop(name, None)
        for name in (web.REMOTE_ALLOW_ENV, web.REMOTE_TOKEN_ENV,
                     mcp_cli.REMOTE_ALLOW_ENV, mcp_cli.REMOTE_TOKEN_ENV)
    }
    try:
        for label, allow_env, token_env, policy in surfaces:
            if policy("127.0.0.1") is not None:
                problems.append(f"{label} 回环地址不应要求 Token")
            try:
                policy("0.0.0.0")
                problems.append(f"{label} 非回环绑定未被拒绝（缺少显式开关时）")
            except OSError:
                pass

            os.environ[allow_env] = "1"
            try:
                policy("0.0.0.0")
                problems.append(f"{label} 非回环绑定在缺少 Token 时未被拒绝")
            except OSError:
                pass

            os.environ[token_env] = "x" * 32
            token = policy("0.0.0.0")
            if token != "x" * 32:
                problems.append(f"{label} 非回环绑定在显式开关 + Token 齐全时未放行")
            os.environ.pop(allow_env, None)
            os.environ.pop(token_env, None)

        # 常量时间比较：错误 Token 必须拒绝，空 Token 永不匹配
        if token_matches("wrong", "x" * 32):
            problems.append("错误 Token 被放行（token_matches）")
        if token_matches("", ""):
            problems.append("空 Token 被判为匹配（token_matches）")
    finally:
        for name in (web.REMOTE_ALLOW_ENV, web.REMOTE_TOKEN_ENV,
                     mcp_cli.REMOTE_ALLOW_ENV, mcp_cli.REMOTE_TOKEN_ENV):
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
    return problems


# --------------------------------------------------------------------------- #
# WARN：已知缺口（需要跨模块改动，见 audit.md）
# --------------------------------------------------------------------------- #

def warnings() -> list[str]:
    notices: list[str] = []
    main_py = SRC / "ladderstock" / "cli" / "main.py"
    text = main_py.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"def config_migrate[\s\S]{0,600}?_todo\(", text):
        notices.append(
            "`ldst config migrate` 仍是占位实现（`_todo`，退出码 70）：迁移流程未落地；"
            "数据侧 SQLite 迁移前无自动备份（见 audit.md R-6）"
        )
    return notices


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

CHECKS = (
    ("config-secrets", check_config_secrets),
    ("credential-printing", check_credential_printing),
    ("privacy-surfaces", check_privacy_surfaces),
    ("boundary-guards", check_boundary_guards),
    ("listener-defaults", check_listener_defaults),
)


def main() -> int:
    failures = 0
    for name, check in CHECKS:
        try:
            problems = check()
        except Exception as exc:  # noqa: BLE001 - 自检脚本要把异常变成 FAIL 而不是栈
            print(f"[FAIL] {name}：扫描异常 {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if problems:
            failures += 1
            print(f"[FAIL] {name}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"[PASS] {name}")

    for notice in warnings():
        print(f"[WARN] {notice}")

    total = len(CHECKS)
    print(f"\n总结：{total - failures}/{total} 组通过，{failures} 组失败（WARN 不计入失败）")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
