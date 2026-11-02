/**
 * 首页演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页头带「演示数据」徽标）。
 *
 * 首页聚合四路只读接口，**四份 payload 必须齐给**，缺一处首屏就还是半张空卡：
 * `/api/sentiment`、`/api/ladder`、`/api/watch/snapshot`、`/api/validation`。
 * 外壳逐键对齐 `web/server.py:_envelope`（`available=false` → `data_as_of=null`
 * + `source_status=partial`，不拿请求日期顶替数据时点），payload 逐键对齐
 * `core/sentiment.py:compute_sentiment`、`core/ladder.py:compute_ladder`、
 * `web/server.py:_watch_rows`、`web/server.py:_validation_conditions`。
 *
 * 盯盘快照的行直接取 `watch/demo.ts` 的同一批事实，两屏讲的是同一个交易日；
 * 标的的板数与 `limitup/demo.ts` 也对得上（`002594` 3 板、`600519` 无板数 → 落 `unknown_board`）。
 *
 * **绝不在真实通道里回落到这份数据**：404 / 断连时页面进空态 / 离线态。
 */

import type {
  LadderEnvelope,
  LadderResult,
  LadderStock,
  SentimentResult,
  ValidationPayload,
  WatchSnapshotPayload,
} from '../../api'
import { DEMO_FETCHED_AT, DEMO_TRADE_DATE, DEMO_WATCH_ROWS } from '../watch/demo'

/** 基准交易日与 `limitup/demo.ts:15-18` 同值；屏侧只要认 `home/demo` 一处即可。 */
export { DEMO_FETCHED_AT, DEMO_TRADE_DATE }

export type HomeDemoVariant = 'full' | 'partial' | 'empty'

/** `core/sentiment.py:TEMPERATURE_FORMULA` 原文，指标口径要能复核。 */
const TEMPERATURE_FORMULA =
  '温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一'

/**
 * 2026-10-30 的连板天梯：{5,3,2,1} 四档 15 只，4 板整档缺失 → `gap_levels=[4]`。
 * 板数 / 封板时间与 `limitup/demo.ts` 同值，`600519 贵州茅台` 取不到板数，
 * 落进 `unknown_board` 而不是被编成 1 板。
 */
const LADDER_LEVELS: Array<{ boards: number; stocks: LadderStock[] }> = [
  {
    boards: 5,
    stocks: [
      { code: '002230', name: '科大讯飞', boards: 5, first_seal_time: '092500', seal_time: '092500' },
      { code: '603501', name: '韦尔股份', boards: 5, first_seal_time: '093000', seal_time: '113000' },
    ],
  },
  {
    boards: 3,
    stocks: [
      { code: '002594', name: '比亚迪', boards: 3, first_seal_time: '101205', seal_time: '143001' },
      { code: '300308', name: '中际旭创', boards: 3, first_seal_time: '093700', seal_time: '140500' },
      { code: '002371', name: '北方华创', boards: 3, first_seal_time: '094900', seal_time: null },
      { code: '600837', name: '海通证券', boards: 3, first_seal_time: '100200', seal_time: '100200' },
    ],
  },
  {
    boards: 2,
    stocks: [
      { code: '600000', name: '浦发银行', boards: 2, first_seal_time: '093015', seal_time: '142500' },
      { code: '000001', name: '平安银行', boards: 2, first_seal_time: '092945', seal_time: null },
      { code: '601127', name: '赛力斯', boards: 2, first_seal_time: '133500', seal_time: '133500' },
    ],
  },
  {
    boards: 1,
    stocks: [
      { code: '300750', name: '宁德时代', boards: 1, first_seal_time: '094210', seal_time: '094210' },
      { code: '688981', name: '中芯国际', boards: 1, first_seal_time: '130502', seal_time: '130502' },
      { code: '002415', name: '海康威视', boards: 1, first_seal_time: '141000', seal_time: null },
      { code: '600585', name: '海螺水泥', boards: 1, first_seal_time: '095500', seal_time: '095500' },
      { code: '000651', name: '格力电器', boards: 1, first_seal_time: null, seal_time: null },
      { code: '601899', name: '紫金矿业', boards: 1, first_seal_time: '144500', seal_time: '144500' },
    ],
  },
]

const LADDER_COUNT = LADDER_LEVELS.reduce((total, level) => total + level.stocks.length, 0)

/** 涨停池原始记录数：15 只能解析板数 + 1 条取不到板数（`600519`）→ `unknown_board`。 */
const LIMIT_POOL_RECORDS = LADDER_COUNT + 1

/**
 * 情绪指标由 `compute_sentiment` 的同一套算式得来，逐项能反推：
 * 封板率 15/(15+4)=78.9% · 晋级率 8/26=30.8% · 高度项 min(1,5/6)=83.3
 * → 温度 = 0.45×30.8 + 0.35×78.9 + 0.20×83.3 = **58.1**。
 */
