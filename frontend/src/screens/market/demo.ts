/**
 * 市场屏演示数据（UI-SPEC §5.6：`?demo=1` 或「设置 → 虚拟数据」任一入口开启，页头带「演示数据」徽标）。
 *
 * 四个 payload 与真实契约**逐键对齐**（键名、可空性、口径都与出站一致，不多不少）：
 * - `/api/sentiment` → `payload.sentiment`（`core/sentiment.py`）
 * - `/api/ladder` → `payload.ladder`（`core/ladder.py`）
 * - `/api/market/history` → `MarketHistoryPayload`（`web/server.py:_sentiment_history_payload`）
 * - `/api/market/theme` → `MarketThemePayload`（`web/server.py:_theme_rows`）
 *
 * 外壳（`domain` / `topic` / `event_id` / `data_as_of` / `source` / `source_detail` / `fetched_at` /
 * `freshness` / `confidence` / `source_status` / `payload.data_source_status`）照
 * `web/server.py:_envelope()` 的规则：`available=true` → `confidence=medium` + `source_status=ok`
 * + `data_as_of` 取请求日；`available=false` → `low` / `partial` / `data_as_of=null`。
 * `domain` 后端对这四段统一给 `sentiment`，`event_id` 恒为 `null`。
 *
 * 后端在这两段还多带 `missing_days` / `quantile` / `delta_vs_prev` 与逐点 `promotion_rate` /
 * `limit_up_count`，但 `src/api.ts` 的契约（前端唯一真源）没有声明、本屏也不读，这里就不写进来。
 *
 * **绝不在真实通道里回落到这份数据**：真实请求 404 / 断连时页面进空态 / 离线态（§5.6 红线）。
 *
 * 演示事实与涨停样本屏同源：基准交易日 `2026-10-30`、取数时刻固定 `15:00:04+08:00`，
 * 代表股（比亚迪 3 板、宁德时代 1 板…）与 `limitup/demo.ts` 对得上，跨屏截图不打架。
 */

import type {
  LadderLevel,
  LadderResult,
  LadderStock,
  MarketHistoryPoint,
  MarketThemeRow,
  SentimentResult,
} from '../../api'
import type { HistoryEnvelope, LadderPayloadEnvelope, MarketSource, SentimentEnvelope, ThemeEnvelope } from './source'

export type MarketDemoVariant = 'full' | 'partial' | 'empty'

/** 演示基准交易日：与 `limitup/demo.ts:15` 同值 */
export const DEMO_MARKET_DATE = '2026-10-30'

/** 演示态的取数时刻：固定值，保证截图与用例可复现（与 `limitup/demo.ts:18` 同值）。 */
const DEMO_FETCHED_AT = '2026-10-30T15:00:04+08:00'

/** 与 `core/sentiment.py:TEMPERATURE_FORMULA` 逐字一致，页面在 `formula` 里展示口径。 */
const TEMPERATURE_FORMULA =
  '温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一'

// ---------------------------------------------------------------- 当日事实（三段共用一套数）
//
// 涨停 58 家 / 炸板 21 家 → 封板率 73.4%；昨日涨停 62 家、今日晋级 24 家 → 晋级率 38.7%；
// 最高 5 板 → 温度 = 0.45×38.7 + 0.35×73.4 + 0.20×(5/6)×100 = **59.8**，
// 与折线末点 `HISTORY_POINTS` 最后一天同值，指标卡与主图不会互相打脸。

const LIMIT_UP_COUNT = 58
const BROKEN_COUNT = 21
const YESTERDAY_LIMIT_UP_COUNT = 62
const PROMOTED_COUNT = 24
const MAX_BOARD = 5
const PROMOTION_RATE = 38.7
const SEAL_RATE = 73.4
const TEMPERATURE = 59.8

const STOCKS: Record<string, LadderStock> = {
  byd: { code: '002594', name: '比亚迪', boards: 3, first_seal_time: '101205', seal_time: '143001' },
  spdb: { code: '600000', name: '浦发银行', boards: 2, first_seal_time: '093015', seal_time: '142500' },
  pab: { code: '000001', name: '平安银行', boards: 2, first_seal_time: '092945', seal_time: null },
  catl: { code: '300750', name: '宁德时代', boards: 1, first_seal_time: '094210', seal_time: '094210' },
  smic: { code: '688981', name: '中芯国际', boards: 1, first_seal_time: '130502', seal_time: '130502' },
  iflytek: { code: '002230', name: '科大讯飞', boards: 4, first_seal_time: '093300', seal_time: '110218' },
  hongbo: { code: '002229', name: '鸿博股份', boards: 5, first_seal_time: '092500', seal_time: '092500' },
}

