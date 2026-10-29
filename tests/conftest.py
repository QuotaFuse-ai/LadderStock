from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> Path:
    """把工作目录切到仓库根，让 `config/` 被 ldst 找到。"""
    monkeypatch.chdir(ROOT)
    return ROOT
