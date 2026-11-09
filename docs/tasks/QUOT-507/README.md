# QUOT-507 代理池卡片：映射列表 + 四种状态 + AI 归属折叠 + 真实 RTT

- Issue：QUOT-507（QUOT-484 的未交付子项，依赖 QUOT-490 / QUOT-506 后端）
- 范围：只改前端 `frontend/`，不碰后端、supervisor 内核与 QUOT-484 已交付的停止清单
- 分支：`agent/agent/b3f1c74cfa3c` → PR base `main`

## 交付内容

「子模块」列表里新增一张**代理池卡片**，与现有模块卡片同列同壳（`rounded border bg-surface
shadow-card` + 既有 Button / Badge 语气），逐条展示 `mappings`：

| 监听端口 | 上游 host:port | 状态 | 归属 |
| --- | --- | --- | --- |
| `127.0.0.1:8788` | `api.openai.com:443` | 运行中 · 12ms | 人工使用 |
| `127.0.0.1:8790` | `api.moonshot.cn:443` | 冲突 · 端口 8790（红） | 人工使用 |
| `127.0.0.1:8791` | `api.anthropic.com:443` | 需人工处理（已停止自愈）（红）+ 重试 | 人工使用 |

### 归属与折叠

- 归属**只**取后端 `mappings[].owner`：`ai` → `AI 使用`，`human` → `人工使用`，无前端推断。
- `AI 使用` 的 mapping 默认折叠成一行摘要：`3 条映射 · 2 条运行中`（有冲突 / 需人工时
  追加 `· N 条异常`），点击摘要行展开明细；人工 mapping 始终逐行可见。

### 四种状态

| 后端 `mappings[].state` | 文案 | 视觉 | 重试入口 |
| --- | --- | --- | --- |
| `running` | `运行中` | 正常色（ok 点） | 无 |
| `stopped` | `已停止` | 中性色（空心点） | 无 |
| `conflict` | `冲突` + `端口 <port>` | 红字 | **无**（重试也没用，得人去腾端口） |
| `needs_human` | `需人工处理（已停止自愈）` | 红字 | 有，调 `pool start` 指定该 mapping |

- 缺 `state` 的记录按 `已停止` 呈现，行内不留空白。
- 冲突端口优先读后端 `conflict_port`；缺省才回落 `listen` 里的端口（仍是后端字段，不扫端口）。

### 探活 RTT

- 行内直接显示真实毫秒数（`12ms` / `37ms`），**不写「正常」**。
- 点「探活」调 `pool probe`，用返回的 `rtt_ms` 覆盖行内读数；失败显示 `探活失败`（红），
  失败原因放在 `title` 里。

### 四个动作

`启动` / `停止` / `探活` / `编辑配置`：

- `启动` → `pool start`（只纳 stopped / needs_human，冲突行不纳入）；`停止` → `pool stop`；
  `探活` → `pool probe`（enabled 的 mapping）。
- `编辑配置` 展开卡内编辑区，逐条改上游 host / 端口；保存走 `pool set_upstream`，
  **不自动重启**，成功后必须显示「配置已保存，需手动启动生效」。
- 未启动时「启动」可用、「停止」禁用；所有动作在后端未接入（404）时如实提示
  「后端未接入代理池动作接口（HTTP 404），本次未执行任何动作」，不假装成功、不卡加载中。
- `needs_human` 行内的「重试」同样调 `pool start`（后端已有的重启动作），并刷新 supervisor 状态。

## 对接契约

按 issue 字段形状消费（`GET /api/supervisor/state`）：

```json
{
  "module": "proxypool",
  "origin": "spawned",
  "mappings": [
    {
      "id": "human-chat",
      "listen": "127.0.0.1:8788",
      "upstream_host": "api.openai.com",
      "upstream_port": 443,
      "enabled": true,
      "owner": "human",
      "state": "running",
      "rtt_ms": 12
    }
  ]
}
```

动作接口（QUOT-490 / QUOT-506 落地前为 404）：

| 动作 | 端点 | 请求体 |
| --- | --- | --- |
| 启动 | `POST /api/supervisor/pool/start` | `{ mapping_ids?: string[] }`（缺省整池） |
| 停止 | `POST /api/supervisor/pool/stop` | `{ mapping_ids?: string[] }` |
| 探活 | `POST /api/supervisor/pool/probe` | `{ mapping_ids?: string[] }` → `{ ok, results: [{ mapping_id, ok, rtt_ms, error }] }` |
| 改上游 | `PUT /api/supervisor/pool/upstream` | `{ mapping_id, upstream_host, upstream_port }` |

