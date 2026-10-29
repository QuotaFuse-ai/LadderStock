"""旧版启动入口：把 `python compatibility/start.py` 翻译成 `ldst run`。

旧版 vibe-astock 用 `python compatibility/start.py [--port ...]` 拉起工作台；
新版入口是 `ldst run`（DEC-003 / `docs/CLI.md` §F）。本文件只做**参数翻译与委托**，
不复制、不重写任何启动逻辑：

1. 旧环境变量前缀（`VRA_*` / `VIBE_ASTOCK_*`）翻译成 `LDST_*`，新前缀优先；
2. 参数前补齐 `run` 子命令（如果传的已经是已知子命令则原样透传）；
3. 交给 `ladderstock.cli.main:main`——与 `ldst` / `ladderstock` 是同一个 app。

首次启动若检测到旧配置/旧数据目录，会先提示跑 `ldst config migrate`：旧文件只读，
迁移前自动备份，失败则回退到旧启动路径继续用旧目录（源文档 §6.5）。

用法：
    python compatibility/start.py                 # 等价 ldst run
    python compatibility/start.py --port 5960     # 等价 ldst run --port 5960
    python compatibility/start.py config migrate  # 已知子命令，原样透传
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

#: 需要翻译的环境变量名后缀（值不复制进日志，避免凭据泄漏）
TRANSLATED_SUFFIXES: tuple[str, ...] = ("CONFIG_DIR", "DATA_DIR")

#: 旧版前缀，按顺序兜底；`LDST_*` 永远优先
LEGACY_PREFIXES: tuple[str, ...] = ("VRA_", "VIBE_ASTOCK_")

#: 已经是合法子命令时不再补 `run`
KNOWN_COMMANDS: frozenset[str] = frozenset(
    {
        "run",
        "review",
        "validate",
        "sentiment",
        "ladder",
        "sync",
        "sources",
        "watch",
        "notify",
        "journal",
        "export",
        "state",
        "ai",
        "mcp",
        "config",
        "schedule",
        "doctor",
    }
)

NOTICE = (
    "旧版启动方式：已委托 `ldst run`；"
    "旧配置/旧数据可用 `ldst config migrate` 只读迁移（迁移前自动备份，失败回退旧路径）"
)


def translate_env(environ: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    """把旧前缀环境变量补成 `LDST_*`；已存在的新变量绝不被覆盖。"""
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    for suffix in TRANSLATED_SUFFIXES:
        new_name = f"LDST_{suffix}"
        if env.get(new_name):
            continue
        for prefix in LEGACY_PREFIXES:
            legacy = env.get(f"{prefix}{suffix}")
            if legacy:
                env[new_name] = legacy
                break
    return env


def build_argv(argv: list[str]) -> list[str]:
    """旧入口默认就是「启动」：没有子命令时补上 `run`（`--port 5960` 也算启动参数）。"""
    args = list(argv)
    if args and (args[0] in KNOWN_COMMANDS or args[0] in ("--help", "-h", "--version")):
        return args
    return ["run", *args]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    translate_env()
    sys.stderr.write(f"ldst: {NOTICE}\n")
    sys.stderr.flush()

    from ladderstock.cli.main import main as cli_main

    sys.argv = ["ldst", *build_argv(args)]
    cli_main()


if __name__ == "__main__":
    main()
