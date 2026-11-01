# QUOT-315 · §11 逐条走查记录（原四屏 + 已定稿屏）

- 走查人：官网开发（agent `d9ffd7b4`）
- 基线：`origin/main` = `44dcb7b`（QUOT-311 经 PR #41 合入后）
- 判定口径：`docs/UI-SPEC.md` §11 v1.1，三态 **符合 / 不符合 / 不适用**；静态读码不能判定的另记 **需运行时确认**
- 失败条目写法：`屏 · 条目 · 文件:行号 · 期望`
- 本轮只改原四屏：`frontend/src/screens/{ReviewScreen,WatchScreen,ValidateScreen,JournalScreen}.tsx`
- 其余屏（313 / 312 / 311 / 320）**只记录，未改动一个字节**

---

## 0. 结论摘要

| 范围 | 走查条目 | 符合 | 不符合 | 不适用 | 需运行时确认 |
|---|---|---|---|---|---|
| §11.0 全局 × 原四屏 | 52 | 45 | 0 | 3 | 4 |
| §11.1 复盘 | 6 | 6 | 0 | 0 | 0 |
| §11.2 盯盘 | 5 | 5 | 0 | 0 | 0 |
| §11.3 验证 | 4 | 3 | 1 | 0 | 0 |
| §11.4 日志 | 3 | 1 | 2 | 0 | 0 |
| **原四屏小计** | **70** | **60** | **3** | **3** | **4** |
| §11.8–11.11 雷达四屏（312） | 21 | 9 | 12 | 0 | 0 |
| §11.12/11.13/11.15 产业+涨停（311） | 16 | 14 | 2 | 0 | 0 |
| §11.14/11.16/11.17 信号+研究（313） | 18 | 12 | 6 | 0 | 0 |
| §11.0 全局 × 上述 10 屏（311/312/313） | 130 | 96 | 24 | 2 | 8 |
| 320 共享层专项（§5.5/5.7、C-09、C-14/17、token 管线） | 8 | 5 | 3 | 0 | 0 |

原四屏 3 条不符合里，**2 条是接口契约缺口**（验证屏确认时间、退出码 8 数据集名）+ **1 条是共享件缺口**（日志屏 `PrivacyNotice`），屏侧无法自修，见 §3 清单。

---

## 1. 原四屏逐条

### 1.1 §11.0 全局 13 条（每屏都要过）