/** 板位 → 家数：`1 板 42 → 5 板 1`，合计 58 家与 `LIMIT_UP_COUNT` 对齐。 */
const LEVELS: LadderLevel[] = [
  { boards: 5, count: 1, stocks: [STOCKS.hongbo] },
  { boards: 4, count: 2, stocks: [STOCKS.iflytek] },
  { boards: 3, count: 4, stocks: [STOCKS.byd] },
  { boards: 2, count: 9, stocks: [STOCKS.spdb, STOCKS.pab] },
  { boards: 1, count: 42, stocks: [STOCKS.catl, STOCKS.smic] },
]

/**
 * 近 20 个交易日的情绪温度（CH-01 折线）。
 * 日期都是真实工作日、跳过国庆长假，形状「有起伏」：先回落到 39.8 的坑，再爬到 69.5 的高点，
 * 收尾在 59.8，全程穿过 40–60 区间带与 50 中性线。
 */
const HISTORY_SERIES: Array<[string, number]> = [
  ['2026-09-28', 47.8],
  ['2026-09-29', 51.2],
  ['2026-09-30', 44.6],
  ['2026-10-08', 39.8],
  ['2026-10-09', 42.3],
  ['2026-10-12', 48.9],
  ['2026-10-13', 55.1],
  ['2026-10-14', 58.7],
  ['2026-10-15', 53.4],
  ['2026-10-16', 49.2],
  ['2026-10-19', 45.7],
  ['2026-10-20', 52.8],
  ['2026-10-21', 60.3],
  ['2026-10-22', 65.9],
  ['2026-10-23', 61.4],
  ['2026-10-26', 57.2],
  ['2026-10-27', 63.8],
  ['2026-10-28', 69.5],
  ['2026-10-29', 63.1],
  [DEMO_MARKET_DATE, TEMPERATURE],
]

const HISTORY_POINTS: MarketHistoryPoint[] = HISTORY_SERIES.map(([date, temperature]) => ({
  date,
  temperature,
}))

/** 题材强度（CH-03）：10 条、降序，覆盖 `limit_up_count` / `leader` / `delta` 三种「—」。 */
const THEMES: MarketThemeRow[] = [
  { theme: '人工智能', strength: 88.6, limit_up_count: 12, leader: '鸿博股份', delta: 6.3 },
  { theme: '半导体', strength: 81.2, limit_up_count: 9, leader: '中芯国际', delta: 2.1 },
  { theme: '机器人', strength: 74.9, limit_up_count: 8, leader: '科大讯飞', delta: -3.4 },
  { theme: '固态电池', strength: 66.3, limit_up_count: 6, leader: '宁德时代', delta: 8.7 },
  { theme: '军工', strength: 58.1, limit_up_count: 5, leader: null, delta: 1.2 },
  { theme: '医药', strength: 51.7, limit_up_count: 4, leader: '恒瑞医药', delta: null },
  { theme: '券商', strength: 45.2, limit_up_count: 3, leader: '东方财富', delta: -1.5 },
  { theme: '光伏', strength: 37.8, limit_up_count: 2, leader: '隆基绿能', delta: -5.6 },
  { theme: '地产', strength: 29.4, limit_up_count: null, leader: null, delta: -1.8 },
  { theme: '稀土', strength: 21.5, limit_up_count: 1, leader: '北方稀土', delta: 0.9 },
]

/** 未接 QuotaFuse Wire 时后端给出的原题文案（`server.py:_theme_status`），演示态照抄。 */
const THEME_NOT_CONNECTED = (date: string) =>
  `theme_news 未接入（QuotaFuse Wire），先配置 qfw：缓存后端 missing，${date} 没有 theme_news 记录（配置后运行 ldst sync --dataset theme_news）`

// ---------------------------------------------------------------- CH-01 情绪温度

function sentimentEnvelope(variant: MarketDemoVariant, date: string): SentimentEnvelope {
  const partial = variant === 'partial'
  const sentiment: SentimentResult =
    variant === 'empty'
      ? {
          date,
          available: false,
          reason: '涨停池为空，无法计算情绪指标（先运行 ldst sync）',
          temperature: null,
          promotion_rate: null,
          seal_rate: null,
          limit_up_count: 0,
          broken_count: 0,
          yesterday_limit_up_count: 0,
          promoted_count: 0,
          max_board: null,
          gap: false,
          gap_levels: [],
          missing: ['limit_pool', 'yesterday_limit_up', 'broken_board'],
          formula: TEMPERATURE_FORMULA,
          source_detail: 'limit_pool: missing; broken_board: missing; yesterday_limit_up: missing',
        }
      : {
          date,
          available: true,
          reason: null,
          temperature: TEMPERATURE,
          // 演示降级：昨日涨停缓存缺失 → 晋级率不可得，按 §11.0 显示「—」而不是 0
          promotion_rate: partial ? null : PROMOTION_RATE,
          seal_rate: SEAL_RATE,
          limit_up_count: LIMIT_UP_COUNT,
          broken_count: BROKEN_COUNT,
          yesterday_limit_up_count: partial ? 0 : YESTERDAY_LIMIT_UP_COUNT,
          promoted_count: partial ? 0 : PROMOTED_COUNT,
          max_board: MAX_BOARD,
          gap: false,
          gap_levels: [],
          missing: partial ? ['yesterday_limit_up'] : [],
          formula: TEMPERATURE_FORMULA,
          source_detail: partial
            ? 'limit_pool: duckdb; broken_board: duckdb; yesterday_limit_up: missing'
            : 'limit_pool: duckdb; broken_board: duckdb; yesterday_limit_up: duckdb',
        }
  return {
    domain: 'sentiment',
    topic: 'shortline_sentiment',
    event_id: null,
    data_as_of: variant === 'empty' ? null : date,
    payload: { sentiment, data_source_status: variant === 'empty' ? 'missing' : 'ok' },
    source: 'ladderstock',
    source_detail: sentiment.source_detail ?? '',
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: variant === 'empty' ? 'low' : 'medium',
    source_status: variant === 'empty' ? 'partial' : 'ok',
  }
}

