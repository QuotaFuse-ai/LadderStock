/**
 * 首页演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页头带「演示数据」徽标）。
 *
 * 首页 v2（SPEC-HOME-V2）聚合五路只读接口：`/api/indices`、`/api/watchlist`、
 * `/api/positions`、`/api/watch/snapshot`、`/api/validation`。
 * 信封外壳逐键对齐 `web/server.py:_envelope`（`available=false` → `data_as_of=null`
 * + `source_status=partial`，不拿请求日期顶替数据时点）。
 *
 * 盯盘快照的行直接取 `watch/demo.ts` 的同一批事实；持仓取 `radar/demo.ts` 的夹具，
 * 再补上首页需要的名称与当日盈亏。指数 / 自选的字段口径对齐 QUOT-380 的 S2 契约。
 *
 * **绝不在真实通道里回落到这份数据**：404 / 断连时页面进空态 / 离线态。
 */

import type {
  IndexQuoteItem,
  IndicesPayload,
  LadderEnvelope,
  ValidationPayload,
  WatchSnapshotPayload,
  WatchlistItem,
  WatchlistPayload,
} from '../../api'
import type { PositionsPayload } from '../../api/radar'
import { DEMO_FETCHED_AT, DEMO_TRADE_DATE, DEMO_WATCH_ROWS } from '../watch/demo'
import { demoPositions } from '../radar/demo'

/** 基准交易日与 `limitup/demo.ts:15-18` 同值；屏侧只要认 `home/demo` 一处即可。 */
export { DEMO_FETCHED_AT, DEMO_TRADE_DATE }

export type HomeDemoVariant = 'full' | 'partial' | 'empty'

/**
 * 条件列表按 `_validation_conditions` 的缺值口径给：源文件没有标的代码的行 →
 * `code: null`，不是空串；`reconcile` 在演示态保持 `null`（还没收盘对账）。
 */
const VALIDATION_CONDITIONS: ValidationPayload['conditions'] = [
  { code: '002594', trigger: '开盘 15 分钟内不破 5 日线，破位减半', note: null },
  { code: '600837', trigger: '涨停次日竞价涨幅 ≥ 2%，低于则放弃', note: null },
  { code: null, trigger: '情绪温度跌破 45 时整体降一档仓位', note: null },
  { code: '300308', trigger: '3 分钟跳水 ≥ 3.0% 立即减半', note: '已与盯盘阈值对齐' },
]

/** `LadderEnvelope` 外壳，逐键对齐 `web/server.py:_envelope`。 */
function envelopeOf<T>(
  domain: LadderEnvelope<T>['domain'],
  topic: string,
  payload: T & { data_source_status?: 'ok' | 'missing' },
  options: { available: boolean; sourceDetail: string; degraded?: boolean },
): LadderEnvelope<T> {
  return {
    domain,
    topic,
    event_id: null,
    data_as_of: options.available ? DEMO_TRADE_DATE : null,
    payload,
    source: 'ladderstock',
    source_detail: options.sourceDetail,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: options.available ? 'medium' : 'low',
    source_status: options.available ? (options.degraded ? 'degraded' : 'ok') : 'partial',
  }
}

// ---------------------------------------------------------------- 首页 v2：指数 / 自选 / 持仓

/**
 * 参考页的 8 格顺序：A 股四指数 → 美股 → 港股。
 * 真实后端（QUOT-380）先只交付 A 股四只；演示源给满 8 格是为了让版式在 1440 / 1280
 * 两档都被真正验证过（前端对 `items[]` 只做「给几格渲染几格」）。
 */
const INDEX_ITEMS: IndexQuoteItem[] = [
  { code: 'sh000001', name: '上证指数', market: 'cn', value: 3813.79, change_pct: 0.05 },
  { code: 'sh000300', name: '沪深300', market: 'cn', value: 4317.25, change_pct: 0.16 },
  { code: 'sz399001', name: '深证成指', market: 'cn', value: 12641.86, change_pct: -0.42 },
  { code: 'sz399006', name: '创业板指', market: 'cn', value: 2856.31, change_pct: 0.73 },
  { code: 'us.DJI', name: '道琼斯', market: 'us', value: 41113.97, change_pct: -0.21 },
  { code: 'us.IXIC', name: '纳斯达克综合', market: 'us', value: 17856.42, change_pct: 0.63 },
  { code: 'hk.HSI', name: '恒生指数', market: 'hk', value: 20198.52, change_pct: 1.12 },
  { code: 'hk.HSTECH', name: '恒生科技', market: 'hk', value: 4420.18, change_pct: 0.86 },
]

const WATCHLIST_ITEMS: WatchlistItem[] = [
  { code: '600519', name: '贵州茅台', latest_price: 1263.0, change_pct: 0.57 },
  { code: '300308', name: '中际旭创', latest_price: 188.42, change_pct: -1.26 },
  { code: '002594', name: '比亚迪', latest_price: 312.55, change_pct: 2.04 },
  { code: '600837', name: '海通证券', latest_price: 11.86, change_pct: 0.34 },
  { code: '300750', name: '宁德时代', latest_price: 252.1, change_pct: -0.68 },
]

const POSITION_NAMES: Record<string, string> = {
  '600000': '浦发银行',
  '600519': '贵州茅台',
  '300750': '宁德时代',
}

/** 演示夹具的当日盈亏；真实通道要等 QUOT-380 从 `daily_k` 昨收价算，缺失时整列 `—`。 */
const POSITION_DAY_PNL: Record<string, number> = {
  '600000': 120,
  '600519': -220,
  '300750': 355,
}