| # | 条目（逐字，短） | 复盘 | 盯盘 | 验证 | 日志 | 证据 |
|---|---|---|---|---|---|---|
| 1 | 1024 与 1440 下 `main` 无横向滚动条 | 需运行时确认 | 需运行时确认 | 需运行时确认 | 需运行时确认 | 本轮截图固定 1440×900；验证屏 960px 网格在 `ValidateScreen.tsx:586` 的 `overflow-auto` 容器内滚，不牵动 `main` |
| 2 | 页标题 `text-page` / 卡标题 `text-subtitle` / 页脚 `text-caption` | 符合 | 符合 | 符合 | 符合 | 统一由 `components/PageShell.tsx:28` + `ui/Card.tsx:43` 提供，四屏不覆写标题层级 |
| 3 | 全页无 hex / rgba 字面量 | 符合 | 符合 | 符合 | 符合 | 四屏 grep `#[0-9a-fA-F]{3,8}` / `rgba(` 零命中；颜色只走语义类 |
| 4 | 无泄漏档（`gap-1.5`/`px-1.5`/`py-0.5`/`gap-0.5`/`mt-0.5`）与任意值 `[...]` | 符合（1 处 §3.1 已登记例外） | 符合 | 符合（2 处 §3.3 已登记例外） | 符合 | 本轮清掉的：`ReviewScreen.tsx` `mb-1.5→mb-2`、`gap-1.5→gap-2`×2、`max-w-[720px]→max-w-measure`；`JournalScreen.tsx` `gap-1.5→gap-2`、`max-w-[720px]→max-w-measure`；`WatchScreen.tsx` `w-[3px]→w-status-bar`。保留的例外见 §4.1 |
| 5 | 间距与圆角只用 §7.3 刻度；圆角只 `rounded`/`rounded-xs`/`rounded-pill` | 符合 | 符合 | 符合 | 符合 | 四屏圆角只出现三档，无 `rounded-md/lg/xl/full`；列宽全部落在 `{56,88,112,144,176,208}`（见 §1.2 各屏） |
| 6 | 数字全部 `.ldst-num`；缺失全部「—」，无 `0`/`0.00`/`?`/假定默认值 | 符合 | 符合 | 符合 | 符合 | 本轮修的：`WatchScreen.tsx:404,409,555,558`（取数时间 / 剩余 TTL / 快照来源 / 页脚日期）、`JournalScreen.tsx:205`（数值列）、`ReviewScreen.tsx:640,768`（字数 / 段序号）、`ValidateScreen.tsx:615,992,1004`（行序号 / 目标 / 实际值）。`WatchScreen.tsx:410` 原样 `${n}s` 在无快照时渲染成 `-5s`（假定值冒充），改为 `remainingSec === null ? MISSING : \`${formatInt(remainingSec)}s\`` |
| 7 | 后端 key 不直显；状态标签与 §5.1/§5.2 逐字一致 | 符合 | 符合 | 符合 | 符合 | 盯盘原 `StatusDot` 自写「需重新取数」，现 `WatchScreen.tsx:385-393` 改 `<SourceBadges envelope>` / `<FreshnessBadge>`+`<SourceStatusBadge>`，标签真源 `ui/SourceBadges.tsx:22-33` |
| 8 | `?demo=1` 有「演示数据」徽标；`?state=` 有 `DemoStateBadge` | 符合 | 符合 | 符合 | 符合 | 四屏各 1 处 `DemoStateBadge`（`ReviewScreen.tsx:605` 等），组件自读 `useDemoEnabled`（`PageShell.tsx:57-75`），不存在「数据走演示源但水印不亮」的分裂 |
| 9 | 交互过渡只有 `duration-fast`；无 >160ms hover/press/展开 | 符合 | 符合 | 符合 | 符合 | 四屏唯一新增过渡是 `ValidateScreen.tsx:606` 行 hover `transition-colors duration-fast ease-ldst`；其余过渡由 kit 提供（`ui/Button.tsx`、`ui/Field.tsx`、`ui/Table.tsx:249`），屏内无 `duration-base` |
| 10 | 表体只有一处（C-09）；本屏无裸 `<table>` | 符合 | 符合 | 符合 | 符合 | 全仓唯一 `<table>` 在 `ui/Table.tsx:318`。验证屏条件行是 §3.3 登记的 grid（非 table 元素），同屏对账明细走 C-09（`ValidateScreen.tsx:948`） |
| 11 | 单行截断处均有 `title` 或 Tooltip；`min-w-0` 与 `truncate` 成对 | 符合 | 符合 | 符合 | 符合 | 本轮修的：`WatchScreen.tsx:141,148` 名称/状态列改 `truncate: true`（C-09 自动补 `title`，见 `ui/Table.tsx:105-110`）、`ValidateScreen.tsx:618` code 加 `min-w-0 truncate`+`title`、`ReviewScreen.tsx` 模型 span 加 `min-w-0 truncate`+`title` |
| 12 | 键盘可达：可点行/可点卡能 Tab 到、Enter 触发 | 不适用 | 不适用 | 符合 | 不适用 | 复盘/盯盘/日志三屏无可点行（无 `onRowClick`）；验证屏行展开是真 `<button>`（`ValidateScreen.tsx:649` 附近），Tab+Enter 原生可达。C-09 可点行的键盘缺口属共享件，见 §3 跨屏清单 X-1 |
| 13 | 区块四态齐全，骨架与真实内容形状同构（列数一致） | 符合 | 符合 | 符合 | 符合 | `ScreenSkeleton metrics=` 与真实卡数一致：复盘 3（`ReviewScreen.tsx:523` vs `:700` 三卡）、盯盘 4（`WatchScreen.tsx:342` vs 快照状态 4 项 `dd`）、验证 3（`ValidateScreen.tsx:775`）、日志 4（`JournalScreen.tsx:374`）；像素级同构需运行时目测 |

### 1.2 §11.1 复盘 `/review`（6 条）

