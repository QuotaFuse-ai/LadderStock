# SPEC-SENATE-ROLE-MODELS StockSenate 辩论角色多模型桥接

- 版本：v1.0（2026-11-10，QUOT-518）
- 关联：QUOT-529（StockSenate 真实契约查明）、QUOT-520（前端页面，本规格不含页面）、
  DEC-021（AI 边界）、`src/stock_senate/schema.py`（角色常量权威来源）
- 实现：`src/ladderstock/web/senate_roles.py`（桥接核心）、
  `src/ladderstock/web/control.py`（`/peers/senate/role-models` 三条路由）、
  `tests/test_senate.py`
- 仓库：只在 `QuotaFuse-ai/LadderStock` 实现；不 checkout / 不修改 StockSenate 源码

## 1. 目标与非目标

**目标**：让 LadderStock 控制面给 StockSenate 的 16 个辩论角色逐角色配置
provider / model，并在保存前做一次最小连通性探测。写入落在 StockSenate 自己的
`config/llm_config.toml`，不新建配置层、不改 StockSenate 校验语义。

**非目标**：

- 不写页面（归 QUOT-520）；后端只提供 JSON API；
- 不启停 / 不控制 peer 进程；
- 不改 `src/stock_senate/` 任何源码、不改 `role_llms` 的 schema 语义；
- 不把密钥写进 TOML / `ai.yaml`；不读 LadderStock 本机 AI 设置当 key；
- 不代理推理请求：只做配置读写与一次最小 chat 探测。

## 2. 契约来源（权威）

桥接启动时 `import stock_senate.schema` 读取常量；布局支持
`<root>/src/stock_senate/`（检出版）与 `<root>/configured/custom/`（部署版），
两处都不可导入时回落到模块内联的常量表，并在 `engine.contract_source` 如实标注
`package` / `fallback`。

| 常量 | 权威值 |
| --- | --- |
| `ROLE_LLM_KEYS` | 16 个角色：`market, social, news, fundamentals, policy, hot_money, lockup, bull, bear, research_manager, trader, risk_aggressive, risk_neutral, risk_conservative, quality_gate, portfolio_manager` |
| `ROLE_ALIASES` | `technical -> market`、`basic -> fundamentals`、`sentiment -> social`、`unlock -> lockup`（照 `schema.ROLE_ALIASES` 逐字复制，桥接不额外发明映射） |
| provider | 7 个真实 provider：`minimax, deepseek, openai, dashscope, qwen, moonshot, zhipu`；另有 `offline` 本地引擎档 |
| 角色条目字段 | 只有 `provider` / `model`（`schema._LLM_ROLE_ENTRY`）；**角色条目没有 `api_key_env`** |
| 全局档 | `[llm] llm_provider / deep_think_llm / quick_think_llm / backend_url / api_key_env` 等 |

注意：`technical` 在 `schema.ROLE_ALIASES` 里是 `market` 的**别名**（不是 16 键之一）。
桥接照抄该别名表并在 `aliases` 字段如实回显；`technical` 不会被"提升"成独立角色，
也不会被丢弃——提交 `technical` 会归一到 `market` 并在 `aliases_rewritten` 里说明。
16 个 canonical 键里没有 `technical`，也没有 `macro / smart_money / game_theory /
volume_price / risk_arbitrage`。

`config/edit.py` 是 StockSenate 侧唯一的配置写入模块，但它只写
`agent_roles.toml`，没有 `llm_config.toml` 的写入函数，因此 TOML 文本改写由桥接
自己完成（见 §5），并在写前用 StockSenate 的 schema 语义校验候选文件。

## 3. 端点

三条路由都在 `/api/control` 前缀下、经过 `_guard`（控制面开关 + 回环来源 +
写方法要 `X-LDST-Client: 1` 或同源 `Origin`）。`/peers/senate` 本体仍返回 peer
快照；`/peers/senate/role-models*` 是两段以上静态路径，与 `/peers/{peer_id}`
（单段）不冲突。

### 3.1 `GET /peers/senate/role-models`

```json
{
  "engine": {
    "provider": "minimax",          // 生效值（环境变量优先）
    "file_provider": "minimax",     // llm_config.toml 里的值
    "env_override": null,           // STOCKSENATE_LLM_PROVIDER
    "authority": "env",
    "reload": "webui_per_job",
    "hint": "…",
    "reload_hint": "…",
    "contract_source": "package"
  },
  "roles": [ { "role": "market", "provider": "…", "model": "…", "configured": true, "global_key_env": null } ],
  "unconfigured_roles": ["…"],
  "role_count": 16,
  "global": { "llm_provider": "…", "deep_think_llm": "…", "quick_think_llm": "…", "backend_url": "", "api_key_env": null },
  "providers": [ { "name": "minimax", "models": ["…"], "key_env": "MINIMAX_API_KEY", "default": true }, "…", { "name": "offline", … } ],
  "aliases": { "…": "…" },
  "directory_exists": true,
  "config_exists": true,
  "env_file": "…/.env",
  "env_keys": ["MINIMAX_API_KEY"],
  "revision": "<内容指纹>"
}
```

- `engine.authority = "env"`、`engine.reload = "webui_per_job"`：StockSenate 在
  进程启动时抓一次 provider，改完要重新起一次任务才生效，`reload_hint` 说明这点。
- `roles[]` 里 `global_key_env` 来自全局 `[llm].api_key_env`（角色条目本身没有该字段）。
- `providers[]` 必含 `minimax`（默认档）与 `offline`。

### 3.2 `PUT /peers/senate/role-models`

