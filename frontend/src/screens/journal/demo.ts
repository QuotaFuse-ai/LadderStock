/**
 * 交易日志（`/journal`）演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页面带「演示数据」徽标）。
 *
 * 形状与 `GET /api/journal/stats` 逐键对齐（`src/api.ts:JournalStatsPayload` ↔
 * `web/server.py:JOURNAL_STATS_FIELDS` 白名单）：这个端点是**扁平 JSON，不是 `LadderEnvelope`**
 * （DEC-015 Tier 1），所以演示 payload 同样没有 `fetched_at` / `freshness` / `data_as_of` 外壳键——
 * 屏侧按 §5.7 出「未知」徽标，演示源也不补一个后端没有的字段。
 *
 * 演示的是**样本**，不是用户自己的成交：数值来自固定夹具，与本机的 `data/journal.sqlite` 无关。
 * 因此本屏在演示态既不读也不写 IndexedDB 缓存，也不提供任何导出入口。
 *
 * **绝不在真实通道里回落到这份数据**：真实请求 404 / 断连时页面进空态 / 离线态。
 */

import type { JournalStatsPayload } from '../../api'

export type JournalDemoVariant = 'full' | 'partial' | 'empty'

/** 演示基准交易日与取数时刻：与 `limitup/demo.ts:15-18` 同值，保证跨屏演示事实不打架。 */
export const DEMO_JOURNAL_FROM = '2026-10-01'
export const DEMO_JOURNAL_TO = '2026-10-30'
export const DEMO_JOURNAL_FETCHED_AT = '2026-10-30T15:00:04+08:00'

/**
 * full：24 笔（14 买 + 10 卖）、10 个闭环（盈 6 / 亏 3 / 平 1）。
 * 闭环盈亏合计 = 6×1240.50 − 3×806.00 = 5025.00，与 `realized_pnl` 同一笔账；
 * `win_rate` = 6/10 → 60.0，`profit_loss_ratio` = 1240.50/806.00 → 1.54（后端同一口径的两位小数）。
 */
const FULL: JournalStatsPayload = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'ok',
  range: { from: DEMO_JOURNAL_FROM, to: DEMO_JOURNAL_TO },
  total_trades: 24,
  buy_count: 14,
  sell_count: 10,
  closed_rounds: 10,
  wins: 6,
  losses: 3,
  flats: 1,
  win_rate: 60,
  avg_win: 1240.5,
  avg_loss: 806,
  profit_loss_ratio: 1.54,
  realized_pnl: 5025,
  unmatched_sell_qty: 200,
  deviation_review: { threshold_pct: 1, count: 3 },
}

/**
 * partial：闭环只有 3 个（盈 2 / 亏 1），少于 `MIN_SAMPLE` 5 —— 用来演示 §11.4
 * 「样本不足显示『样本不足』而不是 0%」。合计同样自洽：2×480.00 − 1×620.00 = 340.00，
 * 胜率 2/3 = 66.7，盈亏比 480/620 = 0.77。
 */
const PARTIAL: JournalStatsPayload = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'ok',
  range: { from: DEMO_JOURNAL_FROM, to: DEMO_JOURNAL_TO },
  total_trades: 8,
  buy_count: 5,
  sell_count: 3,
  closed_rounds: 3,
  wins: 2,
  losses: 1,
  flats: 0,
  win_rate: 66.7,
  avg_win: 480,
  avg_loss: 620,
  profit_loss_ratio: 0.77,
  realized_pnl: 340,
  unmatched_sell_qty: 300,
  deviation_review: { threshold_pct: 1, count: 1 },
}

/** empty：与后端「这一天没有成交样本」同一形状（`data_source_status=missing` + 无样本指标为 `null`）。 */
const EMPTY: JournalStatsPayload = {
  local_only: true,
  store: 'local_sqlite',
  data_source_status: 'missing',
  range: { from: null, to: null },
  total_trades: 0,
  buy_count: 0,
  sell_count: 0,
  closed_rounds: 0,
  wins: 0,
  losses: 0,
  flats: 0,
  win_rate: null,
  avg_win: null,
  avg_loss: null,
  profit_loss_ratio: null,
  realized_pnl: 0,
  unmatched_sell_qty: 0,
  deviation_review: { threshold_pct: 1, count: 0 },
}

const BY_VARIANT: Record<JournalDemoVariant, JournalStatsPayload> = {
  full: FULL,
  partial: PARTIAL,
  empty: EMPTY,
}

export function demoJournalStatsPayload(variant: JournalDemoVariant): JournalStatsPayload {
  return BY_VARIANT[variant]
}

/** 与 `httpJournalStatsSource` 同构（同 `demoLimitPoolSource` 的写法），屏侧只做 `demo ? 演示 : 真实`。 */
export function demoJournalStatsSource(variant: JournalDemoVariant) {
  return {
    load: async (): Promise<JournalStatsPayload> => demoJournalStatsPayload(variant),
  }
}