| # | 条目 | 判定 | 证据 / 本轮改动 |
|---|---|---|---|
| 1 | 指标卡三列等宽，`<1024` 单列；数值右上角有溯源徽标，且来自统一实现 | **符合** | `ReviewScreen.tsx:700` `grid grid-cols-1 gap-4 desk:grid-cols-3`；三卡 `badge={<SourceBadges envelope={sentiment.data} />}`（`:703,713,723`）——统一实现 `ui/SourceBadges.tsx`，屏内无本地标签映射 |
| 2 | `source_status=partial` 显示 `danger`、「部分缺失」；`degraded` 显示 `warn`、「已降级」（规范注：当前 `ReviewScreen.tsx:198` 相反） | **符合** | 屏内 grep `已降级|部分缺失|source_status` 零命中——本地反向映射已不存在，tone 真源 `ui/SourceBadges.tsx:53-57`（`ok/warn/danger` 顺序与 §5.2 一致）。**注：规范给的行号 `:198` 已过期**，当前该文件 878 行结构里 `:198` 是别的内容 |
| 3 | 五段顺序 情绪→资金→题材→龙虎榜→龙头；`desk:` 下每段内左右 40/60，`rail:` 下上下堆叠且 AI 在上 | **符合** | 顺序真源 `frontend/stores/reviewDraft.ts:10-16`（屏只 `REVIEW_SECTIONS.map`，`ReviewScreen.tsx:754`）；40/60 = `ReviewScreen.tsx:797` `grid gap-4 desk:grid-cols-[2fr_3fr]`（§3.1 钦定值，见 §4.1 冲突）；`<1280` 单列且「AI 叙述」块在前 |
| 4 | 三张指标卡 `rail:` 下单列、`desk:` 下等宽三列（规范注 `ReviewScreen.tsx:666`） | **符合** | 同 #1；`grid-cols-1` 是基线，`rail:`(≤1279) 天然单列。**注：规范给的 `:666` 已过期** |
| 5 | 刷新后草稿恢复出现 §4.1 提示条；`truncated` 类错误不出现「重试」主按钮 | **符合**（本轮修） | 提示条 `ReviewScreen.tsx:630-645`：`已恢复 ${formatDraftTime(updatedAt)} 的草稿` + `${labels.join(' / ')} ${n} 段，共 ${千分位} 字` + `[查看改动] [清空草稿]`；时间口径新增 `formatDraftTime`（当日 `HH:mm`、跨日 `MM-DD HH:mm`），字数新增 `formatCharCount`（`toLocaleString('zh-CN')`）。主按钮 `ReviewScreen.tsx` 现按 `viewFailures.some(isRetryableFailure)` 决定「重试失败段 / 重新生成」；段内重试按钮 gate 在 `!failure || isRetryableFailure(failure.kind)` |
| 6 | 指标卡无可编辑入口；AI 区不出现指标样式的数值 | **符合** | `MetricCard` 只读组件；屏内 `<input>`/`<textarea>` 只有五段草稿编辑器（`:761,881`）。AI 区数值是 `text-body` 叙述，卡阵区另有说明「指标由程序直出，不可编辑；缺失显示 — 并注明原因」（`:899`） |

### 1.3 §11.2 盯盘 `/watch`（5 条）

| # | 条目 | 判定 | 证据 / 本轮改动 |
|---|---|---|---|
| 1 | `needsRefetch` 为真时表格零旧值，且 §4.2 四类原因文案逐条对得上 | **符合**（本轮修） | 零旧值 `WatchScreen.tsx:226` `const trustedRows = freshness.needsRefetch ? [] : storeRows`；四类文案新增 `refetchCopyOf()`（`:78-96`）逐字对齐 §4.2：TTL→「快照已超过 TTL（8 分 12 秒），旧值已隐藏」+「立即取数」；交易日变更→「上次快照属于 {date}，与今日不符，需重新取数」；时钟异常→「本机时间比快照时间早 {时长}，请检查系统时间」+「重新校准后取数」；无快照→「还没有可信快照」。时长走新增 `formatDurationSec`，不再写 `492s` |
| 2 | 剩余 TTL、取数时间、快照来源三项均 `.ldst-num` + 「—」缺省规则 | **符合**（本轮修） | `:400`（交易日）、`:404`（取数时间）、`:409-410`（剩余 TTL，null→`MISSING`）、`:555`（快照来源）、`:557-558`（最近扫描 / 本机日期）全部套 `.ldst-num` |
| 3 | 阈值控件非法输入：红边 + 行内原因 + 不保存；合法步进 0.5、区间 1.0–10.0 | **符合** | `WatchScreen.tsx:486` 行内原因 `阈值需在 1.0–10.0 之间，步进 0.5`（原写 `1–10`，与 `THRESHOLD_MIN/MAX` 的 `1.0/10.0` 一位小数口径不一致）+ `:484` hint；`invalid={pctValid ? undefined : true}` 给红边；`setThresholdPct` 返回 false 时不落盘 |
| 4 | 空流仍显示「最近一次扫描 HH:mm:ss，暂无信号」 | **符合**（本轮修） | 新增 `lastScanLabel`，注入 `offline` 与默认 `emptyHint` 描述（`:374`）；无取数时间时该位显示 `—` 而不是整句消失 |
| 5 | 可信状态徽标与 §5.1 同源（同一标签、同一 ●/◐/○），不再是 `StatusDot` 自写文案 | **符合**（本轮修） | 删除 `StatusDot` 引用，快照状态 `dd` 改 `<SourceBadges envelope={envelope} />`，无信封时 `<FreshnessBadge freshness={...} asOf={...} sourceDetail={...} />`（`:385-393`） |

### 1.4 §11.3 验证 `/validate`（4 条）

