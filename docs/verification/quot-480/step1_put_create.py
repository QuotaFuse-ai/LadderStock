"""QUOT-480 #3 证据步骤 1：控制面 PUT 创建真实 deploy/.env（现网等值基线）。

- PUT 15 个非空键（值取自现网 financecoffer-api-1 容器 env 快照），文件从无到有；
- 空值密钥键（FC_EMBEDDING_API_KEY / FC_MCP_TOKEN）不带：update_config 对空串
  是「不修改」，且 compose 侧 `:-` 兜底空串，行为一致；
- 证据落 fc_verify/：PUT 响应原样（服务端对密钥掩码）、GET /config 原样、
  env 文件内容（密钥行打码后）、icacls 原样。
"""
import json
import subprocess
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8793"
OUT = Path(__file__).resolve().parent
ENV_FILE = Path(r"D:\workplace\FinanceCoffer\deploy\.env")

VALUES = {
    "FC_EMBEDDING_PROVIDER": "openai",
    "FC_EMBEDDING_MODEL": "bge-m3",
    "FC_EMBEDDING_DIM": "1024",
    "FC_EMBEDDING_URL": "http://host.docker.internal:8001/v1/embeddings",
    "FC_MINIO_ACCESS_KEY": "fcoffer",
    "FC_MINIO_SECRET_KEY": "fcoffer_local_secret_2026",
    "FC_MINIO_BUCKET": "financecoffer",
    "FC_EVENT_CONSUMER": "redis",
    "FC_EVENT_STREAM": "fc:stockdata:events",
    "FC_EVENT_GROUP": "fc-etl",
    "FC_COMPLIANCE_ENABLED": "true",
    "FC_RATE_LIMIT_ENABLED": "true",
    "FC_RATE_LIMIT_PER_MINUTE": "120",
    "FC_LOG_LEVEL": "INFO",
    "FC_COLD_AFTER_YEARS": "3",
}

SECRETS = {
    "fcoffer_local_secret_2026": "***MINIO_SECRET_KEY***",
    "fcoffer": "***MINIO_ACCESS_KEY***",
}


def request(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"X-LDST-Client": "1", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover
        return exc.code, exc.read().decode("utf-8", "replace")


def mask(text: str) -> str:
    for raw, token in SECRETS.items():
        text = text.replace(raw, token)
    return text


assert not ENV_FILE.exists(), "deploy/.env 已存在，本步骤预期从无到有"
status, put_resp = request("PUT", "/api/control/peers/financecoffer/config", {"values": VALUES})
(OUT / "put_create_response.json").write_text(
    json.dumps({"status": status, "body": put_resp}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"PUT status={status}")
assert status == 200, put_resp

status, get_resp = request("GET", "/api/control/peers/financecoffer/config")
(OUT / "get_after_create.json").write_text(
    json.dumps({"status": status, "body": get_resp}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"GET status={status} exists={get_resp['exists']}")

raw_env = ENV_FILE.read_text(encoding="utf-8")
(OUT / "env_content_masked.txt").write_text(mask(raw_env), encoding="utf-8")

icacls = subprocess.run(
    ["icacls", str(ENV_FILE)], capture_output=True, text=True, errors="replace", timeout=15, check=False
)
(OUT / "icacls_create.txt").write_text(
    (icacls.stdout or "") + (icacls.stderr or ""), encoding="utf-8"
)
print("icacls:")
print((icacls.stdout or "").strip())

body = put_resp if isinstance(put_resp, dict) else {}
print("updated keys:", body.get("updated"))
secrets_in_resp = [k for k, v in (body.get("values") or {}).items() if isinstance(v, dict) and "value" in v]
print("响应里带明文 value 的键（必须为空）:", secrets_in_resp)
print("响应里的密钥键样例:", json.dumps(body.get("values", {}).get("FC_MINIO_SECRET_KEY"), ensure_ascii=False))
