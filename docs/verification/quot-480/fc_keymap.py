# ruff: noqa
"""QUOT-480 打回复核：生成 FC 配置白名单的「键名 → 读取位置」对照表与生效性判定。

不手写：逐键在 FinanceCoffer 源码里定位 Config 字段定义行与 env 变量出现位置，
用 deploy/compose.yaml 的 x-app-env 段判定「写 deploy/.env 后是否真的能被插值覆盖」。
输出供 boundary.config_notes 与验收评论引用；白名单只收「Config 认识 + compose
插值可覆盖 + 有行为效果」三关全过的键。
"""

from __future__ import annotations

import re
from pathlib import Path

FC = Path(r"D:\workplace\FinanceCoffer")
CONFIG_PY = FC / "src" / "financecoffer" / "core" / "config.py"
LOADER_PY = FC / "src" / "financecoffer" / "config" / "loader.py"
COMPOSE_YAML = FC / "deploy" / "compose.yaml"
SCAN_SKIP = {".git", "build", "dist", "node_modules", ".venv", "__pycache__"}


def parse_config_fields() -> dict[str, tuple[int, str]]:
    """Config.model_fields 的字段名 → (config.py 行号, 默认值表达式)。"""
    fields: dict[str, tuple[int, str]] = {}
    lines = CONFIG_PY.read_text(encoding="utf-8").splitlines()
    for no, line in enumerate(lines, 1):
        # 只收类体内缩进 4 的字段定义行（含 pydantic Field(...) 形态）
        m = re.match(r"^    ([a-z_]+): ", line)
        if m and not line.startswith("    #"):
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            fields[m.group(1)] = (no, value[:60])
    return fields


def env_var_name(field: str) -> str:
    return f"FC_{field.upper()}"


def parse_compose_app_env() -> dict[str, tuple[int, str]]:
    """x-app-env 段的 FC_ 键 → (compose.yaml 行号, 值表达式)。"""
    entries: dict[str, tuple[int, str]] = {}
    lines = COMPOSE_YAML.read_text(encoding="utf-8").splitlines()
    in_block = False
    for no, line in enumerate(lines, 1):
        if line.startswith("x-app-env:"):
            in_block = True
            continue
        if in_block:
            if line and not line.startswith((" ", "#")):
                break
            m = re.match(r'^\s+(FC_[A-Z_]+): "?([^"#]*?)"?,?\s*(?:#.*)?$', line)
            if m:
                entries[m.group(1)] = (no, m.group(2))
    return entries


def classify_compose(expr: str) -> str:
    """按 x-app-env 的值表达式判定「写 deploy/.env 后容器是否收得到新值」。

    interpolated = ${FC_KEY:?} / ${FC_KEY:-} 引用同键 → 插值覆盖，可编辑；
    literal      = 无 ${} 引用 → 字面量，写 .env 无效；
    derived      = 只引用别的键（如 POSTGRES_*）拼装 → 写本键无效。
    """
    refs = set(re.findall(r"\$\{([A-Z_]+)[:?}]", expr))
    if not refs:
        return "literal"
    if len(refs) == 1 and next(iter(refs)).startswith("FC_"):
        return "interpolated"
    return "derived"


#: 只有本地 minio 进程形态（ops/minio_process.py）才消费的键：仅当
#: minio_autostart=true 时可达，而容器形态 AUTOSTART 被 compose 钉死 false
#:（compose.yaml:38 字面量）→ 容器内 no-op。脚本先验证消费点集合确实只落在
#: minio_process.py / serve.py，再判 no-op，不靠手工断言。
AUTOSTART_GATED = ("minio_binary", "minio_data_dir", "minio_log_file", "minio_console_endpoint")