| # | 条目 | 判定 | 证据 / 本轮改动 |
|---|---|---|---|
| 1 | 「明日条件」与「收盘对账」两模切换时 L0 文案变化，草稿不丢 | **符合** | 模态文案分支 `ValidateScreen.tsx:734-737`；草稿在 `useValidationDraft`（zustand→IndexedDB）与 mode 解耦，切模不重置 |
| 2 | 15:00 前对账按钮禁用且原因可见；**确认后显示确认时间**；未确认行左侧黄条 | **不符合**（子条款 2） | 禁用+原因 符合（`:941-943` `disabledReason={'15:00 后可对账'}`、`:892`）；黄条 符合（`:611` `w-status-bar` + warn 色）；**确认时间 不符合**：`validationDraft` 的 row 结构只有 `code/trigger/note/confirmed`，无 `confirmedAt` 字段，屏侧无法显示真实时间，`ValidateScreen.tsx:637` 只能挂 `title="后端条件不记录确认状态"`。期望：`stores/validationDraft.ts` 补 `confirmedAt: string \| null`（写入 `new Date().toISOString()`），屏内 `已确认 · HH:mm`。属主：317/数据层，越界不改 |
| 3 | 条件表行高取 `--ldst-row-h*`；hover 有 `bg-surface-3` + `duration-fast`；表头 sticky | **符合**（本轮修） | `ValidateScreen.tsx:606` `min-h-row ... transition-colors duration-fast ease-ldst hover:bg-surface-3`（原无过渡）；`:589` 表头 `sticky top-0 z-sticky ... bg-surface-2`（原不 sticky）；滚动容器 `:586` `maxHeight: 'calc(100vh - 280px)'` = §7.3 唯一具名允许值 |
| 4 | 退出码 8 时黄条列出缺失数据集名，不给结论式断言 | **不符合** | `api/radar.ts` 的 `ValidationReconcile` / `parseReconcile` 不投影数据集名（`exit_code` 只在 radar 侧存在），屏侧只能给「当日没有可对账的条件」这类泛化文案。期望：契约补 `missing_datasets: string[]`，黄条逐条列出。属主：317/接口契约，越界不改 |

### 1.5 §11.4 日志 `/journal`（3 条）

| # | 条目 | 判定 | 证据 / 本轮改动 |
|---|---|---|---|
| 1 | 隐私条常驻、**无网络请求**、无 AI 内容、无分享入口；导出走二次确认并说明文件去向 | **不符合**（子条款「无网络请求」）+ 导出一句**不适用** | 隐私条常驻 符合（`JournalScreen.tsx:432` `Banner tone="info" title="本机私有"`，在所有分支之前）；无 AI 内容 符合（`:594` 卡标题即声明「本屏不出现任何 AI 内容」）；无分享入口 符合（无 `navigator.share`/外链分享）。**「无网络请求」与 §3.4 钦定的 `GET /api/journal/stats` 直接冲突**（`JournalScreen.tsx:216`，`api.ts:574` 注明这是本机私有库唯一 HTTP 出口、回环独占、只回聚合数字）——按 §3.4 保留请求，冲突登记在 §4.2。本屏**不存在**导出 CSV / 删除单条，故「导出走二次确认」子句 不适用（是产品缺口不是回归，见 §3 X-6） |
| 2 | 样本不足显示「样本不足」而不是 `0%`（少于 5 笔） | **符合** | `JournalScreen.tsx:396,402` 按 `MIN_SAMPLE` 判定，`:468` Badge 带 `title` 说明口径，`:605` 卡内写明「闭环样本少于 5 笔一律显示「样本不足」；无样本的胜率/盈亏比显示「—」，不拿 0 冒充」 |
| 3 | 与持仓共用同一 `PrivacyNotice` 实现（R-25 修复后本条为真） | **不符合** | 共享层**没有** `PrivacyNotice`：唯一定义是屏内私有的 `screens/radar/PositionsScreen.tsx:84-101`（`role="note"` + accent 左边条），日志屏另写一套 `Banner tone="info"`。提取共享件需新建 `components/ui/PrivacyNotice.tsx`，越出本轮边界（`components/ui/**` 禁改）。属主：320 共享层，见 §3 X-3 |

---

## 2. 原四屏本轮改动清单（diff 可核对）