```json
{
  "roles": { "risk_neutral": { "provider": "deepseek", "model": "deepseek-chat" } },
  "expected_revision": "<GET 拿到的 revision，可选>"
}
```

- 角色键必须是 16 个 canonical 键之一（别名会归一到 canonical 并回显
  `aliases_rewritten`）。
- 每个角色只接受 `provider` / `model` / `api_key_env`；出现其它字段一律拒绝，
  字段名像密钥（`api_key` / `token` / `secret` …）时报 `SENATE_SECRET_REJECTED`。
- `api_key_env` 只接受**变量名**（字母数字下划线），会写进全局
  `[llm].api_key_env`，**不**写进角色条目；内容像密钥则 `SENATE_SECRET_REJECTED`。
- 带 `expected_revision` 且与磁盘指纹不一致 → `SENATE_STALE`（409），错误体带
  `code` / `message`，不回显提交值。
- 成功返回 `status()` 全量 + `updated` / `aliases_rewritten` / `revision` /
  `api_key_env_written`。

### 3.3 `POST /peers/senate/role-models/test`

```json
{ "role": "market", "provider": "minimax", "model": "MiniMax-M2", "api_key_env": "MINIMAX_API_KEY" }
```

后三个字段可选，缺省回落到已保存值。探测失败也回 **200**，由 `ok` / `error_code`
区分：

```json
{ "role": "market", "provider": "minimax", "provider_source": "role", "model": "MiniMax-M2",
  "ok": false, "skipped": false, "latency_ms": 12,
  "error_code": "SENATE_NO_API_KEY", "message": "…" }
```

- provider 解析顺序：请求 `provider` → 环境覆盖/全局 `offline` 短路 → 角色已保存
  provider → 全局 `llm_provider` → `offline`。`offline` 直接返回
  `{ok: true, skipped: true}`，不发任何网络请求。
- key **只**从进程环境变量与 Senate 的 `.env` 两个文件读（`api_key_env` →
  `STOCKSENATE_LLM_API_KEY` → provider 默认变量），不读 LadderStock 本机 AI 设置。
- 请求发往 `chat/completions`（`model` + 一条 `ping`，`max_tokens=8`，`temperature=0`），
  沿用 `[llm].backend_url`；只有请求里显式给了 `provider` 才走该 provider 默认端点。
- 回包永不包含密钥；错误信息里的 key 会打码（`****后四位`）。

## 4. 错误码

| code | HTTP | 含义 |
| --- | --- | --- |
| `SENATE_UNAVAILABLE` | 409 | StockSenate 目录 / `llm_config.toml` 不存在或读不出来 |
| `SENATE_INVALID_VALUE` | 400 | 角色键、字段、取值不合法（不回显提交值） |
| `SENATE_SECRET_REJECTED` | 400 | 提交了密钥内容或像密钥的字段名 |
| `SENATE_STALE` | 409 | `expected_revision` 与磁盘不一致 |
| `SENATE_WRITE_FAILED` | 500 | 候选文件校验失败或落盘失败（原文件未动） |
| `SENATE_NO_API_KEY` | 400 | 探测时找不到 key（probe 结果码，HTTP 仍 200） |
| `SENATE_NETWORK_DENIED` | 403 | 目标解析到内网/保留/本机地址，或 provider 拒绝密钥 |
| `SENATE_UNREACHABLE` | 502 | 解析失败、超时、限流或 provider 返回非 2xx |

三条路由的错误体统一为 `{"detail": {"code": "SENATE_*", "message": "…"}}`；
请求体解析/字段校验失败走 400 + `SENATE_INVALID_VALUE` / `SENATE_SECRET_REJECTED`，
**不回显**用户提交的内容。

## 5. 写路径（fail-closed）

1. 读 `llm_config.toml` 原文 → 合并既有 `role_llms`（丢掉含非法字段的存量条目）→
   按 16 角色顺序重排 → 文本级替换 `[llm.role_llms]` 表（注释、其它表原样保留）。
2. 候选文本写进同目录临时文件 → 用 `stock_senate.schema` 语义校验候选 → 校验通过
   才 `os.replace` 覆盖；覆盖前用 `copy2` 留备份（`write_ai_yaml` 同款姿态）。
3. 首次生成（文件不存在）时在临时目录里校验后 `os.replace`，不产出备份。

任何一步失败都保留原文件并返回 `SENATE_WRITE_FAILED` / `SENATE_STALE`。

## 6. 回归测试

`tests/test_senate.py`（`python -m unittest tests.test_senate -v`）：

- 契约层：16 角色、7 provider + offline、无臆造 provider、别名随 schema、离线短路、
  密钥不回显、PUT 保留注释 / 空文件首建 / 冲突 / 别名归一、探测四类错误码。
- 真检出回归：设置 `STOCKSENATE_ROOT`（或存在本地检出）时额外断言 16 键与
  `schema.ROLE_LLM_KEYS` 逐字相等、`providers` 含 `minimax`、别名与
  `schema.ROLE_ALIASES` 逐字相等、在真实 `llm_config.toml` 副本上 PUT
  `risk_neutral` 成功；未设置则跳过（不依赖手工拷贝文件）。

## 7. 变更记录

- v1.0（2026-11-10，QUOT-518）：首版。GET / PUT / POST test 三条路由、`SENATE_*`
  错误码、注释保留 + 校验 + 原子写 + 乐观锁；明确 `api_key_env` 落全局、角色条目
  只认 provider/model、provider 7 个含 minimax、别名为 `technical -> market` 等且
  `technical` 不是 canonical 角色。
