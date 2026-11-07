/**
 * 首页演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页头带「演示数据」徽标）。
 *
 * v2 版式（SPEC-HOME-V2 §1.1）把首页改成「先看大盘 → 再决定研究什么 → 只看自己的票」，
 * 取数口从四路换成五路：**指数条 / 自选 / 持仓 / 盯盘快照 / 验证条件**。
 * 五份 payload 必须齐给，缺一处首屏就还是半张空卡。
 *
 * 两条通道的形状不同，这里是分开给的（SPEC-RADAR-PAGES §9.1 / §9.2）：
 * - `/api/indices` 走信封 → `IndicesPayload` 的溯源四键（`freshness` 等）有值；
 * - `/api/watchlist` 是平面 JSON、没有信封键 → 溯源四键恒为 `null`，徽标如实出「未知」。
 * 把两者写成同一个 helper 会把平面响应的「外壳没有这一档」演成「有且是 cached」。
 *
 * 盯盘快照的行直接取 `watch/demo.ts` 的同一批事实，两屏讲的是同一个交易日。
 * 自选与持仓共用同一批行情（`600000` / `300750` / `600519` 的现价、昨收一致），
 * 持仓的市值 / 盈亏 / 当日盈亏全部由这份夹具现算，不写死四个互不相同的数字。
 *
 * **绝不在真实通道里回落到这份数据**：404 / 断连时页面进空态 / 离线态。
 */

import type {
  LadderEnvelope,
  ValidationPayload,
  WatchSnapshotPayload,
} from '../../api'
import type {
  IndicesPayload,
  PositionsPayload,
  WatchlistPayload,
} from '../../api/radar'
import { DEMO_FETCHED_AT, DEMO_TRADE_DATE, DEMO_WATCH_ROWS } from '../watch/demo'

/** 基准交易日与 `limitup/demo.ts:15-18` 同值；屏侧只要认 `home/demo` 一处即可。 */
export { DEMO_FETCHED_AT, DEMO_TRADE_DATE }

export type HomeDemoVariant = 'full' | 'partial' | 'empty'

/** 演示日的前一交易日：当日盈亏 = `close(T) − close(T−1)`，两个出口必须是这两天。 */
const DEMO_PREV_DATE = '2026-10-29'
const DEMO_QUOTE_AT = '2026-10-30T14:57:00'

// ------------------------------------------------------------------ A 块：指数条

/** 一格指数的入参：昨收 + 现价，涨跌与涨跌幅按后端同一算式导出（不写两组互相矛盾的数字）。 */
type IndexSeed = {
  code: string
  market: string
  name: string
  prevClose: number
  price: number | null
  reason?: string
}

const INDEX_SEEDS: IndexSeed[] = [
  { code: 'sh000001', market: 'SH', name: '上证指数', prevClose: 3637.96, price: 3608.1 },
  { code: 'sh000300', market: 'SH', name: '沪深300', prevClose: 4301.2, price: 4267.55 },
  { code: 'sz399001', market: 'SZ', name: '深证成指', prevClose: 12815.44, price: 12768.02 },
  { code: 'sz399006', market: 'SZ', name: '创业板指', prevClose: 2504.18, price: 2519.21 },
  { code: 'DJI', market: 'US', name: '道琼斯', prevClose: 51523.06, price: 51570.09 },
  { code: 'IXIC', market: 'US', name: '纳斯达克综合', prevClose: 24673.5, price: 24707.81 },
  // 港股无合规源（QUOT-471，DEC-006 闸门 4 不自采）：恒定「源未接入」，不补 0。
  { code: 'HSI', market: 'HK', name: '恒生指数', prevClose: 0, price: null, reason: '源未接入：港股指数无合规源' },
  { code: 'HSTECH', market: 'HK', name: '恒生科技', prevClose: 0, price: null, reason: '源未接入：港股指数无合规源' },
]

