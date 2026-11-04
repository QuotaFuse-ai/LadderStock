"""Peer 期望版本（GitHub Releases）与本机版本的三态比对（QUOT-402）。

约束：
- **只读**：绝不自动升级、不自动重启 peer；升级命令只作为可复制文本给出；
- **不阻塞**：GitHub 查询只发生在后台刷新线程里，快照 / doctor 只读缓存；
- **不猜**：没有缓存、当前版本缺失或任一侧不是 semver → 「未知」，
  不许显示 0.0.0，不许拿字符串比较代替语义化版本比较（1.10.0 > 1.9.0）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

#: 缓存文件结构版本：形状不兼容时直接丢旧缓存，不猜
_CACHE_SCHEMA = 1

#: GitHub Releases 的刷新间隔（每小时最多查一次，QUOT-402 验收约束）
_TTL_SEC = 3600.0

#: `v1.2.3` / `1.2.3-rc1` / `1.2.3+build` 都认前三位；其余判「不可比」
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")

_GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


def parse_semver(text: str | None) -> tuple[int, int, int] | None:
    """解析 `v?X.Y.Z` 前三位；解析不了返回 None（调用方判「未知」，不许猜）。"""
    if not text:
        return None
    match = _SEMVER.match(text.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_status(current: str | None, expected: str | None) -> str:
    """三态：`latest` / `behind` / `unknown`。

    任一侧缺号或不是 semver 都判 `unknown`——「未知」是合法结论，比错的结论强。
    """
    cur = parse_semver(current)
    exp = parse_semver(expected)
    if cur is None or exp is None:
        return "unknown"
    return "latest" if cur >= exp else "behind"


def upgrade_command(directory: str) -> str:
    """可复制的升级命令（editable 形态；只给文本，绝不代跑）。"""
    return f"cd {directory} && git pull && pip install -e . --no-deps"


class ReleaseCache:
    """GitHub 最新 Release 的磁盘缓存 + ETag 协商。

    - `refresh*` 只允许后台线程调用（网络 + 磁盘写都在那）；
    - 读方（快照 / doctor）只走 `expected()`，纯内存/磁盘读，不阻塞。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ---------------------------------------------------------------- 读

    def expected(self, repo: str) -> str | None:
        """该 repo 的最新发布版本号；没有缓存返回 None（调用方显示「未知」）。"""
        with self._lock:
            entry = self._data.get(repo)
        if not entry:
            return None
        return entry.get("tag") or None

    def stale(self, repo: str) -> bool:
        with self._lock:
            entry = self._data.get(repo)
        if not entry:
            return True
        return (time.time() - float(entry.get("fetched_at", 0.0))) >= _TTL_SEC

    # ---------------------------------------------------------------- 写

    def refresh(self, repo: str) -> None:
        """同步查一次 GitHub；任何失败都静默保留旧缓存（断网不报错、不清缓存）。"""
        with self._lock:
            entry = self._data.get(repo) or {}
        headers = {
            "Accept": "application/vnd.github+json",
            # GitHub API 要求 UA；匿名公开仓库不需要 token
            "User-Agent": "ladderstock-control-plane",
        }
        # 匿名限额 60/h，机器上很容易用光（QUOT-402）；配置了 GITHUB_TOKEN / GH_TOKEN
        # 就带上（只从环境变量读，绝不写进配置文件或代码）
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        etag = entry.get("etag")
        if etag:
            headers["If-None-Match"] = etag
        try:
            import httpx

            resp = httpx.get(_GITHUB_API.format(repo=repo), headers=headers, timeout=10.0)
        except Exception:  # noqa: BLE001 - 断网/超时/代理异常都算「拿不到」，保留旧值
            return
        if resp.status_code == 304:  # 没变：只把时间戳推进，省一次全量写
            self._touch(repo)
            return
        if resp.status_code != 200:
            return
        tag = resp.json().get("tag_name")
        if not isinstance(tag, str) or not tag:
            return
        with self._lock:
            self._data[repo] = {
                "tag": tag.strip().removeprefix("v"),
                "etag": resp.headers.get("ETag"),
                "fetched_at": time.time(),
            }
        self._save()

    def refresh_missing(self, repos: list[str]) -> None:
        """后台线程循环体：只刷 stale 的仓库。"""
        for repo in repos:
            if self.stale(repo):
                self.refresh(repo)

    # ---------------------------------------------------------------- 内部

    def _touch(self, repo: str) -> None:
        with self._lock:
            entry = self._data.get(repo)
            if entry:
                entry["fetched_at"] = time.time()
        self._save()

    def _load(self) -> None:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict) or doc.get("schema") != _CACHE_SCHEMA:
            return
        repos = doc.get("repos")
        if isinstance(repos, dict):
            with self._lock:
                self._data = {
                    repo: entry for repo, entry in repos.items() if isinstance(entry, dict)
                }

    def _save(self) -> None:
        with self._lock:
            snapshot = json.dumps(
                {"schema": _CACHE_SCHEMA, "repos": self._data}, ensure_ascii=False
            )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(snapshot)
            os.replace(tmp_name, self._path)
        except OSError:
            # 缓存写失败不影响主流程：下一次刷新会再试
            pass


def start_release_watcher(cache: ReleaseCache, repos: list[str]) -> threading.Thread | None:
    """启动 daemon 后台线程，每小时刷新一次期望版本；空列表不启动。"""
    repos = [repo for repo in repos if repo]
    if not repos:
        return None

    def _loop() -> None:
        while True:
            cache.refresh_missing(repos)
            time.sleep(_TTL_SEC)

    thread = threading.Thread(target=_loop, daemon=True, name="peer-release-watch")
    thread.start()
    return thread