| 屏 | 改动 | 对应条目 |
|---|---|---|
| 盯盘 | 列宽 `96/100/120/104/84/84 px` → `88/88/112/112/88/88 px`；`w-[3px]`→`w-status-bar`；表高 `520px`→`420px` | §11.0 #5 |
| 盯盘 | `StatusDot` 自写文案 → `SourceBadges`/`FreshnessBadge`/`SourceStatusBadge` | §11.2 #5、§11.0 #7 |
| 盯盘 | 新增 `formatDurationSec` + `refetchCopyOf`，四类原因文案逐字对齐 | §11.2 #1 |
| 盯盘 | `trustedRows` 零旧值 gate；banner gate 从 `storeRows.length>0` 改 `freshness.needsRefetch && state!=='partial'` | §11.2 #1 |
| 盯盘 | 剩余 TTL `-5s` 假定值 → `—`；页脚三项套 `.ldst-num` | §11.2 #2、§11.0 #6 |
| 盯盘 | 空态描述注入「最近一次扫描 HH:mm:ss，暂无信号」 | §11.2 #4 |
| 盯盘 | 阈值行内原因 `1–10` → `1.0–10.0 之间，步进 0.5` | §11.2 #3 |
| 盯盘 | 名称/状态列 `truncate:true`（C-09 自动 `title`），缺失走 `MISSING` | §11.0 #11 |
| 复盘 | `mb-1.5`→`mb-2`、`gap-1.5`→`gap-2`×2、`max-w-[720px]`→`max-w-measure` | §11.0 #4/#5 |
| 复盘 | 新增 `formatDraftTime`/`formatCharCount`；§4.1 提示条补齐时间/段名/千分位字数与两个动作 | §11.1 #5 |
| 复盘 | 新增 `isRetryableFailure` gate：`truncated`/`internal` 不出「重试」主按钮与段内重试 | §11.1 #5 |
| 复盘 | 模型 span 加 `min-w-0 truncate` + `title` | §11.0 #11 |
| 验证 | `ROW_GRID` 40px→56px、104px→88px；对账表列宽 `6rem/7rem`→`88px/112px` | §11.0 #5 |
| 验证 | 表头 `sticky top-0 z-sticky`；行 `min-h-row` + `duration-fast` + `hover:bg-surface-3`；容器 `calc(100vh - 280px)` | §11.3 #3 |
| 验证 | 展开编辑器 `min-[1100px]:grid-cols-[1fr_1fr_2fr]` → `desk:grid-cols-12` + `col-span-3/3/6`。**这条不只是风格**：`min-[1100px]` 在生产产物里没有对应 `@media`（见 X-16），三列布局此前从未生效；换成 `desk:` 后 ≥1280 才真正分列 | §11.0 #4、X-16 |
| 验证 | code 列 `min-w-0 truncate`+`title`；trigger/note 列补 `min-w-0` | §11.0 #11 |
| 日志 | `max-w-[720px]`→`max-w-measure`、`gap-1.5`→`gap-2`、数值列 `168px`→`176px`、表高 `480px`→`420px` | §11.0 #4/#5/#6 |

---

## 3. 跨屏问题清单（只记录，未改；交对应轨）

编号 X-n 建议交 317（走查归口）分派；标注属主。