/** 港股两格无论哪个变体都不可用：没有源，就不该在演示里「凭空有值」。 */
const HK_CODES = new Set(['HSI', 'HSTECH'])

function indexOf(seed: IndexSeed, options: { available: boolean; reason: string | null }) {
  const price = options.available ? seed.price : null
  const change = price === null ? null : Number((price - seed.prevClose).toFixed(2))
  return {
    code: seed.code,
    market: seed.market,
    name: seed.name,
    available: options.available,
    price,
    change,
    change_pct: price === null || seed.prevClose === 0 ? null : Number(((change ?? 0) / seed.prevClose * 100).toFixed(2)),
    prev_close: price === null ? null : seed.prevClose,
    as_of: price === null ? null : DEMO_QUOTE_AT,
    reason: options.reason,
  }
}

function indicesPayload(variant: HomeDemoVariant): IndicesPayload {
  // `partial`：创业板指那一格没回。缺格也在 `cells[]` 里，前端渲染几格就是几格；
  // 港股两格恒定不可用（无源），不参与 `partial` 的「制造缺格」。
  const broken = variant === 'partial' ? 3 : -1
  const cells = INDEX_SEEDS.map((seed, index) =>
    indexOf(seed, {
      available: variant !== 'empty' && !HK_CODES.has(seed.code) && index !== broken,
      reason:
        HK_CODES.has(seed.code)
          ? seed.reason ?? '源未接入：港股指数无合规源'
          : variant === 'empty'
            ? 'index_quote 这批里没有这一天的行（先 ldst sync --dataset index_quote）'
            : index === broken
              ? 'index_quote 响应里没有创业板指'
              : null,
    }),
  )
  const priced = cells.filter((cell) => cell.price !== null).length
  const status = priced === 0 ? 'missing' : priced === cells.length ? 'ok' : 'partial'
  return {
    available: priced > 0,
    reason: priced === 0 ? '所有指数格都没有点位' : null,
    footnote: '指数为行情快照，不是逐笔实时',
    price_source: 'index_quote',
    datasets: ['index_quote', 'index_quote_us'],
    cells,
    data_source_status: status,
    // 外壳：整块没取到时 `data_as_of=null`，不拿请求日期顶替数据时点
    freshness: 'realtime',
    source_status: status === 'ok' ? 'ok' : 'partial',
    source_detail: `index_quote + index_quote_us: peer 行情（${priced}/${cells.length} 格有值）`,
    data_as_of: status === 'missing' ? null : DEMO_TRADE_DATE,
    fetched_at: status === 'missing' ? null : DEMO_FETCHED_AT,
  }
}

// ------------------------------------------------------------------ C 块：我的自选

/** 一条自选的入参：昨收 + 现价，涨跌按后端同一算式导出。 */
type WatchSeed = {
  code: string
  name: string
  market: string
  prevClose: number
  price: number | null
  priceReason?: string
  /** 估值三项：不给即「这项没取到」，页面显示 `—` 并用 `metrics_reason` 解释 */
  pe?: number | null
  pb?: number | null
  turnover?: number | null
}

const WATCH_SEEDS: WatchSeed[] = [
  { code: '600000', name: '浦发银行', market: 'SH', prevClose: 10.06, price: 10.09, pe: 5.8, pb: 0.52, turnover: 0.61 },
  { code: '300750', name: '宁德时代', market: 'SZ', prevClose: 240.19, price: 249.32, pe: 21.6, pb: 4.18, turnover: 1.24 },
  { code: '002594', name: '比亚迪', market: 'SZ', prevClose: 100, price: 103.5, pe: 23.4, pb: 4.92, turnover: 1.03 },
  // 三项里只取到市盈率：PB / 换手同样按缺省 `—`，不折成 0
  { code: '600519', name: '贵州茅台', market: 'SH', prevClose: 1255.84, price: 1263, pe: 24.9 },
  // 停牌 / 没进这批报价：现价就是取不到，不拿成本价顶替（§4.2）
  {
    code: '688981',
    name: '中芯国际',
    market: 'SH',
    prevClose: 92.4,
    price: null,
    priceReason: 'spot_quote 这批响应里没有 688981 的行',
  },
]

