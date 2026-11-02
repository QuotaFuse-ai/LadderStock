/**
 * 盯盘页演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页头带「演示数据」徽标）。
 *
 * 形状与 `/api/watch/snapshot` 契约逐键对齐：外壳走 `web/server.py:_envelope`
 * （`data_as_of` / `fetched_at` / `freshness` / `source_status` / `source_detail`），
 * 行走 `web/server.py:_watch_rows`（`code` / `name` / `limit_up_count` / `change_pct` / `status`）。
 * `status` 只取 `watch/signal.py:KIND_LABELS` 的四档中文标签；`封板` / `开板` 事件的
 * `value` 是价格而不是涨跌幅，所以那几行的 `change_pct` 必须是 `null`，不是 0。
 *
 * **绝不在真实通道里回落到这份数据**：真实请求 404 / 断连时页面进空态 / 离线态。
 * **也不写进本机 `watch_snapshot` 分片**：IndexedDB 回放口径（TTL / 交易日 / 时钟漂移）
 * 只属于真实快照，演示行是另一件事（UI-SPEC §11.2）。
 *
 * 基准交易日与 `limitup/demo.ts:15-18` 同值，19 屏的演示事实要互相打得上。
 */

import type { LadderEnvelope, WatchSnapshotPayload, WatchSnapshotRow } from '../../api'

export type WatchDemoVariant = 'full' | 'partial' | 'empty'

export const DEMO_TRADE_DATE = '2026-10-30'

/** 演示态的取数时刻：固定值，保证截图与用例可复现。 */
export const DEMO_FETCHED_AT = '2026-10-30T15:00:04+08:00'

/** 后端 `WATCH_SNAPSHOT_TTL_SEC` 之外的降级说明文案（§5.2「已降级」要讲清走了哪条路）。 */
const DEGRADED_DETAIL =
  'watch/status.py: 主工具不可用，已切同 peer 备工具回放；3 分钟拉升缺 5 条，连板数未回填'

/**
 * 14 行（PRD §5.2 一屏 ≥12 行），覆盖级别的全部分支（阈值默认 3.0%）：
 * 强信号（≥2 倍）/ 关注（≥1 倍）/ 提示（<1 倍）/ 封板与开板（触发值「—」）/ 停牌（整行「—」）。
 *
 * 与 `home/demo.ts` 的连板天梯是同一个交易日的事实：`limit_up_count` 一律等于天梯里的
 * `boards`，涨停池内的标的只出现在升向涨停的方向上——`开板` 与 `停牌` 用的是池外代码
 * （前者属炸板池，与 `broken_count=4` 同一件事），不会出现「今日涨停却在跳水」这种互相打脸。
 * `002460` 没有名称来源 → `name: null`，与 `_watch_rows` 的缺值口径一致。
 */
export const DEMO_WATCH_ROWS: WatchSnapshotRow[] = [
  { code: '002594', name: '比亚迪', limit_up_count: 3, change_pct: 6.85, status: '3 分钟拉升' },
  { code: '301234', name: '威腾电气', limit_up_count: null, change_pct: 5.2, status: '3 分钟拉升' },
  { code: '300308', name: '中际旭创', limit_up_count: 3, change_pct: 3.05, status: '3 分钟拉升' },
  { code: '300750', name: '宁德时代', limit_up_count: 1, change_pct: 4.21, status: '3 分钟拉升' },
  { code: '601869', name: '长飞光纤', limit_up_count: null, change_pct: -4.75, status: '3 分钟跳水' },
  { code: '300059', name: '东方财富', limit_up_count: null, change_pct: -3.42, status: '3 分钟跳水' },
  { code: '688981', name: '中芯国际', limit_up_count: 1, change_pct: null, status: '封板' },
  { code: '601127', name: '赛力斯', limit_up_count: 2, change_pct: null, status: '封板' },
  { code: '002460', name: null, limit_up_count: null, change_pct: null, status: '封板' },
  { code: '002131', name: '利欧股份', limit_up_count: null, change_pct: null, status: '开板' },
  { code: '002415', name: '海康威视', limit_up_count: 1, change_pct: 2.98, status: '3 分钟拉升' },
  { code: '600000', name: '浦发银行', limit_up_count: 2, change_pct: 1.1, status: '3 分钟拉升' },
  { code: '000725', name: '京东方A', limit_up_count: null, change_pct: -1.86, status: '3 分钟跳水' },
  { code: '601012', name: '隆基绿能', limit_up_count: null, change_pct: null, status: null },
]

/** 演示态的快照来源行：与 `watch_status.status_detail()` 同口径的一行说明。 */
const FULL_DETAIL = 'watch/status.py: 本机状态文件（14 条，事件按 code 合并为每股一行）'
const EMPTY_DETAIL =
  'watch/status.py: 未找到状态文件（先运行 ldst watch start 产出 watch/status.py）'

/**
 * `LadderEnvelope` 外壳，逐键对齐 `web/server.py:_envelope`：
 * `available=false` 时 `data_as_of=null` 且 `source_status=partial`，不拿请求日期顶替数据时点。
 */
function envelopeOf<T>(
  domain: LadderEnvelope<T>['domain'],
  topic: string,
  payload: T & { data_source_status?: 'ok' | 'missing' },
  options: { available: boolean; sourceDetail: string; degraded?: boolean },
): LadderEnvelope<T> {
  const asOf = options.available ? DEMO_TRADE_DATE : null
  return {
    domain,
    topic,
    event_id: null,
    data_as_of: asOf,
    payload,
    source: 'ladderstock',
    source_detail: options.sourceDetail,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: options.available ? 'medium' : 'low',
    source_status: options.available ? (options.degraded ? 'degraded' : 'ok') : 'partial',
  }
}

export function demoWatchPayload(variant: WatchDemoVariant): LadderEnvelope<WatchSnapshotPayload> {
  if (variant === 'empty') {
    return envelopeOf<WatchSnapshotPayload>(
      'sentiment',
      'watch_snapshot',
      { trade_date: null, rows: [], data_source_status: 'missing' },
      { available: false, sourceDetail: EMPTY_DETAIL },
    )
  }
  if (variant === 'partial') {
    // 降级：只回放备工具取到的那 9 行，封板行的连板数保持 null，不补 0、不借主工具的值补造
    const rows = DEMO_WATCH_ROWS.slice(0, 9).map((row) => ({
      ...row,
      limit_up_count: row.status === '封板' ? null : row.limit_up_count,
    }))
    return envelopeOf<WatchSnapshotPayload>(
      'sentiment',
      'watch_snapshot',
      { trade_date: DEMO_TRADE_DATE, rows, data_source_status: 'ok' },
      { available: true, sourceDetail: DEGRADED_DETAIL, degraded: true },
    )
  }
  return envelopeOf<WatchSnapshotPayload>(
    'sentiment',
    'watch_snapshot',
    { trade_date: DEMO_TRADE_DATE, rows: DEMO_WATCH_ROWS, data_source_status: 'ok' },
    { available: true, sourceDetail: FULL_DETAIL },
  )
}

/** 屏侧取数口：与 `api.watchSnapshot` 同签名，便于 `demo ? demoSource : httpSource` 直接替换。 */
export interface WatchSource {
  snapshot(signal?: AbortSignal): Promise<LadderEnvelope<WatchSnapshotPayload>>
}

export function demoWatchSource(variant: WatchDemoVariant): WatchSource {
  return {
    snapshot: async () => demoWatchPayload(variant),
  }
}