| # | 问题 | 证据 | 期望 | 建议属主 |
|---|---|---|---|---|
| X-1 | **C-09 可点行键盘不可达**：`onRowClick` 只给 `<tr onClick>` + `cursor-pointer`，无 `tabIndex`/`role`/`onKeyDown` | `components/ui/Table.tsx:247-256`；受影响消费方 `signals/SignalsScreen.tsx:428`、`radar/PoolScreen.tsx:456`、`industry/IndustryMatrixScreen.tsx:479` | 可点行 `tabIndex={0}` + Enter/Space 触发；这是 §11.0 #12 在 4 张屏上的同一根因 | 320 |
| X-2 | **§5.7「新鲜度未知」缺省徽标不存在**：`SourceBadges` 在 `envelope == null` 时 `return null`，正是规范禁止的「静默不渲染」 | `components/ui/SourceBadges.tsx:175-176`；fallback 文案是 `未知`/`来源状态未知`（`:133,:158`），全仓无 `新鲜度未知` 字符串 | 无信封时渲染 neutral `○ 新鲜度未知` + tooltip「响应未携带 freshness」 | 320 |
| X-3 | **`PrivacyNotice` 无共享实现**：唯一一份是屏内私有；日志屏另写 `Banner tone="info"` | `screens/radar/PositionsScreen.tsx:84-101`（唯一消费者 `:710`）、`screens/JournalScreen.tsx:432` | 提取 `components/ui/PrivacyNotice.tsx`（`role="note"`），§11.4 #3 / §11.11 #1 同时转真 | 320 |
| X-4 | **`Badge` 把中文标签套进 `.ldst-num`**：§7.2 的等宽数字类作用到「实时/数据完整」等中文标签上 | `components/ui/Badge.tsx:39` | 数字类只套数值，不套标签 | 320 |
| X-5 | **`formatTime`/`formatDate` 遇不可解析字符串原样回吐**，与 §8「不泄露后端裸值」相悖 | `lib/format.ts:104,129` | 不可解析 → `MISSING` | 320 |
| X-6 | **日志屏无导出 CSV / 删除单条**（§3.4 的 L3 能力未落地），§11.4 #1 后半句无从判定 | `screens/JournalScreen.tsx` 全文无导出/删除入口 | 若确认要做，需带二次确认 + 「只写本机」去向说明 | 产品/317 裁定 |
| X-7 | **涨停屏信封不上屏**：`payloadOf` 只取 `root.payload`，`parseLimitPool` 不读 `freshness`/`source_status`，屏内只有 payload 自带的 `data_source_status`，无 `FreshnessBadge` | `api/limitPool.ts:89-93,111-127`；`screens/limitup/LimitUpScreen.tsx:158-163` | 解析层保留信封两字段并上屏（§11.15 #3） | 311 |
| X-8 | **信号屏数据层无溯源字段**：`SignalsPayload` 没有 `freshness`/`source_status`/`source_detail`，屏侧无从渲染徽标；`passed: record.passed !== false` 把「字段缺失」判成「成立」 | `api/industry.ts:336-350,836,866-889`；`screens/signals/SignalsScreen.tsx:256-272` | 契约补三字段；`passed` 缺失应为 `null` 而非 `true`（§8 不猜缺失值） | 313 |
| X-9 | **研究两屏在 loading/empty/error/offline 四态整块不渲染溯源徽标** | `screens/research/ResearchDetailScreen.tsx:613-618`、`ResearchReportScreen.tsx:210-217` | 与 X-2 一并解决：无信封也要显式「新鲜度未知」 | 313 |
| X-10 | **`/signals` 三张指标卡 `?? 0` 未修**（R-07 只修了 subtitle），`?state=partial` + payload null 时显式渲染 `0` | `screens/signals/SignalsScreen.tsx:363,372-374` | `countOrMissing(payload?.counts.total)`（`lib/format.ts:65-67`） | 313 |
| X-11 | **`/signals` 列宽与表高整片越刻度**：`96/132/92/72/152/48px` + `calc(100vh - 380px)` | `screens/signals/SignalsScreen.tsx:175,190,199,208,215,227,237,427,550`；抽屉 `:84,98,108` | 列宽收 `{56,88,112,144,176,208}`；表高收 `{240,320,360,420}` 或唯一具名 `calc(100vh - 280px)` | 313 |
| X-12 | **研究报告 `retryable===false` 仍出「重试本段」主按钮**，且失败类别直显后端枚举 key | `screens/research/ResearchReportScreen.tsx:408-427`（`facts.ts:225` 已有 `failureKindLabel`，`:487` 已在用） | 不可重试时不传 `onRetry`；`code={failureKindLabel(document.failure.kind)}` | 313 |
| X-13 | **个股深挖两卡缺来源脚注 + 分位空态用裸 `<p>`** | `screens/research/ResearchDetailScreen.tsx:483-513`（`PercentileCard`）、`:770-792`（`SectionCard`）、`:501-504` | 补 `<SourceFoot>`；空态换 C-17 `EmptyState` | 313 |
| X-14 | **雷达四屏缺失值仍用 `?? 0` 冒充**：资讯「共 0 条 · 已抽取事件 0」、日历「0 / 5 个小节有源」 | `screens/radar/NewsScreen.tsx:456-458`、`CalendarScreen.tsx:395-397` | `countOrMissing` / `MISSING`（§8、§11.8 #2、§11.9 #4） | 312 |
| X-15 | **资讯屏直显后端 key**：`新鲜度 ${row.freshness}`、`来源 ${row.source}` | `screens/radar/NewsScreen.tsx:178-179` | 走 §5.1 中文标签映射 | 312 |
| X-16 | **`min-[...]` 自定义断点在生产产物里是死 CSS（不只是风格违规）**。`tailwind.config.ts:40-43` 的 `screens` 含对象档（`rail:{max:'1279px'}`），`npm run build` 报 `warn - The min-* and max-* variants are not supported with a screens configuration containing objects.`；实测 `dist/assets/index-*.css` 里全部 `@media` 只有 3 种：`(max-width: 1279px)`×1、`(min-width: 1280px)`×2、`(prefers-reduced-motion: reduce)`×3 —— `min-width:1024`/`1100`/`640` 出现次数均为 **0**，即这 8 处响应式行为**在线上根本没生效** | `components/valuation/ValuationBasisPanel.tsx:121,253`、`screens/radar/PositionsScreen.tsx:110`、`screens/WeeklyScreen.tsx:367,492,540`、`screens/MarketScreen.tsx:508` | 优先级高于「清零」：这些屏在 1024–1279 区间的实际布局与代码意图不符（永远停在基线单列/两列）。要么把 `screens` 改回可支持 `min-*` 的写法，要么全部换 `rail:`/`desk:`。§11.7 #1、§11.6 #2、§11.16 #4 的判据建议同时改成「产物里存在对应 `@media`」 | 320（配置）+ 各屏属主 |
| X-16b | **其余任意值/魔法尺寸**：`min-h-[76px]`×2、`max-w-[140px]`、`grid-cols-[184px_…_340px]` | `CalendarScreen.tsx:136,154,536`、`NewsScreen.tsx:580`、`PositionsScreen.tsx:341` | 换注册断点与刻度值（§11.8 #1、§11.9 #1、§11.11 #5） | 312 |
| X-17 | **候选池表高魔法值 + 列宽越刻度**：`calc(100vh - 360px)`、`96/148/120/72px` | `screens/radar/PoolScreen.tsx:243,251,277,289,455` | §11.10 #3 | 312 |
| X-18 | **持仓 `HBarList` 绕过 `format.ts`**：`format={(value) => value.toFixed(2)}`；多处硬编码裸 `—` 字符串未引 `MISSING` | `screens/radar/PositionsScreen.tsx:180,206`；`:230,289,302,345` | 传格式化函数；统一引用 `MISSING`（§11.11 #2/#4） | 312 |
| X-19 | **日历小节状态徽标用词与 §5.2 不一致**：「有源（降级）」vs「已降级」；且 §3.9 自己写的是第三种「有数据源（降级）」 | `screens/radar/CalendarScreen.tsx:336,339` | 三处口径统一（规范侧也要改，见 §4.2） | 312 + 规范 |
| X-20 | **产业详情五维卡阵无窄屏单列回退**：`grid grid-cols-2 gap-3` 在 1024 仍两列 | `screens/industry/IndustryDetailScreen.tsx:608` | `grid-cols-1 rail:... desk:grid-cols-2`（§11.13 #2） | 311 |
| X-21 | **产业矩阵 `window` 项截断无 `title`** | `screens/industry/IndustryMatrixScreen.tsx:515-524`（`dd` 恒 `min-w-0 shrink truncate`，`bits.tsx:43`） | 补 `title`（§11.0 #11） | 311 |
| X-22 | **`min-h-topbar` 是空类**：`tailwind.config.ts:125-130` 的 `minHeight` 只登记 control/row/empty/textarea，`topbar` 只在 `height` 下 | `components/ui/Card.tsx:38`、`components/ui/Overlay.tsx:133` | 静默 no-op，需登记或改用 `h-topbar` | 320 |
| X-23 | **`tokens.css` 生成器重复输出 layout/z 块**；`tokens.json` 的 `motion.reducedMotion` 反向指向 `--ldst-duration-base` | `styles/tokens.css:98-121` 与 `:122-145` 同块两份 | 修 gen-tokens；reducedMotion 语义确认 | 320 |
| X-24 | **共享件里的泄漏档与 `StatePanel` 闲置**：`gap-1.5` 在 `ui/Badge.tsx:68`/`ui/Card.tsx:138`/`ui/Tabs.tsx:59`；C-14/17/18/19 的 `StatePanel` 屏侧 0 消费者；`worstSourceTone`（§5.3 banner 取最严重）0 消费者；`ui/Gallery.tsx` 孤儿 | 见证据行号 | 泄漏档清零；`StatePanel`/`worstSourceTone` 要么接上要么从 §6 摘掉 | 320 |
| X-25 | **`ui/Table.tsx` `truncate:true` + JSX 单元格 + 无 `cellTitle` → 省略号但无 `title`** | `components/ui/Table.tsx:105-110`（`typeof content === 'string' \|\| 'number'` 才兜底） | 非字符串内容时要求 `cellTitle`，或取 `children` 文本 | 320 |
| X-26 | **`ValuationBasisPanel` 仍有 `min-[1024px]:grid-cols-3`**，被个股深挖左下 7/5 卡位消费，使 §11.16 #4「整屏判定」不能算通过 | `components/valuation/ValuationBasisPanel.tsx:121,253` | 换 `rail:`/`desk:` | 313 |
| X-27 | **`/research` 落地页不在 §11 的 18 屏清单内**，但已注册路由并进侧栏 | `App.tsx:82`、`Layout.tsx:149` | 补 §11.x 条目或并入 §11.16 | 规范 |