/** 缺哪项就写哪项的中文名；三项齐全返回 `null`（与后端 `metrics_reason` 同口径）。 */
function missingMetricNames(
  pe: number | null | undefined,
  pb: number | null | undefined,
  turnover: number | null | undefined,
): string | null {
  const missing = [
    pe == null ? '市盈率' : null,
    pb == null ? '市净率' : null,
    turnover == null ? '换手率' : null,
  ].filter((name): name is string => name !== null)
  return missing.length === 0 ? null : `${missing.join('、')}没有取到`
}

function watchlistOf(seed: WatchSeed) {
  const price = seed.price
  const change = price === null ? null : Number((price - seed.prevClose).toFixed(2))
  return {
    code: seed.code,
    added_at: `${DEMO_TRADE_DATE}T09:30:00`,
    available: price !== null,
    name: seed.name,
    market: seed.market,
    latest_price: price,
    change,
    change_pct: price === null ? null : Number(((change ?? 0) / seed.prevClose * 100).toFixed(2)),
    prev_close: price === null ? null : seed.prevClose,
    price_as_of: price === null ? null : DEMO_QUOTE_AT,
    price_status: price === null ? 'unavailable' : 'ok',
    price_reason: price === null ? (seed.priceReason ?? '现价未取到') : null,
    pe_ttm: seed.pe ?? null,
    pb: seed.pb ?? null,
    turnover_pct: seed.turnover ?? null,
    metrics_reason: missingMetricNames(seed.pe, seed.pb, seed.turnover),
  }
}

function watchlistPayload(variant: HomeDemoVariant): WatchlistPayload {
  if (variant === 'empty') {
    return {
      local_only: true,
      store: 'local_sqlite',
      available: false,
      // 空态文案由接口给、指回本机 CLI，页面不自编（QUOT-380 拍板口径）
      reason: '本机还没有自选：用 ldst watch add --code 600000 加一条（写入只在本机）',
      watch_count: 0,
      data_source_status: 'missing',
      price_source: 'spot_quote',
      price_as_of: null,
      source_detail: null,
      items: [],
      freshness: null,
      source_status: null,
      data_as_of: null,
      fetched_at: null,
    }
  }
  // `partial`：只回三条，其中一条没进报价
  const seeds = variant === 'partial' ? WATCH_SEEDS.slice(0, 3) : WATCH_SEEDS
  const items = seeds.map(watchlistOf)
  const priced = items.filter((item) => item.latest_price !== null).length
  return {
    local_only: true,
    store: 'local_sqlite',
    available: items.length > 0,
    reason: null,
    watch_count: items.length,
    data_source_status: priced === items.length ? 'ok' : 'partial',
    price_source: 'spot_quote',
    price_as_of: priced > 0 ? DEMO_QUOTE_AT : null,
    source_detail: `tencent（${priced}/${items.length} 只取到现价）`,
    items,
    // 平面响应没有外壳：这四键恒为 `null`，徽标如实出「未知」（§9.2）
    freshness: null,
    source_status: null,
    data_as_of: priced > 0 ? DEMO_QUOTE_AT : null,
    fetched_at: null,
  }
}

// ------------------------------------------------------------------ D 块：我的持仓

/**
 * 持仓夹具：`positions` 表只有 `code/cost/qty/entry_date/industry/note`，**没有名称列**，
 * 所以这里也不给 `name`（§5.2「名称未接入」，不拿代码或行业顶替）。
 * 现价与自选块同源，两屏讲的是同一批行情。
 */
type PositionSeed = {
  code: string
  cost: number
  qty: number
  industry: string
  price: number | null
  prevClose: number | null
}

