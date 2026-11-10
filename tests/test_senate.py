"""QUOT-518：StockSenate 辩论角色多模型桥接（`/api/control/peers/senate/role-models`）。

分两层：

1. **契约层**（`unittest` 跑，不联网、不碰真 Senate 目录）：GET 的形状、PUT 的
   校验 / 原子写 / 注释保留 / `SENATE_STALE`、test 的三类可区分错误
   （offline 跳过 / 无 key / 网络被拒）。
2. **真仓回归**（`STOCKSENATE_ROOT` 指向 StockSenate 检出目录才跑，否则 skip）：
   角色的 16 个键、7 个 provider 含 minimax、`technical` 是否被映射一律以
   **真实包** `ROLE_ALIASES` 为准、`risk_neutral` 可以写进去——不靠手工复制文件。

运行：`python -m unittest tests.test_senate -v`
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
for candidate in (REPO_ROOT / "src", REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ladderstock.core.boundary import PEER_CONTROL  # noqa: E402
from ladderstock.web import senate_roles  # noqa: E402
from ladderstock.web.control import ControlPlane  # noqa: E402
from ladderstock.web.server import create_app  # noqa: E402

LOOPBACK = ("127.0.0.1", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
SENTINEL_KEY = "sk-quot518-SENTINEL-do-not-print"
REAL_PROVIDERS = ("minimax", "deepseek", "openai", "dashscope", "qwen", "moonshot", "zhipu")

REAL_LLM_CONFIG = """# StockSenate 模型配置
# 这段注释必须原样保留

[llm]
# 七个基础键，全部保留。
llm_provider = "minimax"
deep_think_llm = "MiniMax-M2"
quick_think_llm = "MiniMax-M2"
backend_url = ""            # 留空用 provider 默认
max_tokens = 8192

[llm.role_llms]
# 细粒度覆盖：同一 provider+model 共用一个客户端。
market = { provider = "minimax", model = "MiniMax-M2" }
social = { provider = "minimax", model = "MiniMax-M2" }
bull = { provider = "minimax", model = "MiniMax-M2" }
risk_neutral = { provider = "deepseek", model = "deepseek-chat" }

[llm.allowed_keys]
keys = ["market", "social", "bull", "risk_neutral"]
strict = false

