# DEC-037 盯盘阈值持久化：新增 `watch_settings` 分片，多窗口靠 BroadcastChannel 对齐

- 日期：2026-11-01
- 提出：前端架构师（QUOT-291）
- 状态：生效（随 QUOT-291 实现落地）
- 编号：`DEC-037`（总经理预占；`docs/decisions/` 现为 030–037 连续，不复用 036）
- 关联：`docs/UI-SPEC.md` §3.2 / §12（原「阈值持久化」遗留项）、`config/state.yaml`、
  `frontend/stores/manifest.json`、`tests/unit/test_state_contract.py`、
  DEC-001（前端持久化与 Python 进程边界）、DEC-035 §2.6（阈值类判定口径不在前端编造）

## 1. 背景

盯盘页的涨幅阈值、冷却时间、封板/开板开关原本只存在 React 内存里：
刷新即回 3.0%，卡片上明写「本次会话有效」（旧 `WatchScreen.tsx:466`）。UI-SPEC §3.2 把它记为
遗留决策点，§12 第 1 项要求「新增 `watch_settings` 分片，由前端架构决策后另开任务」。

阈值是**用户偏好**，不是行情快照：它没有「新不新鲜」的问题，恰恰相反——用户改了阈值就希望
下次打开还是这个值。这与 `watch_snapshot` 的 TTL 信任模型方向相反，不能共用一个分片。

## 2. 决定

### 2.1 新增 `watch_settings` 分片，`kind: settings`，无 TTL

三处同步（契约测试 `test_state_contract.py` 是唯一护栏）：

| 落点 | 内容 |
|---|---|
| `config/state.yaml` | `- name: watch_settings` + `persist: true`，**不写 `ttl_sec`** |
| `frontend/stores/manifest.json` | `id: watchSettings`、`kind: settings`、`recoverable: true` |
| `frontend/stores/registry.ts` | `StoreKind` 增加 `'settings'`（索引上报只对 `snapshot` 算 needs_refetch） |

- **schema_version 仍是 2**：这是新 key，不存在 v1 数据，`migrate.ts` 的 `MIGRATIONS` 不需要
  为它写 v1→v2；把版本号抬到 3 会让另外三个分片凭空多一步迁移，没有收益。
  对应的护栏 `test_every_persisted_shard_has_a_v1_upgrade_step` 显式豁免 `watchSettings`
  （`POST_V2_STORES`），同时断言豁免项必须仍在 manifest 里，避免名单失真。
- **不新增后端接口**：分片仍走既有 state 读写通道（IndexedDB + `/__state/index` 上报），
  Python 侧只多看到一行 `config-only` / `index` 状态。

### 2.2 越界值在三条入口都被拒绝

持久化最大的风险是「上次写进去的」绕过本次校验。因此校验不只在按钮上：

| 入口 | 行为 |
|---|---|
| `setThresholdPct` / `setCooldownSec`（用户输入） | 越界返回 `false`、不 `set`、不写盘；界面红边 + 行内提示，分片保留上一个合法值 |
| `rehydrate`（刷新 / 重启） | `createPersistedStore` 新增可选 `sanitize`，`merge` 前严格解析；任一字段越界/缺失 → **整片回到初始态** |
| BroadcastChannel 消息（多窗口） | 同一 `parseWatchSettings` 严格解析，非法消息整条丢弃 |

区间沿用既有口径：涨幅 `1.0–10.0`（步进 0.5 由输入框 `step` 保证，存储层不做步进取整）、
冷却 `0–600` 秒。`NaN` / `Infinity` / 字符串数字一律视为非法。

为什么恢复时「整片拒绝」而不是逐字段回落默认：一个越界的阈值说明分片已经不可信，
把同片的冷却时间也一起放弃，比拼出一个「一半新一半旧」的配置更容易解释。

### 2.3 多窗口一致：BroadcastChannel，不是轮询、不是 storage 事件

IndexedDB **不产生** `storage` 事件（那是 localStorage 的机制），所以跨窗口实时同步必须自己接线：

- 频道名 `ldst-watch-settings`，消息 `{ type: 'watch_settings', values, updatedAt }`；
- 本窗口写入 → 订阅器发现三个字段有变化 → `postMessage`；**值相同不广播**（比如重复保存同一阈值）；
- 收到远端消息 → 严格校验 → 与当前值相同则忽略，不同才 `setState`；
- `applyingRemote` 标志保证「应用远端设置」不会再被订阅器广播回去，避免两个窗口回声；
- 宿主没有 `BroadcastChannel`（老 WebView）时降级为「打开时读同一份」——设置照样落盘，
  只是不实时联动；构造失败不抛错、不阻塞看板启动。

### 2.4 输入框保留草稿文本，失焦回到已保存值

受控输入不能直接绑定分片值：用户输入 `12` 时如果立刻用分片里的 `4.5` 覆盖，输入框会「跳回去」，
错误提示也来不及出现。因此界面维护 `thresholdDraft` / `cooldownDraft`：

- `onChange`：先记草稿；文本非空且通过区间校验才写分片（否则只显示错误）；
- `onBlur`：清掉草稿，回到分片值；
- 信号流级别列始终用**分片里的**阈值计算，不跟着非法草稿跳。

### 2.5 卡片文案

- 删掉 `Badge「本次会话有效」`；
- 副标题改为「改动即时保存到本机 · 刷新 / 重启 / 多窗口共用」；
- 底部说明改为「设置保存在本机 IndexedDB（watch_settings）……越界输入不会被保存」。
  不再出现「阈值不落盘」「另行决策」这类过期提示。

## 3. 权衡与不做的

- **不做跨设备同步**：本产品是单机看板（DEC-001），多窗口一致不等于多机器一致；
  把偏好传到服务端会新增写接口与鉴权面，超出本轮范围。
- **不把设置塞进 `watch_snapshot`**：快照有 300s TTL 与交易日失效规则，偏好跟着过期就完全错了。
- **不加防抖**：数字输入每次合法变更就落一次 IndexedDB，单键写入代价可忽略；
  加防抖反而会制造「改了但没存」的窗口。
- **不做步进强制**：`3.2` 这类非 0.5 倍数在区间内仍被接受——旧的会话内实现也只校验区间，
  收紧步进等于顺手改行为，不在本任务范围。
- **不动 `journal_draft`**：它仍只是配置预留，本轮不实现、不迁移。

## 4. 验收

- `frontend/tests/watch-settings-store.test.ts`：区间边界（1/10、0/600）、越界拒绝且分片保持旧值、
  rehydrate 恢复三字段、落库越界值整片拒绝。
- `frontend/tests/watch-settings-screen.test.tsx`：界面写入 → 分片 `version=2` 且值正确 →
  清内存后从分片 rehydrate → 新渲染读回 `4.5`；越界输入红边 + 行内提示 + 不落盘 + 失焦回填；
  远端写入后本屏立即显示；卡片不再出现「本次会话有效」「阈值不落盘」。
- `frontend/tests/watch-settings-sync.test.ts`：合法改动广播一次；越界不广播；远端非法整条丢弃；
  同值忽略；应用远端后不回推。
- `tests/unit/test_state_contract.py`：`IMPLEMENTED_STORES` 扩到四个分片，配置 / 清单 / 迁移版本三处一致。
- 门禁：`tsc --noEmit`、`vitest run`、`vite build`、`pytest tests/unit/test_state_contract.py` 全绿。