const POSITION_SEEDS: PositionSeed[] = [
  { code: '600000', cost: 9.8, qty: 1000, industry: '银行', price: 10.09, prevClose: 10.06 },
  { code: '300750', cost: 210, qty: 200, industry: '电池', price: 249.32, prevClose: 240.19 },
  { code: '600519', cost: 1500, qty: 100, industry: '白酒', price: 1263, prevClose: 1255.84 },
]

function round(value: number): number {
  return Number(value.toFixed(2))
}

function positionsPayload(variant: HomeDemoVariant): PositionsPayload {
  if (variant === 'empty') {
    return {
      local_only: true,
      store: 'local_sqlite',
      data_source_status: 'missing',
      as_of: null,
      price_source: 'daily_k',
      position_count: 0,
      valuation: {
        total_cost: 0,
        market_value: null,
        priced_cost: 0,
        unpriced_cost: 0,
        coverage_pct: null,
        unrealized_pnl: null,
        unrealized_pnl_pct: null,
        daily_pnl: null,
        daily_pnl_status: 'missing',
        currency: 'CNY',
        daily_pnl_missing: [],
      },
      items: [],
      concentration: { available: false, reason: '本机没有持仓', basis: 'cost', top1_pct: null, top3_pct: null, hhi: null, holdings: [] },
      sector_distribution: { available: false, reason: '本机没有持仓', basis: 'cost', labeled_count: 0, unlabeled_count: 0, industries: [] },
      price_lookup: { dataset: 'daily_k', date: null, prev_date: null, backend: null, reason: '本机没有持仓，无需取现价' },
      daily_lookup: { dataset: 'daily_k', date: null, prev_date: null, backend: null, reason: '本机没有持仓，无需算当日盈亏' },
    }
  }

  // `partial`：最后一条当日停牌，缓存里没有 T 日行 → 该条全 `null` + 原因，总数不补半截
  const seeds = POSITION_SEEDS.map((seed, index) =>
    variant === 'partial' && index === POSITION_SEEDS.length - 1 ? { ...seed, price: null, prevClose: null } : seed,
  )

  const items: PositionsPayload['items'] = seeds.map((seed) => {
    const priced = seed.price !== null
    const dayChange = priced && seed.prevClose !== null ? round(seed.price! - seed.prevClose) : null
    return {
      code: seed.code,
      cost: seed.cost,
      qty: seed.qty,
      entry_date: DEMO_PREV_DATE,
      industry: seed.industry,
      note: '',
      latest_price: priced ? seed.price : null,
      market_value: priced ? round(seed.price! * seed.qty) : null,
      unrealized_pnl: priced ? round((seed.price! - seed.cost) * seed.qty) : null,
      unrealized_pnl_pct: priced ? round((seed.price! / seed.cost - 1) * 100) : null,
      daily_pnl: dayChange === null ? null : round(dayChange * seed.qty),
      daily_pnl_status: dayChange === null ? 'missing' : 'ok',
      daily_pnl_reason: dayChange === null ? '缓存里没有这个代码的 T 日 / T−1 日收盘价' : null,
    }
  })

  const totalCost = round(seeds.reduce((sum, seed) => sum + seed.cost * seed.qty, 0))
  const pricedCost = round(
    seeds.filter((seed) => seed.price !== null).reduce((sum, seed) => sum + seed.cost * seed.qty, 0),
  )
  const unpricedCost = round(totalCost - pricedCost)
  const marketValue = round(
    items.reduce((sum, item) => sum + (item.market_value ?? 0), 0),
  )
  const unrealized = round(items.reduce((sum, item) => sum + (item.unrealized_pnl ?? 0), 0))
  const dailyMissing = items.filter((item) => item.daily_pnl === null)
  const dailyTotal = round(items.reduce((sum, item) => sum + (item.daily_pnl ?? 0), 0))
  // 半截总数一律 `null`（口径 5）：有一条算不出就不给「看起来像全账户」的合计
  const dailyComplete = items.length > 0 && dailyMissing.length === 0

  return {
    local_only: true,
    store: 'local_sqlite',
    data_source_status: unpricedCost > 0 ? 'partial' : 'ok',
    as_of: DEMO_TRADE_DATE,
    price_source: 'daily_k',
    position_count: items.length,
    valuation: {
      total_cost: totalCost,
      market_value: unpricedCost > 0 ? null : marketValue,
      priced_cost: pricedCost,
      unpriced_cost: unpricedCost,
      coverage_pct: totalCost > 0 ? round((pricedCost / totalCost) * 100) : null,
      unrealized_pnl: pricedCost > 0 ? unrealized : null,
      unrealized_pnl_pct: pricedCost > 0 ? round((unrealized / pricedCost) * 100) : null,
      daily_pnl: dailyComplete ? dailyTotal : null,
      daily_pnl_status: dailyComplete ? 'ok' : dailyMissing.length === items.length ? 'missing' : 'partial',
      currency: 'CNY',
      daily_pnl_missing: dailyMissing.map((item) => ({ code: item.code, reason: item.daily_pnl_reason ?? null })),
    },
    items,
    concentration: { available: false, reason: '首页不展示集中度', basis: unpricedCost > 0 ? 'cost' : 'market_value', top1_pct: null, top3_pct: null, hhi: null, holdings: [] },
    sector_distribution: { available: false, reason: '首页不展示行业分布', basis: unpricedCost > 0 ? 'cost' : 'market_value', labeled_count: items.length, unlabeled_count: 0, industries: [] },
    price_lookup: {
      dataset: 'daily_k',
      date: DEMO_TRADE_DATE,
      prev_date: null,
      backend: 'parquet',
      reason: unpricedCost > 0 ? 'daily_k 在缓存日期里没有这些代码的收盘价字段' : null,
    },
    daily_lookup: {
      dataset: 'daily_k',
      date: DEMO_TRADE_DATE,
      prev_date: DEMO_PREV_DATE,
      backend: 'parquet',
      reason: dailyMissing.length > 0 ? '部分代码在 T 日 / T−1 日没有行' : null,
    },
  }
}

