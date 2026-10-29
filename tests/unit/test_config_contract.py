"""仓库自带配置必须能通过 JSON Schema 校验（测试工程师可直接复核）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


@pytest.mark.parametrize("name", ["sources", "sync", "ai", "state"])
def test_config_matches_schema(name: str) -> None:
    data = yaml.safe_load((CONFIG / f"{name}.yaml").read_text(encoding="utf-8"))
    schema = json.loads((CONFIG / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def test_config_files_carry_no_secrets() -> None:
    for path in CONFIG.glob("*.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        for banned in ("sk-", "api_key:", "secret", "password", "token:"):
            assert banned not in text, f"{path.name} 疑似包含密钥字段：{banned}"
