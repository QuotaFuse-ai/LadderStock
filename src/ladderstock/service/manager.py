"""服务安装/卸载/状态（QUOT-527）：三平台共用一套 dry-run / apply 语义。

**默认只出计划**：`install` / `uninstall` 不带 `apply` 时不写文件、不执行命令；
`apply=True` 才落盘并调用原生注册命令。`apply` 还有一个硬闸门——计划平台必须
等于当前 OS 平台，防止在 Windows 上误跑 launchctl / systemctl（反之亦然）。

开发与测试宿主永远不安装真服务：单测注入 `FakeRunner` + 临时 `home`/`data_root`，
CLI 的基线测试只跑 dry-run / render。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ladderstock.service.backends import (
    BACKENDS,
    PlatformBackend,
    WindowsTaskBackend,
    backend_for,
)
from ladderstock.service.model import (
    ALL_PLATFORMS,
    CommandResult,
    Platform,
    ServiceCommand,
    ServiceError,
    ServicePlan,
    ServiceSpec,
    detect_platform,
    platform_label,
)

INSTALL_MANIFEST = "install.json"
DEFAULT_TIMEOUT_SEC = 30.0


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT_SEC) -> CommandResult: ...


class SubprocessRunner:
    """真实执行器：argv 列表、`shell=False`，起不来/超时都归一到 CommandResult。"""

    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT_SEC) -> CommandResult:
        command = tuple(str(part) for part in argv)
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            return CommandResult(command, returncode=-1, error=f"找不到命令：{exc}")
        except subprocess.TimeoutExpired:
            return CommandResult(command, returncode=-1, error=f"命令超时（{timeout:g}s）")
        except OSError as exc:
            return CommandResult(command, returncode=-1, error=f"命令无法执行：{exc}")
        return CommandResult(
            command,
            returncode=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


@dataclass
class ServiceManager:
    """一个服务（默认 LadderStock 单入口）的安装面。

    `platform` 是本次计划的目标平台（缺省 = 当前平台）；`current` 是真实执行
    平台，测试可注入。`apply` 只允许两者相等。
    """

    platform: Platform | None = None
    current: Platform | None = None
    home: Path | None = None
    data_root: Path | None = None
    runner: CommandRunner | None = None
    uid: int | None = None
    user: str | None = None
    config_home: Path | None = None
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        self.current = detect_platform(self.current) if self.current else detect_platform()
        self.platform = detect_platform(self.platform) if self.platform else self.current
        self.home = Path(self.home) if self.home is not None else _home(self.env)
        if self.data_root is None:
            from ladderstock.cli.paths import data_dir

            self.data_root = data_dir()
        self.data_root = Path(self.data_root)
        self.runner = self.runner or SubprocessRunner()

    # ------------------------------------------------------------------ 查询

    @property
    def service_dir(self) -> Path:
        assert self.data_root is not None  # __post_init__ 已落定
        return self.data_root / "service"

    @property
    def manifest_path(self) -> Path:
        return self.service_dir / INSTALL_MANIFEST

    def backend(self, platform: Platform | None = None) -> PlatformBackend:
        target = platform or self.platform
        if target is None:
            raise ServiceError(
                "无法识别当前平台（支持 Windows / macOS / Linux）；"
                "可用 render 在任意平台生成三平台产物"
            )
        kwargs: dict[str, object] = {}
        if target == "macos":
            kwargs["uid"] = self.uid
        elif target == "windows":
            kwargs["user"] = self.user
        elif target == "linux":
            kwargs["config_home"] = self.config_home
        return backend_for(target, **kwargs)

    def artifact_path(self, backend: PlatformBackend) -> Path:
        assert self.home is not None  # __post_init__ 已落定
        if backend.platform == "windows":
            # XML 是注册输入，放在本应用自己的服务目录，不进系统任务目录
            return self.service_dir / backend.artifact_filename
        return backend.install_artifact_path(self.home)

    # ------------------------------------------------------------------ 计划

    def plan_install(self, spec: ServiceSpec, platform: Platform | None = None) -> ServicePlan:
        spec.validate()
        backend = self.backend(platform)
        artifact = self.artifact_path(backend)
        return ServicePlan(
            platform=backend.platform,
            artifact_kind=backend.artifact_kind,
            artifact_path=artifact,
            content=backend.render(spec),
            commands=backend.install_commands(spec, artifact),
            artifact_encoding=backend.artifact_encoding,
        )

    def plan_uninstall(self, spec: ServiceSpec, platform: Platform | None = None) -> ServicePlan:
        spec.validate()
        backend = self.backend(platform)
        artifact = self.artifact_path(backend)
        return ServicePlan(
            platform=backend.platform,
            artifact_kind=backend.artifact_kind,
            artifact_path=artifact,
            content="",  # 卸载不需要重写产物；保留路径用于删除
            commands=backend.uninstall_commands(spec),
            post_commands=backend.post_uninstall_commands(spec),
            artifact_encoding=backend.artifact_encoding,
        )

    def render_all(
        self,
        spec: ServiceSpec,
        out_dir: Path,
        *,
        platforms: Sequence[Platform] = ALL_PLATFORMS,
    ) -> dict[str, object]:
        """把三平台原生定义写到 `out_dir`（纯文本产物，不触碰 OS 注册表）。"""
        spec.validate()
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, object]] = []
        for platform in platforms:
            backend = self.backend(platform)
            content = backend.render(spec)
            path = out_dir / backend.artifact_filename
            _write_text(path, content, backend.artifact_encoding)
            data = content.encode(backend.artifact_encoding)
            artifacts.append(
                {
                    "platform": platform,
                    "mechanism": platform_label(platform),
                    "artifact_kind": backend.artifact_kind,
                    "artifact_encoding": backend.artifact_encoding,
                    "path": str(path),
                    "bytes": len(data),
                    "sha256": _sha256(data),
                }
            )
        return {"ok": True, "out_dir": str(out_dir), "artifacts": artifacts}

    # ------------------------------------------------------------------ 安装

    def install(self, spec: ServiceSpec, *, apply: bool = False) -> dict[str, object]:
        plan = self.plan_install(spec)
        payload = {"dry_run": not apply, **plan.to_dict()}
        if not apply:
            payload["ok"] = True
            payload["hint"] = "默认 dry-run；确认后加 --apply 才写文件并注册 OS 服务"
            return payload
        self._require_current(plan.platform)
        spec.validate()

        Path(spec.log_dir).mkdir(parents=True, exist_ok=True)
        plan.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text(plan.artifact_path, plan.content, plan.artifact_encoding)

        results = self._run_commands(plan.commands)
        ok = all(
            item.ok
            for index, item in enumerate(results)
            if not _optional(plan.commands, index)
        )
        payload.update(
            {
                "ok": ok,
                "artifact_written": True,
                "results": [item.to_dict() for item in results],
            }
        )
        if ok:
            self._write_manifest(plan)
            payload["manifest"] = str(self.manifest_path)
        return payload

    # ------------------------------------------------------------------ 卸载

    def uninstall(self, spec: ServiceSpec, *, apply: bool = False) -> dict[str, object]:
        plan = self.plan_uninstall(spec)
        payload = {"dry_run": not apply, **plan.to_dict()}
        if not apply:
            payload["ok"] = True
            payload["hint"] = "默认 dry-run；确认后加 --apply 才反注册并删除产物"
            return payload
        self._require_current(plan.platform)
        spec.validate()

        state = self._query_status(spec, plan.platform)
        # 状态查询明确回答「没装」才跳过反注册；查询不可用（unknown）时仍尝试，
        # 失败就保留产物（失败关闭），绝不留下「注册还在、文件被删」的半成品。
        if not state.get("installed") and state.get("state") != "unknown":
            removed = _remove_file(plan.artifact_path)
            self._remove_manifest()
            payload.update(
                {
                    "ok": True,
                    "already_absent": True,
                    "artifact_removed": removed,
                    "status": state,
                    "results": [],
                }
            )
            return payload

        results = self._run_commands(plan.commands)
        ok = all(item.ok for item in results)
        payload["results"] = [item.to_dict() for item in results]
        if ok:
            payload["artifact_removed"] = _remove_file(plan.artifact_path)
            post_results = self._run_commands(plan.post_commands)
            payload["post_results"] = [item.to_dict() for item in post_results]
            ok = all(item.ok for item in post_results)
            self._remove_manifest()
        payload["ok"] = ok
        return payload

    # ------------------------------------------------------------------ 状态

    def status(self, spec: ServiceSpec) -> dict[str, object]:
        platform = self.current or self.platform
        if platform is None:
            return {
                "ok": True,
                "supported": False,
                "installed": False,
                "detail": "无法识别当前平台；支持 Windows / macOS / Linux",
            }
        state = self._query_status(spec, platform)
        backend = self.backend(platform)
        artifact = self.artifact_path(backend)
        manifest = self._read_manifest()
        return {
            "ok": True,
            "supported": True,
            "platform": platform,
            "mechanism": platform_label(platform),
            "artifact_path": str(artifact),
            "artifact_exists": artifact.is_file(),
            "manifest": manifest,
            **state,
        }

    # ------------------------------------------------------------------ 内部

    def _require_current(self, target: Platform) -> None:
        if self.current != target:
            raise ServiceError(
                f"当前平台是 {platform_label(self.current)}，不能在这里安装/卸载 "
                f"{platform_label(target)} 的服务；目标平台产物请用 `service render` 生成后"
                " 在对应机器上安装"
            )

    def _query_status(self, spec: ServiceSpec, platform: Platform) -> dict[str, object]:
        backend = self.backend(platform)
        results = self._run_commands(backend.status_commands(spec))
        return backend.parse_status(results)

    def _run_commands(self, commands: Sequence[ServiceCommand]) -> list[CommandResult]:
        results: list[CommandResult] = []
        runner = self.runner or SubprocessRunner()
        for index, command in enumerate(commands):
            result = runner.run(command.argv)
            results.append(result)
            if not result.ok and not _optional(commands, index):
                break  # 失败关闭：后续步骤依赖前一步，不带着半成品继续
        return results

    def _write_manifest(self, plan: ServicePlan) -> None:
        spec = plan.to_dict()
        payload = {
            "platform": plan.platform,
            "artifact_path": str(plan.artifact_path),
            "artifact_sha256": spec["artifact_sha256"],
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.service_dir.mkdir(parents=True, exist_ok=True)
        _write_text(
            self.manifest_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )

    def _read_manifest(self) -> dict[str, object] | None:
        try:
            raw = self.manifest_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _remove_manifest(self) -> None:
        try:
            self.manifest_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _optional(commands: Sequence[ServiceCommand], index: int) -> bool:
    return bool(commands[index].optional)


def _home(env: Mapping[str, str] | None) -> Path:
    values = os.environ if env is None else env
    override = (values.get("HOME") or "").strip()
    if override and Path(override).is_absolute():
        return Path(override)
    return Path.home()


def _write_text(path: Path, content: str, encoding: str) -> None:
    """原子写：先写同目录 .tmp，再 replace；失败不会留下半个 unit。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    data = content.encode(encoding)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove_file(path: Path) -> bool:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "BACKENDS",
    "CommandRunner",
    "INSTALL_MANIFEST",
    "ServiceManager",
    "SubprocessRunner",
    "WindowsTaskBackend",
]