// ------------------------------------------------------------------ E/F 块沿用

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

function watchPayload(variant: HomeDemoVariant): LadderEnvelope<WatchSnapshotPayload> {
  if (variant === 'empty') {
    return envelopeOf<WatchSnapshotPayload>('sentiment', 'watch_snapshot',
      { trade_date: null, rows: [], data_source_status: 'missing' },
      {
        available: false,
        sourceDetail: 'watch/status.py: 未找到状态文件（盯盘守护进程未启动）',
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
        sourceDetail: `local validation cache 不存在：validation-${date}.json（当日收盘复盘尚未生成）`,
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

/** 屏侧取数口：五条与 `fetchIndices` / `fetchWatchlist` / `api.positions` / `api.watchSnapshot` / `api.validation` 同签名。 */
export interface HomeSource {
  indices(signal?: AbortSignal): Promise<IndicesPayload>
  watchlist(signal?: AbortSignal): Promise<WatchlistPayload>
  positions(signal?: AbortSignal): Promise<PositionsPayload>
  watchSnapshot(signal?: AbortSignal): Promise<LadderEnvelope<WatchSnapshotPayload>>
  validation(date: string, signal?: AbortSignal): Promise<LadderEnvelope<ValidationPayload>>
}

export function demoHomeSource(variant: HomeDemoVariant, date: string): HomeSource {
  return {
    indices: async () => indicesPayload(variant),
    watchlist: async () => watchlistPayload(variant),
    positions: async () => positionsPayload(variant),
    watchSnapshot: async () => watchPayload(variant),
    validation: async () => validationPayload(date, variant),
  }
}