const SENTIMENT_BASE: SentimentResult = {
  date: DEMO_TRADE_DATE,
  available: true,
  reason: null,
  temperature: 58.1,
  promotion_rate: 30.8,
  seal_rate: 78.9,
  limit_up_count: LADDER_COUNT,
  broken_count: 4,
  yesterday_limit_up_count: 26,
  promoted_count: 8,
  max_board: 5,
  gap: true,
  gap_levels: [4],
  missing: [],
  formula: TEMPERATURE_FORMULA,
  source_detail: 'limit_pool: duckdb; broken_board: duckdb; yesterday_limit_up: duckdb',
}

const LADDER_BASE: LadderResult = {
  date: DEMO_TRADE_DATE,
  available: true,
  reason: null,
  source_detail: `limit_pool: duckdb（${LIMIT_POOL_RECORDS} 条）`,
  height: 5,
  count: LADDER_COUNT,
  ladder: LADDER_LEVELS.map((level) => ({
    boards: level.boards,
    count: level.stocks.length,
    stocks: level.stocks,
  })),
  gap: true,
  gap_levels: [4],
  leader_candidates: LADDER_LEVELS[0].stocks.map((stock) => ({ ...stock })),
  unknown_board: 1,
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

type SentimentBody = { sentiment: SentimentResult }
type LadderBody = { ladder: LadderResult }

function sentimentPayload(variant: HomeDemoVariant): LadderEnvelope<SentimentBody> {
  if (variant === 'empty') {
    return envelopeOf<SentimentBody>('sentiment', 'shortline_sentiment',
      {
        sentiment: {
          ...SENTIMENT_BASE,
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
          data_source_status: 'missing',
        } as SentimentBody['sentiment'],
      },
      {
        available: false,
        sourceDetail: 'limit_pool: missing; broken_board: missing; yesterday_limit_up: missing',
      },
    )
  }
  if (variant === 'partial') {
    // 缺 `broken_board` 一个数据集：炸板数取不到就是取不到，不补 0 参与算式
    return envelopeOf<SentimentBody>('sentiment', 'shortline_sentiment',
      {
        sentiment: {
          ...SENTIMENT_BASE,
          reason: null,
          seal_rate: null,
          broken_count: 0,
          missing: ['broken_board'],
          source_detail: 'limit_pool: duckdb; broken_board: missing; yesterday_limit_up: duckdb',
          data_source_status: 'ok',
        } as SentimentBody['sentiment'],
      },
      {
        available: true,
        degraded: true,
        sourceDetail: 'limit_pool: duckdb; broken_board: missing（炸板池未同步，封板率不参与算式）',
      },
    )
  }
  return envelopeOf<SentimentBody>('sentiment', 'shortline_sentiment',
    { sentiment: { ...SENTIMENT_BASE, data_source_status: 'ok' } as SentimentBody['sentiment'] },
    { available: true, sourceDetail: SENTIMENT_BASE.source_detail ?? '' },
  )
}

function ladderPayload(variant: HomeDemoVariant): LadderEnvelope<LadderBody> {
  if (variant !== 'full') {
    const degraded = variant === 'partial'
    return envelopeOf<LadderBody>('sentiment', 'limit_ladder',
      {
        ladder: {
          ...LADDER_BASE,
          available: degraded,
          reason: degraded
            ? '部分层级缺少封板时间，梯队高度按可解析记录给出'
            : '涨停池没有可解析的连板数据（可能尚未同步）',
          height: degraded ? 5 : null,
          count: degraded ? LADDER_COUNT : 0,
          ladder: degraded
            ? LADDER_BASE.ladder.map((level) => ({
                ...level,
                stocks: level.stocks.map((stock) => ({ ...stock, first_seal_time: null })),
              }))
            : [],
          gap_levels: degraded ? [4] : [],
          leader_candidates: degraded ? LADDER_BASE.leader_candidates : [],
          unknown_board: degraded ? 1 : 0,
          data_source_status: degraded ? 'ok' : 'missing',
        } as LadderBody['ladder'],
      },
      {
        available: degraded,
        degraded,
        sourceDetail: degraded
          ? 'limit_pool: duckdb（16 条，5 条缺少封板时间）'
          : 'limit_pool: missing（0 条）',
      },
    )
  }
  return envelopeOf<LadderBody>('sentiment', 'limit_ladder',
    { ladder: { ...LADDER_BASE, data_source_status: 'ok' } as LadderBody['ladder'] },
    { available: true, sourceDetail: LADDER_BASE.source_detail },
  )
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

/** 屏侧取数口：四条与 `api.sentiment` / `api.ladder` / `api.watchSnapshot` / `api.validation` 同签名。 */
export interface HomeSource {
  sentiment(date: string, signal?: AbortSignal): Promise<LadderEnvelope<SentimentBody>>
  ladder(date: string, signal?: AbortSignal): Promise<LadderEnvelope<LadderBody>>
  watchSnapshot(signal?: AbortSignal): Promise<LadderEnvelope<WatchSnapshotPayload>>
  validation(date: string, signal?: AbortSignal): Promise<LadderEnvelope<ValidationPayload>>
}

export function demoHomeSource(variant: HomeDemoVariant, date: string): HomeSource {
  return {
    sentiment: async () => sentimentPayload(variant),
    ladder: async () => ladderPayload(variant),
    watchSnapshot: async () => watchPayload(variant),
    validation: async () => validationPayload(date, variant),
  }
}
