"""逐条执行 31 条基线命令，记录退出码与 stdout 形状（可重复执行）。

用途：验收期把 `docs/CLI.md` 的命令矩阵跑一遍，把「哪条命令、什么退出码、
stdout 是不是合法 JSON」落成一张表，避免靠肉眼记忆。

```bash
python scripts/check_commands.py                    # 31 条，默认
python scripts/check_commands.py --with-output      # 每条再跑一次 --output json
python scripts/check_commands.py --report out.json  # 另存机器可读结果
```

约定：

- **每条命令一个独立临时数据目录**（`LDST_DATA_DIR`），既不写仓库 `data/`，
  也不会让前一条命令的产物污染后一条（例如 `review` 生成报告后 `review show`
  就不再是"无结果 6"）；
- 配置目录沿用仓库 `config/`，所以 `config validate` / `doctor` 校验的是真实配置；
- 长驻服务（`run` / `mcp stdio` / `mcp http`）不进矩阵——它们由
  `tests/contract/test_mcp_stdio.py` 与 `tests/integration/test_journal_privacy.py` 覆盖；
- 期望退出码来自 `tests/fixtures/command_matrix.py`（单一事实来源），
  与 `pytest` 用的完全一致；
- 退出码 70 = 施工期占位，属于已知偏差，单独计一列，不算失败但会在摘要里点名。

命令条数的口径（`docs/CLI.md`）：§A–E 是 P0 基线的 1–35 行，`sync` 三种形态合并为一条
命令后是 33 个叶子路径（含 `run` / `mcp stdio` / `mcp http` 三个长驻服务）；
§D′ 的 R1-1 `research` 1 行（36）与 §E″ 的 R7 增量 5 行（`pool` + `position *`，37–41）
都是基线之外的增量，叶子路径因此共 39 个。
矩阵覆盖 **38 条可执行 argv**——39 个叶子减去三个长驻服务，另加
`sync --dataset` / `sync --full` 两种形态各跑一次。叶子数以
`tests/fixtures/command_matrix.py:LEAF_PATHS` 为准，摘要行按它算，不写死。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from fixtures.command_matrix import (  # noqa: E402  - 依赖上面的 sys.path 注入
    COMMAND_MATRIX,
    JSON_PATHS,
    LEAF_PATHS,
)

UNIMPLEMENTED = 70
DEFAULT_TIMEOUT_SEC = 180
#: 全量同步要逐个数据集走主备链路；本机 peer 全挂时每条链路都要等满超时，
#: 单跑约 2 分钟，所以这两条单独放宽（其余命令都在秒级）。
ROW_TIMEOUTS: dict[tuple[str, ...], float] = {
    ("sync",): 420.0,
    ("sync", "--full"): 420.0,
}


@dataclass
class Result:
    argv: tuple[str, ...]
    expected: set[int]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code in self.expected

    @property
    def placeholder(self) -> bool:
        return self.exit_code == UNIMPLEMENTED


def _run_one(argv: Sequence[str], env: dict[str, str], timeout: float) -> Result:
    command = [sys.executable, "-X", "utf8", "-m", "ladderstock", *argv]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Result(
            argv=tuple(argv),
            expected=set(),
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
    return Result(
        argv=tuple(argv),
        expected=set(),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _shape(row: Result) -> str:
    """stdout 的一句话形状描述：json / 非 JSON / 空。"""
    text = row.stdout.strip()
    if not text:
        return "empty"
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return "text"
    return "json"


def _table(rows: list[Result], *, with_output: bool) -> str:
    lines = [
        f"{'#':>3}  {'command':<58} {'expect':<12} {'exit':>4}  {'stdout':<6} status",
        "-" * 100,
    ]
    for index, row in enumerate(rows, start=1):
        name = " ".join(row.argv)
        expect = ",".join(str(code) for code in sorted(row.expected))
        exit_code = "timeout" if row.timed_out else str(row.exit_code)
        status = "PASS" if row.ok else "FAIL"
        if row.placeholder:
            status += " (70=施工期占位)"
        lines.append(
            f"{index:>3}  {name:<58} {expect:<12} {exit_code:>4}  {_shape(row):<6} {status}"
        )
    if with_output:
        lines.append("")
        lines.append("以上每行跑两次（默认输出 + 追加 --output json）；exit 取两次里非零的那个。")
    return "\n".join(lines)


def _json_payload(rows: list[Result]) -> list[dict[str, Any]]:
    return [
        {
            "argv": list(row.argv),
            "expected": sorted(row.expected),
            "exit_code": row.exit_code,
            "timed_out": row.timed_out,
            "stdout_shape": _shape(row),
            "ok": row.ok,
            "placeholder": row.placeholder,
            "stderr_head": row.stderr.strip().splitlines()[:3],
        }
        for row in rows
    ]


def _merge(first: Result, second: Result, expected: set[int]) -> Result:
    """同一条命令跑两次（默认 / --output json）：合并成一行结果。

    两次都必须落在期望集合里；只要有一次违约就整行判失败，并把违约那次的退出码亮出来。
    """
    failed = second if second.ok else first
    return Result(
        argv=first.argv,
        expected=expected,
        exit_code=first.exit_code if first.ok and second.ok else failed.exit_code,
        stdout=first.stdout if first.ok else second.stdout,
        stderr=first.stderr if first.ok else second.stderr,
        timed_out=first.timed_out or second.timed_out,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐条跑 CLI 基线命令矩阵并核对退出码")
    parser.add_argument(
        "--with-output",
        action="store_true",
        help="每条命令再跑一次并追加 --output json（OUTPUT_MATRIX）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC, help="单条超时秒数")
    parser.add_argument("--report", type=Path, default=None, help="把结果写成 JSON 文件")
    args = parser.parse_args(argv)

    json_paths = {" ".join(path) for path in JSON_PATHS}
    base_env = dict(os.environ)
    base_env.setdefault("PYTHONIOENCODING", "utf-8")
    # 配置文件来自仓库（config/ 校验的是真实配置），数据目录逐条隔离。
    base_env.pop("LDST_DATA_DIR", None)

    rows: list[Result] = []
    json_failures: list[str] = []
    for command, expected in COMMAND_MATRIX:
        timeout = ROW_TIMEOUTS.get(tuple(command), args.timeout)
        # 每条命令一个干净的临时数据目录：命令之间不共享产物，
        # `--with-output` 的两次运行共用同一目录（比较的是同一初始状态）。
        with tempfile.TemporaryDirectory(prefix="ldst-check-") as temp:
            env = dict(base_env)
            env["LDST_DATA_DIR"] = temp

            first = _run_one(command, env, timeout)
            first.expected = set(expected)
            if args.with_output:
                second = _run_one((*command, "--output", "json"), env, timeout)
                second.expected = set(expected)
                first = _merge(first, second, set(expected))
        rows.append(first)

        name = " ".join(command)
        if first.ok and name in json_paths and _shape(first) != "json":
            json_failures.append(name)

    print(_table(rows, with_output=args.with_output))

    failures = [row for row in rows if not row.ok]
    placeholders = [row for row in rows if row.placeholder]
    print()
    print(
        f"合计 {len(rows)} 条可执行 argv（CLI.md §A–E + §D′ + §E″ 共 41 行、"
        f"去重 {len(LEAF_PATHS)} 个叶子路径，"
        f"其中 run / mcp stdio / mcp http 为长驻服务不入矩阵）："
        f"通过 {len(rows) - len(failures) - len(placeholders)}，"
        f"失败 {len(failures)}，施工期占位 {len(placeholders)}"
    )
    for row in failures:
        print(f"  FAIL {' '.join(row.argv)} -> exit={row.exit_code} 期望 {sorted(row.expected)}")
    for row in placeholders:
        print(f"  占位 {' '.join(row.argv)} -> exit=70（已注册、能力未实现）")
    for name in json_failures:
        print(f"  JSON {name} 有结构化输出但 stdout 不是合法 JSON")

    if args.report is not None:
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "total": len(rows),
            "failed": len(failures),
            "placeholders": len(placeholders),
            "json_shape_failures": json_failures,
            "results": _json_payload(rows),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.report}")

    return 1 if failures or json_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
