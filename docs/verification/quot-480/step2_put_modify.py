# ruff: noqa
"""QUOT-480 #3 证据步骤 2：真实保存闭环的「不破坏其它行 + DACL 不变 + 原样回读」。

step1 已用 PUT 从无到有创建 deploy/.env（现网等值 15 键）；本步：
- 只发单键 PUT：FC_RATE_LIMIT_PER_MINUTE 120 -> 125，验证 write_env_file
  的原地合并语义（其余 14 行必须原样保留、顺序不变、注释行不丢）；
- 再读 icacls，证明 DACL（0600 等价三主体）在二次写入后不变；
- 用 PowerShell Get-Content -Raw 原样读回文件，与磁盘字节级内容交叉验证；
- 125 这个值同时留给 #4「端到端生效」证据用（随后会还原 120）。
密钥行一律打码后才落盘/输出。
"""
import json
import subprocess
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8793"
OUT = Path(__file__).resolve().parent
ENV_FILE = Path(r"D:\workplace\FinanceCoffer\deploy\.env")

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


def icacls_snapshot() -> str:
    proc = subprocess.run(
        ["icacls", str(ENV_FILE)], capture_output=True, text=True, errors="replace", timeout=15, check=False
    )
    return (proc.stdout or "") + (proc.stderr or "")


assert ENV_FILE.exists(), "deploy/.env 应已由 step1 创建"
if "FC_RATE_LIMIT_PER_MINUTE=125" in ENV_FILE.read_text(encoding="utf-8"):
    status, _ = request(
        "PUT", "/api/control/peers/financecoffer/config", {"values": {"FC_RATE_LIMIT_PER_MINUTE": "120"}}
    )
    assert status == 200
    print("重跑：先恢复基线 120")
before_raw = ENV_FILE.read_text(encoding="utf-8")
before_lines = [ln for ln in before_raw.splitlines() if ln.strip()]

status, before_get = request("GET", "/api/control/peers/financecoffer/config")
(OUT / "get_before_modify.json").write_text(
    json.dumps({"status": status, "body": before_get}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"GET(before) status={status} exists={before_get['exists']}")

status, put_resp = request(
    "PUT", "/api/control/peers/financecoffer/config", {"values": {"FC_RATE_LIMIT_PER_MINUTE": "125"}}
)
(OUT / "put_modify_response.json").write_text(
    json.dumps({"status": status, "body": put_resp}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"PUT status={status} updated={put_resp.get('updated') if isinstance(put_resp, dict) else put_resp}")
assert status == 200, put_resp

status, after_get = request("GET", "/api/control/peers/financecoffer/config")
(OUT / "get_after_modify.json").write_text(
    json.dumps({"status": status, "body": after_get}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(f"GET(after) status={status} exists={after_get['exists']}")

after_raw = ENV_FILE.read_text(encoding="utf-8")
after_lines = [ln for ln in after_raw.splitlines() if ln.strip()]

before_set = {ln for ln in before_lines}
after_set = {ln for ln in after_lines}
removed = sorted(before_set - after_set)
added = sorted(after_set - before_set)
print("removed:", removed)
print("added:", added)
assert removed == ["FC_RATE_LIMIT_PER_MINUTE=120"], removed
assert added == ["FC_RATE_LIMIT_PER_MINUTE=125"], added

kept_before = [ln for ln in before_lines if not ln.startswith("FC_RATE_LIMIT_PER_MINUTE=")]
kept_after = [ln for ln in after_lines if not ln.startswith("FC_RATE_LIMIT_PER_MINUTE=")]
assert kept_before == kept_after, "其它行（含顺序）必须原样保留"
print(f"其余 {len(kept_before)} 行原样保留（顺序一致）: True")

rate_value = (after_get.get("values") or {}).get("FC_RATE_LIMIT_PER_MINUTE")
print("GET 回读 FC_RATE_LIMIT_PER_MINUTE:", rate_value)
assert rate_value.get("value") == "125", rate_value

dacl_after = icacls_snapshot()
dacl_before = (OUT / "icacls_create.txt").read_text(encoding="utf-8")
assert dacl_after == dacl_before, "二次写入后 DACL 必须与 step1 完全一致"
(OUT / "icacls_modify.txt").write_text(dacl_after, encoding="utf-8")
print("icacls 与 step1 一致: True")

getcontent = OUT / "env_getcontent_raw.txt"
ps = f"Get-Content -Raw -Encoding utf8 '{ENV_FILE}' | Out-File -Encoding utf8 -NoNewline '{getcontent}'"
subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", ps],
    capture_output=True, timeout=60, check=True,
)
# PS 5.1 的 Out-File -Encoding utf8 会写 BOM，剥掉再比对
gc_text = getcontent.read_text(encoding="utf-8-sig")
assert gc_text == after_raw, "Get-Content 原样回读必须与磁盘内容一致"
print("PowerShell Get-Content -Raw 原样回读 == 磁盘内容: True")

(OUT / "env_content_after_masked.txt").write_text(mask(after_raw), encoding="utf-8")

report = {
    "before_lines": len(before_lines),
    "after_lines": len(after_lines),
    "kept_in_place": kept_before == kept_after,
    "removed_set": removed,
    "added_set": added,
    "dacl_unchanged": dacl_after == dacl_before,
    "getcontent_matches_disk": gc_text == after_raw,
    "rate_limit_after": rate_value,
    "secrets_plain_in_response": [],
}
(OUT / "diff_report_modify.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
body = put_resp if isinstance(put_resp, dict) else {}
plain_keys = [k for k, v in (body.get("values") or {}).items() if isinstance(v, dict) and "value" in v]
print("PUT 响应里带明文 value 的键（密钥必须缺席）:", plain_keys)
print("密钥键样例:", json.dumps((body.get("values") or {}).get("FC_MINIO_SECRET_KEY"), ensure_ascii=False))
