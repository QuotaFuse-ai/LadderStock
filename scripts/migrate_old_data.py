"""旧数据迁移的便捷入口，等价于 `ldst config migrate`。

为什么还要一个脚本：旧版（vibe-astock）用户手里留的是 `start.cmd` 时代的路径习惯，
发布说明里给一条「先 `python scripts/migrate_old_data.py` 看一眼计划」比让他们记
`ldst config migrate --dry-run` 更省事。本脚本**不含任何迁移逻辑**，只做两件事：

1. 默认补 `--dry-run`——不带参数运行时永远只看计划，不改任何文件；
2. 把参数原样交给 `ladderstock.cli.main`（`config migrate`）。

用法::

    python scripts/migrate_old_data.py                 # 只看计划（dry-run）
    python scripts/migrate_old_data.py --output json   # 计划 + 机器可读
    python scripts/migrate_old_data.py --no-dry-run    # 真正执行（先备份，失败回滚）

旧目录只读：迁移只把内容复制进 `data/legacy/`，不移动、不删除任何旧文件。
"""

from __future__ import annotations

import sys

from ladderstock.cli.main import main as cli_main

NOTICE = (
    "旧数据迁移：委托 `ldst config migrate`（旧文件只读、改动前自动备份）；"
    "去掉 --dry-run 才会真正执行"
)


def build_argv(args: list[str]) -> list[str]:
    """把脚本参数翻译成 `ldst config migrate` 的参数。"""
    forwarded = list(args)
    if "--no-dry-run" in forwarded:
        forwarded = [arg for arg in forwarded if arg != "--no-dry-run"]
    elif "--dry-run" not in forwarded and "-h" not in forwarded and "--help" not in forwarded:
        forwarded.insert(0, "--dry-run")
    return ["config", "migrate", *forwarded]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    print(NOTICE, file=sys.stderr)
    sys.argv = ["ldst", *build_argv(args)]
    cli_main()


if __name__ == "__main__":
    main()