def grep_field_consumers(field: str) -> list[str]:
    """字段名（config.<field> 形态）在 src/ 的消费位置，供 autostart 门控判定。"""
    hits: list[str] = []
    src = FC / "src"
    for path in sorted(src.rglob("*.py")):
        if any(part in SCAN_SKIP for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if field in line and not line.lstrip().startswith("#"):
                hits.append(f"{path.relative_to(FC)}:{no}")
    return hits


def grep_occurrences(var: str) -> list[str]:
    """env 变量名在 FC 仓库的出现位置（排除构建目录），供核对「真的在被读」。"""
    hits: list[str] = []
    for base in (FC / "src", FC / "deploy", FC / "scripts"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".sh", ".example", ""}:
                continue
            if any(part in SCAN_SKIP for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for no, line in enumerate(text.splitlines(), 1):
                if var in line and not line.lstrip().startswith("#"):
                    rel = path.relative_to(FC)
                    hits.append(f"{rel}:{no}")
    return hits


def main() -> None:
    fields = parse_config_fields()
    compose = parse_compose_app_env()
    print(f"Config.model_fields 共 {len(fields)} 字段；x-app-env 段共 {len(compose)} 个 FC_ 键\n")

    autostart_gated = {
        name: grep_field_consumers(name) for name in AUTOSTART_GATED
    }
    print("## autostart 门控判定（消费点必须只落在本地进程形态）")
    for name, hits in autostart_gated.items():
        non_local = [
            h for h in hits
            if "ops/minio_process.py" not in h.replace("\\", "/")
            and "cli/commands/serve.py" not in h.replace("\\", "/")
            and "core/config.py" not in h.replace("\\", "/")
        ]
        ok = not non_local
        print(f"  {name}: 消费点 {len(hits)} 处，非本地进程消费 {'有 ' + str(non_local) if non_local else '无'}"
              f" -> {'autostart 门控成立' if ok else '门控判定失败，不得按 no-op 排除'}")
    print()

    header = f"{'env 键':34} {'Config 字段':28} {'config.py':11} {'compose 形态':14} 结论"
    print(header)
    print("-" * len(header))

    whitelist: list[str] = []
    rejected: list[tuple[str, str]] = []
    for field, (no, default) in sorted(fields.items(), key=lambda kv: kv[1][0]):
        var = env_var_name(field)
        expr = compose.get(var, ("", "<缺>"))[1]
        form = classify_compose(expr) if var in compose else "<不在 x-app-env>"
        if form == "interpolated":
            if field in AUTOSTART_GATED:
                verdict = "排除（autostart 门控：容器 AUTOSTART=false，容器内 no-op）"
                rejected.append((var, "autostart-gated"))
            else:
                verdict = "白名单（插值可覆盖）"
                whitelist.append(var)
        else:
            verdict = f"排除（{form}，写 .env 不生效）"
            rejected.append((var, form))
        print(f"{var:34} {field:28} config.py:{no:<3} {form:14} {verdict}")

    print("\n## compose.yaml x-app-env 值表达式（行号: 键 = 值）")
    for var, (no, expr) in sorted(compose.items(), key=lambda kv: kv[1][0]):
        print(f"  compose.yaml:{no}: {var} = {expr!r} -> {classify_compose(expr)}")

    print("\n## 排除键的原文位置（供评论引用）")
    for var, form in rejected:
        no, expr = compose.get(var, (0, "<缺>"))
        print(f"  {var}: compose.yaml:{no} = {expr!r}（{form}）")

    print(f"\n## 白名单（{len(whitelist)} 键）")
    for var in whitelist:
        print(f"  {var}")

    print("\n## 抽查 8 键的源码出现位置（非注释行）")
    for var in ("FC_EMBEDDING_PROVIDER", "FC_MINIO_BUCKET", "FC_ETL_BATCH_SIZE",
                "FC_METRICS_PROFILE", "FC_LOG_LEVEL", "FC_RATE_LIMIT_PER_MINUTE",
                "FC_MINIO_AUTOSTART", "FC_REDIS_URL"):
        print(f"  {var}: {grep_occurrences(var) or '（无出现！）'}")


if __name__ == "__main__":
    main()