实现约定（后端改名时只需改一处）：

- 模块键先找 `proxypool`，找不到再找当前 482 系列的 `proxyfleet`（同一产品，避免改名期卡片整块消失）。
- 每条 mapping 的状态按 issue 的描述放在 `mappings[].state`；若后端最终改为模块级
  `state: { [mappingId]: ... }`，只需改 `src/lib/proxyPoolView.ts::mappingState`。
- 前端不扫端口、不推断归属、不自动改端口。

## 判过证据

### 1. 截图（浅色 + 深色，覆盖未启动 / 运行中 / 冲突）

`frontend/scripts/quot-507-mock-api.mjs` 按上述契约回放四种场景，前端仍走真实
`fetch('/api/supervisor/state')`；截图用 `frontend/scripts/visual-shot.mjs`（无头 Chrome，
1440×1500，`--scroll 700`）。

| 场景 | 浅色 | 深色 |
| --- | --- | --- |
| 未启动态（全部 mapping = stopped，启动可用 / 停止禁用） | `docs/screenshots/quot-507/settings-507-stopped-light.png` | `...-stopped-dark.png` |
| 运行中态（人工两条运行中 + AI 折叠） | `...-running-light.png` | `...-running-dark.png` |
| 冲突态（端口 8790 红字、无重试；needs_human 有重试） | `...-conflict-light.png` | `...-conflict-dark.png` |
| 探活后真实 RTT（37ms / 24ms + 「探活完成」提示） | `...-probe-light.png` | `...-probe-dark.png` |

### 2. AI 归属折叠：展开前 / 展开后

| 状态 | 浅色 | 深色 |
| --- | --- | --- |
| 展开前（一行摘要 `3 条映射 · 2 条运行中`） | `...-running-light.png` | `...-running-dark.png` |
| 展开后（三条 AI 明细 + 各状态 / RTT） | `...-ai-expanded-light.png` | `...-ai-expanded-dark.png` |

### 3. 重启不自动起

`SCENARIO=stopped` 的快照里 proxypool 全部 mapping 为 `stopped`，卡片逐行显示「已停止」，
「启动」可用、「停止」禁用（见上面 stopped 两张截图）。

接口 JSON 取证：`docs/tasks/QUOT-507/supervisor-state.stopped.json`
（由 `SCENARIO=stopped node scripts/quot-507-mock-api.mjs --dump` 生成）。

### 4. 探活 RTT

`settings-507-probe-light.png` / `-dark.png`：探活前 rtt 为空，点击「探活」后行内显示
`37ms` / `24ms`，并出现「探活完成：5 条成功」提示。

### 5. 构建与单测

```text
$ npx tsc --noEmit
（无输出）

$ npx vitest run tests/quot-507-proxy-pool.test.tsx
Test Files  1 passed (1)
     Tests  7 passed (7)

$ npx vitest run
Test Files  89 passed (89)
     Tests  884 passed (884)

$ npm run build
✓ built in 10.84s
（仅既有 >500 kB chunk 体积 warning，与本次改动无关）
```

### 6. PR

base `main`，标题带 `QUOT-507`，单 PR 只含本卡片相关文件。

## 主要改动文件

- `frontend/src/api.ts`：`ProxyMapping` / `ProxyProbeResponse` 类型 + 四个动作端点 + `SupervisorModule.mappings`
- `frontend/src/lib/proxyPoolView.ts`：状态 / 归属 / 折叠 / RTT / 冲突端口的纯函数口径
- `frontend/src/components/supervisor/ProxyPoolCard.tsx`：卡片 UI 与动作
- `frontend/src/screens/SettingsScreen.tsx`：在子模块列表挂载卡片（仅增量）
- `frontend/tests/quot-507-proxy-pool.test.tsx`：7 条行为测试
- `frontend/scripts/quot-507-mock-api.mjs`：截图取证替身 API

## 未覆盖 / 后续

- 后端 `pool start|stop|probe|set_upstream` 与 `mappings` 尚未落地：真实环境卡片显示
  「后端未提供映射清单（mappings 缺失）」，动作为 404 时如实提示，不会假装成功。
- `mappings[].state` 的字段位置按 issue 文字实现；若 QUOT-506 最终改成模块级 map，
  仅需调整 `proxyPoolView.ts` 的 `mappingState`。
- 不做托盘 / 关闭后最小化（派单明确不做）。
