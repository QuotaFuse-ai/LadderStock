"""安装 / 卸载 / 预检（DEC-086）。

三条来自决策的硬约束，都写在这里而不是散在页面里：

1. **体积必须先确认**：`plan()` 给出明细与总占用，`install(confirm=True)` 少了确认就不动盘；
2. **数据只在安装目录内**：装到系统盘（C:）默认拒绝，要强行继续必须显式 `allow_system_drive=True`；
3. **卸载整目录删除**：`uninstall()` 返回 `data_destroyed=True`，结果里写明不可恢复。

取件（下载 PG / Redis / MinIO / embedding）单独一步：`fetch` 是个可注入的回调，
默认实现走 `fetcher.fetch`（本地来源优先，其次官方下载），拿不到就抛 `FetchError`——
**不假装下载成功**。取件之后还要把 API / embedding 的 Python 依赖装进模块目录
（`--target`，不碰全局环境），否则「结构化查询照常可用」只是句空话。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import layout, manifest, state

#: 取件回调签名：(组件 key, 目标目录) -> 数量的字节；抛异常即视为该组件失败
Fetcher = Callable[[str, Path], int]

#: 进度回调签名：(0.0–1.0, 一句人话)。向导页靠它画进度条；回调抛异常不影响安装。
Progress = Callable[[float, str], None]

#: FinanceCoffer API 的运行依赖。只装源码不装这些，`/health` 都起不来，
#: 「结构化查询照常可用」就是句空话——所以它们属于安装的一部分，不是用户自己 pip。
API_DEPS: tuple[str, ...] = (
    "sqlalchemy>=2.0,<3",
    "psycopg[binary]>=3.1,<4",
    "pgvector>=0.2,<1",
    "redis>=5,<7",
    "httpx>=0.27,<1",
    "alembic>=1.13,<2",
    "python-dotenv>=1.0,<2",
    "typer>=0.12,<1",
    "pydantic>=2.7,<3",
    # s3 extra：冷归档的 MinIO 客户端。缺它 /health 的 minio 项永远停在
    # not_configured（探测在 import boto3 失败时就返回了，跟凭据对不对无关），
    # 于是「MinIO 已配置」永远无法被证明——这条必须进安装清单，不能靠事后补装。
    "boto3>=1.34",
)

#: embedding 服务依赖；torch 走 CPU 索引（本机没有 CUDA，装 GPU 版只是白占 2GB）
EMBEDDING_DEPS: tuple[str, ...] = ("transformers>=4.40",)
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


class BundleError(RuntimeError):
    """安装/卸载不可继续（体积确认、盘位、缺件）。"""


@dataclass(frozen=True)
class Preflight:
    """预检结果：errors 非空就装不了，warnings 是必须让用户看见的强提示。"""

    plan: manifest.BundlePlan
    target: Path
    free_mb: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _drive_of(path: Path) -> str:
    try:
        drive = path.resolve().drive.upper()
    except OSError:  # pragma: no cover - 解析失败按空盘符处理
        drive = ""
    if drive:
        return drive
    # 非 Windows 上 `Path("C:/x").drive` 是空串。盘符规则属于「目标在哪块盘」，
    # 不该随运行平台失效：这里按文本前缀补认一次，让 C 盘拦截跨平台生效。
    head = str(path)[:2]
    if len(head) == 2 and head[0].isalpha() and head[1] == ":":
        return head.upper()
    return ""


def plan(
    root: Path | None = None,
    *,
    with_embedding: bool,
    allow_system_drive: bool = False,
) -> Preflight:
    """向导第一步：给出体积明细 + 盘位检查，由用户点确认。"""
    target = layout.module_root(root)
    bundle = manifest.plan(with_embedding)
    need_mb = bundle.total_mb
    try:
        usage = shutil.disk_usage(target.anchor or target.parent)
        free_mb = int(usage.free // (1024 * 1024))
    except OSError:
        free_mb = 0

    errors: list[str] = []
    warnings: list[str] = []

    drive = _drive_of(target)
    if drive.startswith("C") and not allow_system_drive:
        errors.append(
            f"目标在系统盘 {drive}：数据库与对象存储数据会长在 C 盘。"
            "请换到数据盘再装（确要装 C 盘需显式允许）"
        )
    if free_mb and free_mb < need_mb:
        errors.append(f"磁盘空间不足：需要约 {need_mb} MB，可用 {free_mb} MB")

    data_dir = layout.data_dir(root)
    if data_dir.exists() and any(data_dir.iterdir()):
        warnings.append(f"{data_dir} 里已有数据：本次安装不会清空它，卸载时会一并删除（不可恢复）")
    if with_embedding:
        warnings.append("勾选 embedding：首次加载 30–60 秒，常驻内存约 +1.5GB")
    else:
        warnings.append("未勾选 embedding：向量检索长期不可用，结构化查询（宽表 / 公告）照常可用")

    return Preflight(
        plan=bundle, target=target, free_mb=free_mb, errors=tuple(errors), warnings=tuple(warnings)
    )


def _default_fetch(component: str, target: Path) -> int:
    """默认取件走 `fetcher`：本地来源优先（随包 runtime / 已下载缓存），其次官方下载。"""
    from ladderstock.modules.financecoffer import fetcher

    return fetcher.fetch(component, target)


def install(
    root: Path | None = None,
    *,
    with_embedding: bool,
    confirm: bool,
    fetch: Fetcher | None = None,
    allow_system_drive: bool = False,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    """按完整包一条路径安装，返回每步结果。

    `confirm` 必须是 True：组件是 GB 级的，没有「默默开始下载」这种选项。
    `on_progress` 按组件体积加权上报（PG 848MB 和 API 40MB 不能各占一半进度）。
    """
    preflight = plan(root, with_embedding=with_embedding, allow_system_drive=allow_system_drive)
    if not confirm:
        return {
            "ok": False,
            "status": "needs_confirmation",
            "detail": "需要先确认体积与落盘位置",
            "components": [
                {"key": c.key, "label": c.label, "size_mb": c.size_mb, "required": c.required}
                for c in preflight.plan.components
            ],
            "total_mb": preflight.plan.total_mb,
            "total_gb": preflight.plan.total_gb,
            "consequences": list(preflight.plan.consequences),
            "target": str(preflight.target),
            "free_mb": preflight.free_mb,
            "errors": list(preflight.errors),
            "warnings": list(preflight.warnings),
        }
    if preflight.errors:
        return {
            "ok": False,
            "status": "blocked",
            "detail": "；".join(preflight.errors),
            "errors": list(preflight.errors),
            "target": str(preflight.target),
        }

    root_path = preflight.target

    def _emit(fraction: float, message: str) -> None:
        """进度上报：回调异常一律吞掉，不能因为页面断了就中断安装。"""
        if on_progress is None:
            return
        try:
            on_progress(min(max(fraction, 0.0), 1.0), message)
        except Exception:  # noqa: BLE001
            pass

    try:
        state.write_state(root, "planning", detail="已确认体积与落盘位置", step="plan")
        # 重装 / 补齐组件时先把服务停掉：postgres.exe、minio.exe 在跑，文件被占用复制会失败
        from ladderstock.modules.financecoffer import services

        stopped = services.stop_all(root) if root_path.exists() else None

        root_path.mkdir(parents=True, exist_ok=True)
        for sub in ("runtime", "conf", "logs", "run", *layout.DATA_DIRNAMES):
            (root_path / sub).mkdir(parents=True, exist_ok=True)
        for name in layout.REDIS_PLATFORMS:
            (root_path / "runtime" / "redis" / name).mkdir(parents=True, exist_ok=True)

        state.write_state(root, "fetching", detail="开始取件", step="fetch")
        _emit(0.05, "开始安装组件")
        fetcher = fetch or _default_fetch
        fetched: list[dict[str, Any]] = []
        # 进度按体积加权：PG 848MB 与 API 40MB 不能各占同样一格
        total_mb = max(preflight.plan.total_mb, 1)
        done_mb = 0
        total_count = len(preflight.plan.components)
        for position, component in enumerate(preflight.plan.components, start=1):
            _emit(
                0.05 + 0.70 * done_mb / total_mb,
                f"正在安装 {component.label}（{position}/{total_count}，{component.size_mb}MB）",
            )
            target = _component_target(root, component.key)
            target.mkdir(parents=True, exist_ok=True)
            try:
                size = int(fetcher(component.key, target))
            except BundleError:
                raise
            except Exception as exc:  # noqa: BLE001 - 取件失败要能落到状态文件
                raise BundleError(f"组件 {component.key} 取件失败：{exc}") from exc
            fetched.append({"key": component.key, "bytes": size, "target": str(target)})
            done_mb += component.size_mb
            _emit(
                0.05 + 0.70 * done_mb / total_mb,
                f"{component.label} 完成（{component.size_mb}MB）",
            )

        state.write_state(root, "initializing", detail="初始化数据库与数据目录", step="initdb")
        _emit(0.78, "初始化数据库与数据目录")
        _write_conf(root)

        state.write_state(root, "configuring", detail="写入配置", step="configure")
        _emit(0.85, "写入配置并安装 API 依赖（这一步较慢）")
        deps = _install_deps(root, with_embedding)
        detail = "增强包已就绪" if deps.get("ok") else f"增强包已就绪，但依赖未装齐：{deps.get('detail')}"
        state.write_state(
            root,
            "ready",
            detail=detail,
            step="ready",
            with_embedding=with_embedding,
            components=[item["key"] for item in fetched],
        )
        _emit(1.0, "增强包安装完成")
        return {
            "ok": True,
            "status": "ready",
            "detail": detail,
            "target": str(root_path),
            "fetched": fetched,
            "total_mb": preflight.plan.total_mb,
            "services_stopped": stopped,
            "deps": deps,
        }
    except Exception as exc:  # noqa: BLE001 - 任何一步失败都必须留在状态文件里
        try:
            current = state.read_state(root).get("state")
            if current != "failed":
                state.write_state(root, "failed", detail=str(exc), step="install")
        finally:
            return {"ok": False, "status": "failed", "detail": str(exc), "target": str(root_path)}


def _install_deps(root: Path | None, with_embedding: bool) -> dict[str, Any]:
    """把 API / embedding 的 Python 依赖装进模块目录（`--target`），不碰全局环境。

    这是「结构化查询照常可用」的兑现方式：只复制源码、不装依赖，API 起不来，
    用户点「启动全部」只会看到 missing_deps。所以依赖跟着安装一起做。

    失败不抛异常：写进返回结果的 `detail`，装机本身仍算 ready（数据与二进制都到位，
    事后补依赖即可），`doctor` 会把对应服务判成 missing_deps。
    """
    results: dict[str, Any] = {"ok": True, "detail": "", "api": None, "embedding": None}
    problems: list[str] = []
    runtime = layout.module_root(root) / "runtime"
    if not (runtime / "api").is_dir():
        results["detail"] = "api 组件未装，跳过依赖安装"
        return results

    def _pip(target: Path, packages: Iterable[str], *, index_url: str | None = None) -> str:
        target.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target)]
        cmd.extend(packages)
        if index_url:
            cmd.extend(["--index-url", index_url])
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"pip 调用失败：{exc}"
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["未知错误"]
            return f"pip 退出码 {done.returncode}：{tail[0]}"
        return "ok"

    results["api"] = _pip(runtime / "api-deps", API_DEPS)
    if results["api"] != "ok":
        problems.append(f"api 依赖：{results['api']}")

    if with_embedding:
        results["embedding"] = _pip(runtime / "embedding-deps", ("torch",), index_url=TORCH_INDEX)
        if results["embedding"] == "ok":
            results["embedding"] = _pip(runtime / "embedding-deps", EMBEDDING_DEPS)
        if results["embedding"] != "ok":
            problems.append(f"embedding 依赖：{results['embedding']}")

    results["ok"] = not problems
    results["detail"] = "；".join(problems)
    return results


def _component_target(root: Path | None, key: str) -> Path:
    mapping = {
        "pg": layout.pg_dir(root),
        "redis": layout.redis_dir(root),
        "minio": layout.minio_dir(root),
        "api": layout.module_root(root) / "runtime" / "api",
        "embedding": layout.module_root(root) / "runtime" / "embedding",
    }
    return mapping[key]


def _write_conf(root: Path | None) -> None:
    """三端共用一份 `conf/redis.conf`（决策 2）：端口/数据目录都指向模块内，不写死盘符。"""
    conf = layout.conf_dir(root) / "redis.conf"
    if conf.is_file():
        return  # 已有配置不覆盖（可能是用户改过的）
    data = layout.data_dir(root, "redisdata")
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "# FinanceCoffer 增强包自带 Redis（三端共用一份配置，DEC-086）\n"
        "bind 127.0.0.1\n"
        "port 16379\n"
        # dir 用相对路径：Windows 的 Redis 是 cygwin 移植版，盘符路径会被解析成
        # /cygdrive/c/.../D:\... 然后报「配置文件不存在」（实测踩到）。启动时 cwd=模块根。
        "dir redisdata\n"
        "appendonly yes\n"
        "save \"\"\n",
        encoding="utf-8",
    )


def uninstall(root: Path | None = None, *, confirm: bool) -> dict[str, Any]:
    """整目录删除（决策 5）；`confirm` 缺省不动盘，并明确告知数据不可恢复。"""
    target = layout.module_root(root)
    if not target.exists():
        return {
            "ok": True,
            "status": "not_installed",
            "detail": f"{target} 不存在，无需卸载",
            "target": str(target),
            "data_destroyed": False,
        }
    if not confirm:
        return {
            "ok": False,
            "status": "needs_confirmation",
            # 数据目录按 env 分份（`pgdata` 与 `pgdata-prod` 并存）：这里必须用
            # layout.data_dir() 算真路径，否则提示里列的是另一份目录，用户
            # 以为「只删了测试服那份」而实际整根都删了。
            "detail": "卸载会删除整个目录，包括 pgdata / miniodata / redisdata 及其 -prod 分份，删除后不可恢复",
            "target": str(target),
            "data_dirs": [str(layout.data_dir(root, name)) for name in layout.DATA_DIRNAMES],
        }

    try:
        state.write_state(root, "uninstalling", detail="删除整个模块目录", step="uninstall")
    except state.IllegalTransition:
        pass  # 状态文件本身就坏了也照样删，不能因为状态机卡住而删不掉

    # 边界守卫：只删解析后的模块根，且必须落在同一个安装根的 file/modules 之内
    resolved = target.resolve()
    base_root = Path(root) if root is not None else layout.install_root()
    allowed_parent = (base_root / "file" / "modules").resolve()
    if not str(resolved).startswith(str(allowed_parent)) or resolved.name != layout.MODULE_NAME:
        return {
            "ok": False,
            "status": "refused",
            "detail": f"拒绝删除越界目录：{resolved}（只允许删 {allowed_parent} 下的 {layout.MODULE_NAME}）",
            "target": str(resolved),
            "data_destroyed": False,
        }

    shutil.rmtree(resolved)
    return {
        "ok": True,
        "status": "removed",
        "detail": "已删除整个模块目录（含 pgdata / miniodata / redisdata），数据不可恢复",
        "target": str(resolved),
        "data_destroyed": True,
    }


def components_for(with_embedding: bool) -> Iterable[manifest.Component]:
    return manifest.plan(with_embedding).components


__all__ = ["BundleError", "Fetcher", "Preflight", "components_for", "install", "plan", "uninstall"]