// ---------------------------------------------------------------- CH-02 连板梯队

function ladderEnvelope(variant: MarketDemoVariant, date: string): LadderPayloadEnvelope {
  const empty = variant === 'empty'
  const ladder: LadderResult = {
    date,
    available: !empty,
    reason: empty ? '涨停池没有可解析的连板数据（可能尚未同步）' : null,
    source_detail: empty ? 'limit_pool: missing（0 条）' : `limit_pool: duckdb（${LIMIT_UP_COUNT} 条）`,
    height: empty ? null : MAX_BOARD,
    count: empty ? 0 : LIMIT_UP_COUNT,
    ladder: empty ? [] : LEVELS,
    gap: false,
    gap_levels: [],
    leader_candidates: empty ? [] : [STOCKS.hongbo, STOCKS.iflytek, STOCKS.byd],
    unknown_board: 0,
  }
  return {
    domain: 'sentiment',
    topic: 'limit_ladder',
    event_id: null,
    data_as_of: empty ? null : date,
    payload: { ladder, data_source_status: empty ? 'missing' : 'ok' },
    source: 'ladderstock',
    source_detail: ladder.source_detail,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: empty ? 'low' : 'medium',
    source_status: empty ? 'partial' : 'ok',
  }
}

// ---------------------------------------------------------------- CH-01 序列 + CH-03 题材

function historyEnvelope(variant: MarketDemoVariant, date: string): HistoryEnvelope {
  const empty = variant === 'empty'
  const points = empty ? [] : HISTORY_POINTS
  const payload: HistoryEnvelope['payload'] = {
    date,
    available: !empty,
    reason: empty ? '本机没有更早的交易日缓存（先运行 ldst sync）' : null,
    points,
    window: 20,
    data_source_status: empty ? 'missing' : 'ok',
  }
  return {
    domain: 'sentiment',
    topic: 'shortline_sentiment_history',
    event_id: null,
    data_as_of: empty ? null : points[points.length - 1].date,
    payload,
    source: 'ladderstock',
    source_detail: empty
      ? 'limit_pool 缓存: 0/0 个交易日; 本机没有更早的交易日缓存（先运行 ldst sync）'
      : `limit_pool 缓存: 20/20 个交易日（${points[0].date}~${DEMO_MARKET_DATE}); 可用点: 20`,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: empty ? 'low' : 'medium',
    source_status: empty ? 'partial' : 'ok',
  }
}

function themeEnvelope(variant: MarketDemoVariant, date: string): ThemeEnvelope {
  // `partial` 走的是本机最常见的形态：接口在、但没接 QuotaFuse Wire → 200 + available=false
  const available = variant === 'full'
  const payload: ThemeEnvelope['payload'] = {
    date,
    available,
    reason: available ? null : THEME_NOT_CONNECTED(date),
    themes: available ? THEMES : [],
    data_source_status: available ? 'ok' : 'missing',
  }
  return {
    domain: 'sentiment',
    topic: 'theme_rotation',
    event_id: null,
    data_as_of: available ? date : null,
    payload,
    source: 'ladderstock',
    source_detail: available
      ? `theme_news: duckdb（${THEMES.length * 12} 条 → ${THEMES.length} 个题材，${date}）`
      : `theme_news 未接入（QuotaFuse Wire），先配置 qfw：缓存后端 missing，${date} 没有 theme_news 记录（配置后运行 ldst sync --dataset theme_news）`,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: available ? 'medium' : 'low',
    source_status: available ? 'ok' : 'partial',
  }
}

export function demoMarketSource(variant: MarketDemoVariant): MarketSource {
  return {
    sentiment: async (date) => sentimentEnvelope(variant, date || DEMO_MARKET_DATE),
    ladder: async (date) => ladderEnvelope(variant, date || DEMO_MARKET_DATE),
    history: async (date) => historyEnvelope(variant, date || DEMO_MARKET_DATE),
    theme: async (date) => themeEnvelope(variant, date || DEMO_MARKET_DATE),
  }
}