function indicesPayload(variant: HomeDemoVariant): LadderEnvelope<IndicesPayload> {
  if (variant === 'empty') {
    return envelopeOf<IndicesPayload>('market', 'indices',
      { items: [], data_source_status: 'missing' },
      { available: false, sourceDetail: 'index_quote: missing（未接入指数行情数据集）' },
    )
  }
  const items =
    variant === 'partial'
      ? INDEX_ITEMS.map((item) =>
          item.market === 'hk' ? { ...item, value: null, change_pct: null, reason: '源未接入' } : item,
        )
      : INDEX_ITEMS
  return envelopeOf<IndicesPayload>('market', 'indices',
    { items, data_source_status: 'ok' },
    {
      available: true,
      degraded: variant === 'partial',
      sourceDetail: variant === 'partial' ? 'index_quote: 港股未接入，A 股与美股可用' : 'index_quote: 行情快照',
    },
  )
}

function watchlistPayload(variant: HomeDemoVariant): WatchlistPayload {
  if (variant === 'empty') {
    return {
      local_only: true,
      watch_count: 0,
      items: [],
      as_of: null,
      price_source: null,
      data_source_status: 'missing',
    }
  }
  if (variant === 'partial') {
    return {
      local_only: true,
      watch_count: 3,
      items: [
        WATCHLIST_ITEMS[0],
        WATCHLIST_ITEMS[1],
        { ...WATCHLIST_ITEMS[2], latest_price: null, change_pct: null, reason: '现价未取到' },
      ],
      as_of: DEMO_TRADE_DATE,
      price_source: 'spot_quote: 部分代码未命中',
      data_source_status: 'ok',
    }
  }
  return {
    local_only: true,
    watch_count: WATCHLIST_ITEMS.length,
    items: WATCHLIST_ITEMS,
    as_of: DEMO_TRADE_DATE,
    price_source: 'spot_quote: 行情快照',
    data_source_status: 'ok',
  }
}

function positionsPayload(variant: HomeDemoVariant): PositionsPayload {
  const base = demoPositions(variant)
  const items = base.items.map((item) => ({
    ...item,
    name: POSITION_NAMES[item.code] ?? null,
    currency: null,
    day_pnl: item.latest_price === null ? null : POSITION_DAY_PNL[item.code] ?? null,
  }))
  const dayPnl = items.every((item) => item.day_pnl === null)
    ? null
    : items.reduce((total, item) => total + (item.day_pnl ?? 0), 0)
  return {
    ...base,
    items,
    valuation: { ...base.valuation, day_pnl: dayPnl },
  }
}

function watchPayload(variant: HomeDemoVariant): LadderEnvelope<WatchSnapshotPayload> {
  if (variant === 'empty') {
    return envelopeOf<WatchSnapshotPayload>('sentiment', 'watch_snapshot',
      { trade_date: null, rows: [], data_source_status: 'missing' },
      {
        available: false,
        sourceDetail: 'watch/status.py: 未找到状态文件（先运行 ldst watch start）',
      },
    )
  }
  if (variant === 'partial') {
    return envelopeOf<WatchSnapshotPayload>('sentiment', 'watch_snapshot',
      {
        trade_date: DEMO_TRADE_DATE,
        rows: DEMO_WATCH_ROWS.slice(0, 6),
        data_source_status: 'ok',
      },
      {
        available: true,
        degraded: true,
        sourceDetail: 'watch/status.py: 主工具不可用，已切同 peer 备工具回放（8 条未合并）',
      },
    )
  }
  return envelopeOf<WatchSnapshotPayload>('sentiment', 'watch_snapshot',
    { trade_date: DEMO_TRADE_DATE, rows: DEMO_WATCH_ROWS, data_source_status: 'ok' },
    { available: true, sourceDetail: 'watch/status.py: 本机状态文件（14 条，每股一行）' },
  )
}

function validationPayload(date: string, variant: HomeDemoVariant): LadderEnvelope<ValidationPayload> {
  if (variant === 'empty') {
    return envelopeOf<ValidationPayload>('validation', 'validation',
      { trade_date: date, conditions: [], reconcile: null, data_source_status: 'missing' },
      {
        available: false,
        sourceDetail: `local validation cache 不存在：validation-${date}.json（先运行 ldst review --date ${date}）`,
      },
    )
  }
  return envelopeOf<ValidationPayload>('validation', 'validation',
    {
      trade_date: date,
      conditions: variant === 'partial' ? VALIDATION_CONDITIONS.slice(0, 2) : VALIDATION_CONDITIONS,
      reconcile: null,
      data_source_status: 'ok',
    },
    {
      available: true,
      sourceDetail: `local validation cache: validation-${date}.json；尚未对账（ldst validate reconcile）`,
    },
  )
}

/** 屏侧取数口：与 http 源同签名（QUOT-381 首页 v2 起含指数 / 自选 / 持仓五路）。 */
export interface HomeSource {
  watchSnapshot(signal?: AbortSignal): Promise<LadderEnvelope<WatchSnapshotPayload>>
  validation(date: string, signal?: AbortSignal): Promise<LadderEnvelope<ValidationPayload>>
  indices(signal?: AbortSignal): Promise<LadderEnvelope<IndicesPayload>>
  watchlist(signal?: AbortSignal): Promise<WatchlistPayload>
  positions(date: string, signal?: AbortSignal): Promise<PositionsPayload>
}

export function demoHomeSource(variant: HomeDemoVariant, date: string): HomeSource {
  return {
    watchSnapshot: async () => watchPayload(variant),
    validation: async () => validationPayload(date, variant),
    indices: async () => indicesPayload(variant),
    watchlist: async () => watchlistPayload(variant),
    positions: async () => positionsPayload(variant),
  }
}