[llm.fallback]
chain = []
"""


def _llm_config(provider: str) -> str:
    return REAL_LLM_CONFIG.replace('llm_provider = "minimax"', f'llm_provider = "{provider}"')


def _dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def _backend(
    root: Path,
    *,
    http: _HttpPost,
    environ: dict[str, str] | None = None,
    resolve_hosts: list[str] | None = None,
) -> senate_roles.SenateRoleModels:
    ctl = replace(PEER_CONTROL["senate"], directory=str(root), env_filename=".env")
    env_file = root / ".env"
    return senate_roles.SenateRoleModels(
        ctl=ctl,
        env_reader=lambda _ctl: _dotenv(env_file),
        http_post=http,
        environ={} if environ is None else environ,
        resolve=lambda _host: list(resolve_hosts or ["93.184.216.34"]),
        is_forbidden=lambda ip: ip.startswith("169.254") or ip.startswith("10."),
        clock=lambda: 0.0,
    )


class _SenateRoot:
    """临时 StockSenate 检出目录：`config/llm_config.toml` + `.env`。"""

    def __init__(self, base: Path, text: str = REAL_LLM_CONFIG) -> None:
        self.root = base / "StockSenate"
        (self.root / "config").mkdir(parents=True)
        self.config_path = self.root / "config" / "llm_config.toml"
        self.config_path.write_text(text, encoding="utf-8")
        (self.root / ".env").write_text("", encoding="utf-8")

    @property
    def config_text(self) -> str:
        return self.config_path.read_text(encoding="utf-8")

    def parsed(self) -> dict:
        return tomllib.loads(self.config_text)


class _HttpPost:
    def __init__(self, status: int = 200, body: str = '{"model": "MiniMax-M2"}') -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict, dict, float]] = []
        self.raises: Exception | None = None

    def __call__(self, url, payload, headers, timeout):
        self.calls.append((url, payload, headers, timeout))
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


class _SenateCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.senate = _SenateRoot(self.tmp)
        self.http = _HttpPost()
        self.environ: dict[str, str] = {}
        self.resolve_hosts = ["93.184.216.34"]
        # 注入的 environ / DNS 结果在构造时快照；要换桩就在用例里重新 `self._backend(...)`。
        self._default_backend = self._backend(self.senate.root)

    @property
    def backend(self) -> senate_roles.SenateRoleModels:
        return self._default_backend

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _backend(
        self, root: Path, *, http: _HttpPost | None = None, environ: dict[str, str] | None = None
    ) -> senate_roles.SenateRoleModels:
        return _backend(
            root,
            http=http or self.http,
            environ=self.environ if environ is None else environ,
            resolve_hosts=self.resolve_hosts,
        )

    def _http_client(self, root: Path | None = None) -> TestClient:
        root = self.senate.root if root is None else root
        plane = ControlPlane(
            data_root=self.tmp / "data",
            peers={
                "senate": replace(PEER_CONTROL["senate"], directory=str(root), env_filename=".env")
            },
            sources_loader=lambda: {},
            port_probe=lambda port: [],
            health_probe=lambda url, timeout: (True, "HTTP 200"),
            resolve=lambda host: list(self.resolve_hosts),
            http_post=self.http,
        )
        app = create_app(
            data_root=self.tmp / "data",
            dist_dir=self.tmp / "missing",
            control_plane=plane,
        )
        return TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)


# --------------------------------------------------------------------------- #
# 契约层：GET
# --------------------------------------------------------------------------- #


class GetContractTest(_SenateCase):
    def test_status_lists_sixteen_roles_and_real_providers(self) -> None:
        payload = self.backend.status()
        self.assertEqual(payload["role_count"], 16)
        self.assertEqual(len(payload["roles"]), 16)
        self.assertIn("risk_neutral", [role["role"] for role in payload["roles"]])
        names = [provider["name"] for provider in payload["providers"]]
        for expected in (*REAL_PROVIDERS, "offline"):
            self.assertIn(expected, names)
        invented = ("anthropic", "google", "openrouter", "ollama", "lmstudio", "custom", "azure")
        for name in invented:
            self.assertNotIn(name, names)
        minimax = next(p for p in payload["providers"] if p["name"] == "minimax")
        self.assertEqual(minimax["api_key_env"], "MINIMAX_API_KEY")
        for key in (
            "engine",
            "roles",
            "unconfigured_roles",
            "role_count",
            "global",
            "providers",
            "aliases",
        ):
            self.assertIn(key, payload)

    def test_status_reflects_saved_entries_and_unconfigured_roles(self) -> None:
        payload = self.backend.status()
        by_role = {role["role"]: role for role in payload["roles"]}
        self.assertTrue(by_role["market"]["configured"])
        self.assertEqual(by_role["market"]["model"], "MiniMax-M2")
        self.assertEqual(by_role["risk_neutral"]["provider"], "deepseek")
        self.assertFalse(by_role["portfolio_manager"]["configured"])
        self.assertIn("portfolio_manager", payload["unconfigured_roles"])
        self.assertEqual(payload["global"]["provider"], "minimax")
        self.assertEqual(payload["engine"]["authority"], "env")
        self.assertEqual(payload["engine"]["reload"], "webui_per_job")
        self.assertTrue(payload["revision"])

    def test_technical_alias_follows_contract_not_bridge_invention(self) -> None:
        payload = self.backend.status()
        self.assertEqual(
            payload["aliases"].get("technical"),
            senate_roles.ROLE_ALIASES.get("technical"),
        )
        self.assertEqual(payload["aliases"].get("unlock"), "lockup")

    def test_offline_engine_is_reported_with_hint(self) -> None:
        senate = _SenateRoot(self.tmp / "offline", _llm_config("offline"))
        payload = self._backend(senate.root).status()
        self.assertEqual(payload["global"]["provider"], "offline")
        engine = payload["engine"]
        self.assertIn("offline", engine["hint"])
        self.assertTrue(engine["hint"])
        self.assertIn("重启", engine["reload_hint"])

    def test_http_get_hits_bridge_not_peer_snapshot(self) -> None:
        response = self._http_client().get("/api/control/peers/senate/role-models")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["role_count"], 16)
        self.assertNotIn("status_label", body)

    def test_missing_directory_resolves_to_bridge_error(self) -> None:
        with self.assertRaises(senate_roles.SenateError) as raised:
            self._backend(self.tmp / "absent").update(
                {"market": {"provider": "minimax", "model": "MiniMax-M2"}}
            )
        self.assertEqual(raised.exception.code, "SENATE_UNAVAILABLE")
        self.assertEqual(raised.exception.status_code, 409)


# --------------------------------------------------------------------------- #
# 契约层：PUT
# --------------------------------------------------------------------------- #


class PutContractTest(_SenateCase):
    def test_update_writes_role_and_preserves_comments(self) -> None:
        before = self.senate.config_text
        payload = self.backend.update({"risk_neutral": {"provider": "zhipu", "model": "glm-4.6"}})
        after = self.senate.config_text
        self.assertIn("# 这段注释必须原样保留", after)
        self.assertIn("# 留空用 provider 默认", after)
        parsed = self.senate.parsed()["llm"]
        self.assertEqual(parsed["role_llms"]["risk_neutral"]["provider"], "zhipu")
        self.assertEqual(parsed["role_llms"]["market"]["model"], "MiniMax-M2")
        self.assertEqual(parsed["max_tokens"], 8192)
        self.assertEqual(
            parsed["allowed_keys"]["keys"], ["market", "social", "bull", "risk_neutral"]
        )
        self.assertIn("risk_neutral", payload["updated"])
        self.assertTrue(payload["revision"])
        self.assertNotEqual(before, after)

    def test_empty_config_is_created_with_canonical_keys(self) -> None:
        root = self.tmp / "fresh" / "StockSenate"
        root.mkdir(parents=True)
        backend = self._backend(root)
        backend.update({"market": {"provider": "minimax", "model": "MiniMax-M2"}})
        parsed = tomllib.loads((root / "config" / "llm_config.toml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["llm"]["role_llms"]["market"]["provider"], "minimax")

    def test_unknown_role_rejected_and_file_untouched(self) -> None:
        before = self.senate.config_text
        with self.assertRaises(senate_roles.SenateError) as raised:
            self.backend.update({"hedge_fund": {"provider": "minimax", "model": "x"}})
        self.assertEqual(raised.exception.code, "SENATE_INVALID_VALUE")
        self.assertEqual(self.senate.config_text, before)

    def test_secret_must_not_be_put_into_toml(self) -> None:
        before = self.senate.config_text
        with self.assertRaises(senate_roles.SenateError) as raised:
            self.backend.update(
                {"bull": {"provider": "minimax", "model": "MiniMax-M2", "api_key": SENTINEL_KEY}}
            )
        self.assertEqual(raised.exception.code, "SENATE_SECRET_REJECTED")
        with self.assertRaises(senate_roles.SenateError) as raised:
            self.backend.update(
                {
                    "bull": {
                        "provider": "minimax",
                        "model": "MiniMax-M2",
                        "api_key_env": f"MINIMAX_API_KEY={SENTINEL_KEY}",
                    }
                }
            )
        self.assertEqual(raised.exception.code, "SENATE_SECRET_REJECTED")
        self.assertEqual(self.senate.config_text, before)
        self.assertNotIn(SENTINEL_KEY, self.senate.config_text)

    def test_api_key_env_stores_variable_name_only(self) -> None:
        self.backend.update(
            {
                "bull": {
                    "provider": "minimax",
                    "model": "MiniMax-M2",
                    "api_key_env": "MINIMAX_API_KEY",
                }
            }
        )
        llm = self.senate.parsed()["llm"]
        # StockSenate 的角色条目只认 provider / model：变量名落在全局 [llm].api_key_env。
        self.assertEqual(llm["role_llms"]["bull"], {"provider": "minimax", "model": "MiniMax-M2"})
        self.assertEqual(llm["api_key_env"], "MINIMAX_API_KEY")
        self.assertNotIn(SENTINEL_KEY, self.senate.config_text)

    def test_stale_revision_rejected(self) -> None:
        before = self.senate.config_text
        with self.assertRaises(senate_roles.SenateError) as raised:
            self.backend.update(
                {"market": {"provider": "openai", "model": "gpt-4.1-mini"}},
                expected_revision="deadbeefdeadbeef",
            )
        self.assertEqual(raised.exception.code, "SENATE_STALE")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.senate.config_text, before)

    def test_current_revision_accepted(self) -> None:
        revision = self.backend.status()["revision"]
        self.backend.update(
            {"market": {"provider": "openai", "model": "gpt-4.1-mini"}},
            expected_revision=revision,
        )
        self.assertEqual(self.senate.parsed()["llm"]["role_llms"]["market"]["provider"], "openai")

    def test_existing_alias_key_is_canonicalised(self) -> None:
        config_path = self.senate.config_path
        config_path.write_text(
            self.senate.config_text.replace(
                'market = { provider = "minimax", model = "MiniMax-M2" }',
                'unlock = { provider = "minimax", model = "MiniMax-M2" }',
            ),
            encoding="utf-8",
        )
        self.backend.update({"lockup": {"provider": "moonshot", "model": "kimi-k2-0905-preview"}})
        role_llms = self.senate.parsed()["llm"]["role_llms"]
        self.assertNotIn("unlock", role_llms)
        self.assertEqual(role_llms["lockup"]["provider"], "moonshot")

    def test_http_put_writes_and_reports_revision(self) -> None:
        client = self._http_client()
        revision = client.get("/api/control/peers/senate/role-models").json()["revision"]
        response = client.put(
            "/api/control/peers/senate/role-models",
            json={
                "roles": {"portfolio_manager": {"provider": "zhipu", "model": "glm-4.6"}},
                "expected_revision": revision,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["updated"], ["portfolio_manager"])
        self.assertNotIn("portfolio_manager", body["unconfigured_roles"])
        stale = client.put(
            "/api/control/peers/senate/role-models",
            json={
                "roles": {"portfolio_manager": {"provider": "zhipu", "model": "glm-4.6"}},
                "expected_revision": revision,
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "SENATE_STALE")

    def test_http_put_error_envelope(self) -> None:
        response = self._http_client().put(
            "/api/control/peers/senate/role-models",
            json={"roles": {"bogus": {"provider": "minimax", "model": "x"}}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "SENATE_INVALID_VALUE")

    def test_http_put_rejects_secret_field(self) -> None:
        before = self.senate.config_text
        response = self._http_client().put(
            "/api/control/peers/senate/role-models",
            json={
                "roles": {
                    "bull": {"provider": "minimax", "model": "MiniMax-M2", "api_key": SENTINEL_KEY}
                }
            },
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "SENATE_SECRET_REJECTED")
        self.assertIn("api_key", detail["message"])
        self.assertEqual(self.senate.config_text, before)
        self.assertNotIn(SENTINEL_KEY, response.text)


# --------------------------------------------------------------------------- #
# 契约层：POST test
# --------------------------------------------------------------------------- #


class TestProbeContract(_SenateCase):
    def test_offline_short_circuits_without_network(self) -> None:
        senate = _SenateRoot(self.tmp / "offline", _llm_config("offline"))
        payload = self._backend(senate.root).test(role="market")
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["skipped"])
        self.assertIsNone(payload["error_code"])
        self.assertEqual(payload["provider"], "offline")
        self.assertEqual(self.http.calls, [])

    def test_missing_key_is_distinguishable(self) -> None:
        payload = self.backend.test(role="market")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "SENATE_NO_API_KEY")
        self.assertEqual(self.http.calls, [])

    def test_key_from_env_file_is_used_but_never_echoed(self) -> None:
        (self.senate.root / ".env").write_text(
            "MINIMAX_API_KEY=" + SENTINEL_KEY + chr(10), encoding="utf-8"
        )
        payload = self.backend.test(role="market")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(len(self.http.calls), 1)
        url, body, headers, _ = self.http.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(headers["Authorization"], f"Bearer {SENTINEL_KEY}")
        self.assertEqual(body["model"], "MiniMax-M2")
        self.assertNotIn(SENTINEL_KEY, json.dumps(payload, ensure_ascii=False))

    def test_internal_address_with_key_denied(self) -> None:
        self.environ["MINIMAX_API_KEY"] = SENTINEL_KEY
        self.resolve_hosts = ["10.0.0.8"]
        payload = self._backend(self.senate.root).test(role="market")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "SENATE_NETWORK_DENIED")
        self.assertEqual(self.http.calls, [])
        self.assertNotIn(SENTINEL_KEY, json.dumps(payload, ensure_ascii=False))

    def test_loopback_with_key_denied(self) -> None:
        self.environ["MINIMAX_API_KEY"] = SENTINEL_KEY
        self.resolve_hosts = ["127.0.0.1"]
        payload = self._backend(self.senate.root).test(role="market")
        self.assertEqual(payload["error_code"], "SENATE_NETWORK_DENIED")
        self.assertEqual(self.http.calls, [])

    def test_network_failure_is_unreachable(self) -> None:
        self.environ["MINIMAX_API_KEY"] = SENTINEL_KEY
        self.http.raises = urllib.error.URLError("connection refused")
        payload = self.backend.test(role="market")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "SENATE_UNREACHABLE")
        self.assertNotIn(SENTINEL_KEY, json.dumps(payload, ensure_ascii=False))

    def test_unauthorized_reports_denied(self) -> None:
        self.environ["MINIMAX_API_KEY"] = SENTINEL_KEY
        self.http.status = 401
        self.http.body = '{"error": "invalid key"}'
        payload = self.backend.test(role="market")
        self.assertEqual(payload["error_code"], "SENATE_NETWORK_DENIED")

    def test_request_override_does_not_touch_saved_config(self) -> None:
        before = self.senate.config_text
        (self.senate.root / ".env").write_text(
            "MINIMAX_API_KEY=" + SENTINEL_KEY + chr(10), encoding="utf-8"
        )
        payload = self.backend.test(role="market", provider="minimax", model="MiniMax-M2")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(self.senate.config_text, before)

    def test_unknown_role_rejected(self) -> None:
        with self.assertRaises(senate_roles.SenateError) as raised:
            self.backend.test(role="hedge_fund")
        self.assertEqual(raised.exception.code, "SENATE_INVALID_VALUE")

    def test_http_probe_is_200_with_error_code(self) -> None:
        response = self._http_client().post(
            "/api/control/peers/senate/role-models/test", json={"role": "market"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["error_code"], "SENATE_NO_API_KEY")


# --------------------------------------------------------------------------- #
# 真仓回归（STOCKSENATE_ROOT 指向 StockSenate 检出目录才跑）
# --------------------------------------------------------------------------- #


def _senate_root() -> Path | None:
    raw = (os.environ.get("STOCKSENATE_ROOT") or "").strip()
    if raw:
        candidate = Path(raw)
        return candidate if (candidate / "src" / "stock_senate").is_dir() else None
    sibling = REPO_ROOT.parent / "StockSenate"
    if (sibling / "src" / "stock_senate").is_dir():
        return sibling
    return None


STOCKSENATE_ROOT = _senate_root()


def _real_schema(root: Path):
    sys.path.insert(0, str(root / "src"))
    try:
        from stock_senate.config import schema
    finally:
        sys.path.remove(str(root / "src"))
    return schema


@unittest.skipUnless(
    STOCKSENATE_ROOT is not None,
    "未设置 STOCKSENATE_ROOT、也不是同级检出：跳过真仓回归（不靠手工复制文件换绿）",
)
class RealCheckoutRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert STOCKSENATE_ROOT is not None
        cls.root = STOCKSENATE_ROOT
        cls.schema = _real_schema(cls.root)
        cls.contract, cls.authoritative = senate_roles.load_contract(cls.root)

    def test_contract_comes_from_the_package(self) -> None:
        self.assertTrue(self.authoritative, "真实包导入失败，退回了内联镜像")

    def test_sixteen_role_keys(self) -> None:
        self.assertEqual(len(self.contract.roles), 16)
        self.assertEqual(tuple(self.schema.ROLE_LLM_KEYS), self.contract.roles)

    def test_providers_include_minimax(self) -> None:
        self.assertIn("minimax", self.contract.providers)
        for name in REAL_PROVIDERS:
            self.assertIn(name, self.contract.providers)
        self.assertEqual(self.contract.provider_key_env["minimax"], "MINIMAX_API_KEY")

    def test_aliases_are_copied_verbatim_from_schema(self) -> None:
        # 桥接不自创映射：technical 是否映射到 market 完全由 StockSenate 的
        # ROLE_ALIASES 决定；两边必须逐项一致。
        self.assertEqual(self.contract.aliases, dict(self.schema.ROLE_ALIASES))
        for role in self.contract.roles:
            self.assertNotIn(role, self.contract.aliases)

    def test_inline_tables_match_real_schema(self) -> None:
        # 导入失败时的内联兜底必须逐字等于真包，否则离线环境会静默漂移。
        self.assertEqual(tuple(senate_roles.ROLE_LLM_KEYS), tuple(self.schema.ROLE_LLM_KEYS))
        self.assertEqual(senate_roles.ROLE_ALIASES, dict(self.schema.ROLE_ALIASES))

    def test_put_risk_neutral_on_real_config_copy(self) -> None:
        import shutil

        real_config = self.root / "config" / "llm_config.toml"
        if not real_config.is_file():
            self.skipTest(f"检出里没有 {real_config}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "StockSenate"
            shutil.copytree(self.root / "config", root / "config")
            (root / ".env").write_text("", encoding="utf-8")
            http = _HttpPost()
            backend = _backend(root, http=http)
            before = (root / "config" / "llm_config.toml").read_text(encoding="utf-8")
            payload = backend.update(
                {"risk_neutral": {"provider": "deepseek", "model": "deepseek-chat"}}
            )
            self.assertIn("risk_neutral", payload["updated"])
            after = (root / "config" / "llm_config.toml").read_text(encoding="utf-8")
            parsed = tomllib.loads(after)
            self.assertEqual(
                parsed["llm"]["role_llms"]["risk_neutral"],
                {"provider": "deepseek", "model": "deepseek-chat"},
            )
            self.assertEqual(
                parsed["llm"]["llm_provider"], tomllib.loads(before)["llm"]["llm_provider"]
            )
            probe = backend.test(role="risk_neutral")
            self.assertEqual(probe["provider"], "deepseek")
            self.assertEqual(probe["model"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=io.StringIO())