---

## 4. 规范自身的问题（不改正文，提给 S1b 属主）

### 4.1 §11.0 #4「无任意值 `[...]`」与 §3.1/§3.3 钦定值直接冲突

- `ReviewScreen.tsx:797` `desk:grid-cols-[2fr_3fr]` —— §3.1 逐字要求「每段内左右 40/60」，§11.0 #4 又把它算违规。已按 §3.1 保留。
- `ValidateScreen.tsx:173` `ROW_GRID`（含 `3.5rem`/`5.5rem`）与 `:587` `min-w-[960px]` —— §3.3 明确登记为本屏例外（条件行不是 C-09 表体），已按 §3.3 保留并把数值收进刻度（40→56、104→88）。
- 期望：§11.0 #4 补一句「§3.x 逐字登记的例外不计违规，但取值必须落在 §7.3 刻度上」。

### 4.2 三处规范内部矛盾

1. **§11.4 #1「无网络请求」 vs §3.4**：§3.4 钦定 `GET /api/journal/stats` 为本机私有库唯一 HTTP 出口（回环独占、只回聚合数字）。§11.4 的「无网络请求」若按字面执行会砍掉日志屏全部数据。已按 §3.4 保留请求。
2. **§11.12 #2「表高不再是 `calc(100vh - 280px)`」 vs §7.3**：§7.3 恰恰把 `calc(100vh - 280px)` 列为**唯一具名允许值**，而产业矩阵（`:478`）与涨停（`:270`）用的正是它。两条判据相反，需人类裁定。
3. **§5.2「已降级」 vs §3.9「有数据源（降级）」 vs 日历实现「有源（降级）」**：同一状态三种写法（X-19）。

### 4.3 §11 引用的行号大面积过期

`§11.1` 引 `ReviewScreen.tsx:198/666/763`，实际当前文件这三处内容均非所述对象（文件 878 行，指标卡在 `:700`、段内在 `:797`）。`§7.5` 第 4 条登记的 `ResearchReportScreen.tsx:79 transition-[width] duration-base` 代码里已是 `duration-fast`（`:84`）。`§6.3` 说「C-09 无 summaryRow/expandable」，实际 `ui/Table.tsx:45,51-57,367-387` 都已实现。`§3.15:377` 说的裸 `HHMMSS` 已由 `formatSealTime` 修掉。**期望：走查前统一以代码现状为准，规范行号只作定位提示。**

### 4.4 §7.3 未覆盖的两类，建议明确口径（不要默认算违规）

- **图标尺寸 `size-3.5`**：原四屏 + 走查的 10 屏共约 30 处（`RefreshCw`/`Trash2`/`Search` 等 leadingIcon）。`tailwind.config.ts` 的 spacing 覆写只到 0–7 + `half`，图标档未登记；§7.3 的泄漏枚举表只列 `gap/px/py/mt`，未列 `size`。本轮**未改**（改了会破坏与其余屏的一致性），建议：要么在 `tokens.json` 登记 `icon` 档，要么在 §7.3 明确 `size-*` 不属间距刻度。
- **`w-52`/`w-56`/`w-40`/`w-24`/`w-16`/`h-5` 等 Tailwind 默认尺寸档**：§3.14/§3.17 自己写着 `Select w-56`、`desk:w-[208px]`，而刻度表只给列宽。同族混用（`w-52` = 208px 但非具名档）。另注意 `tailwind.config.ts:80-82` 把数值 spacing 0–7 覆写成 token，导致 `h-5`→24px、`pl-7`→48px、`h-6/h-7`→32/48px —— **同一类名在组件里的实际像素与 Tailwind 默认不同**（`ui/Field.tsx:132,152` 注释写「36×20 轨道」而 `h-5 w-10` 解析成 24/48px），这类"注释与解析值不符"建议一并清理。

---

## 5. 四条既有硬约束复核（未破坏）

| 约束 | 状态 | 证据 |
|---|---|---|
| 日志：隐私边界，`journal*` 不经 HTTP 外发，唯一出口是回环只读聚合 | **未破坏** | `api.ts:574`「`GET /api/journal/stats` 是本机私有库的唯一 HTTP 出口，扁平 JSON、回环独占」；`JournalScreen.tsx:216` 仍是本屏唯一网络调用，本轮未新增任何请求；离线回放只读本机 IndexedDB（`KV_STORE` + `CACHE_KEY`，缓存里只有聚合数字，无自由文本） |
| 验证：清空/覆盖必须走 C-16 二次确认，文案说清丢什么 | **未破坏** | `ValidateScreen.tsx:1051` `ConfirmDialog title="清空本机验证草稿？" confirmLabel="清空草稿"`、`:1061` 第二个确认（覆盖）；`:769`「本屏不做自动合并…谁赢必须你当场定」 |
| 复盘：草稿可恢复；指标卡不可编辑；AI 区不冒充指标 | **未破坏** | 草稿 store `stores/reviewDraft.ts` 未动，§4.1 提示条只是将既有 `restored` 快照渲染得更完整（新增 `labels`/`firstKey` 只读首帧）；`ReviewScreen.tsx:899` 说明「指标由程序直出，不可编辑；缺失显示 — 并注明原因」；三张 `MetricCard` 无输入控件 |
| 盯盘：过期即零旧值，不按过期数据决策 | **未破坏**（并加强） | `WatchScreen.tsx:226` `trustedRows = freshness.needsRefetch ? [] : storeRows`；本轮把 banner 的 gate 从「有行」改成「needsRefetch 且非 partial」，隐藏得更严；`:50` 注释与 `:420` 标题「旧值已隐藏」一致 |

---

## 6. 验证与截图

- `npm run typecheck`（`tsc --noEmit`）：见回执评论尾部原始输出
- `npm test`（vitest）：见回执评论尾部原始输出
- `npm run build`：见回执评论尾部原始输出
- 截图：`docs/screenshots/{review,watch,validate,journal}-{before,after}-315-{dark,light}.png` 与 `...-{before,after}-315-offline-{dark,light}.png`，共 32 张，1440×900，真实 dev server（`vite` @127.0.0.1:5173，本机无 `ldst run`，故为各屏真实的「未同步 / 空 / 降级」态 + `?state=offline` 强制态；未用 `?demo=1`，避免 §5.6 演示源与真源混拍）
