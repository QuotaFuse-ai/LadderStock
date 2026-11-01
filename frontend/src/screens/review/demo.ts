/**
 * 收盘复盘页演示数据（`?demo=1` 或设置页「虚拟数据」开关，页面带「演示数据」徽标）。
 *
 * 形状与 `/api/review` + `/api/sentiment` 两条契约逐键对齐（含 `LadderEnvelope` 外壳），
 * 事实基线与其余屏共用同一份市场事实：交易日 2026-10-30、涨停 47 家、连板 1板32/2板8/3板4/4板2/5板1。
 *
 * **绝不在真实通道里回落到这份数据**：演示源只由 `useDemoEnabled()` 选中，
 * 真实请求 404 / 断连时页面进空态 / 离线态，不展示这里的任何一行。
 */

import type { LadderEnvelope, ReviewPayload, SentimentResult } from '../../api'
import type { ReviewSource } from './useReviewView'

export type ReviewDemoVariant = 'full' | 'partial' | 'empty'

/** 演示交易日：与 limitup / radar 等屏同一份事实基线。 */
export const DEMO_REVIEW_DATE = '2026-10-30'

/** 演示态的取数时刻：固定值，保证截图与用例可复现。 */
export const DEMO_FETCHED_AT = '2026-10-30T15:00:04+08:00'

const DEMO_MODEL = 'deepseek-v3'

/**
 * 演示事实基线的程序指标（所有屏共用同一份市场事实，取值互相对得上）：
 * 涨停 47 家 = 1板32 + 2板8 + 3板4 + 4板2 + 5板1，最高 5 板、梯队无断层；
 * 昨日涨停 46 家里今日晋级 15 家（1→2 板 25%、2→3 板 50%）→ 晋级率 32.6%；
 * 封板 47 / 炸板 8 → 封板率 85.5%。契约里没有的字段（如「较昨日 +6」）不补。
 */
export const DEMO_SENTIMENT: SentimentResult = {
  date: DEMO_REVIEW_DATE,
  available: true,
  reason: null,
  temperature: 68,
  promotion_rate: 32.6,
  seal_rate: 85.5,
  limit_up_count: 47,
  broken_count: 8,
  yesterday_limit_up_count: 46,
  promoted_count: 15,
  max_board: 5,
  gap: false,
  gap_levels: [],
  missing: [],
  formula: '温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一',
  source_detail: 'limit_pool: duckdb（47 条）；broken_board: duckdb（8 条）',
}

/** 外壳上 `source_detail` / `freshness` 的演示取值（与 `DEMO_SENTIMENT.source_detail` 同源）。 */
const DEMO_SENTIMENT_SOURCE = {
  source_detail: DEMO_SENTIMENT.source_detail ?? '',
  freshness: 'cached' as const,
}

interface DemoSection {
  name: string
  title: string
  narrative: string[]
  notes: string[]
}

/** 五段固定顺序（情绪→资金→题材→龙虎榜→龙头），内容只写叙述，指标一律归指标卡。 */
const SECTIONS: DemoSection[] = [
  {
    name: 'sentiment',
    title: '情绪温度与赚钱效应',
    narrative: [
      '温度处在偏暖区间且较昨日继续抬升，涨停家数与最高板同步走高，说明资金愿意在高位承接。',
      '昨日涨停个股今日晋级比例不高不低，属于「有接力但分化」的一天：龙头加速、跟风股开始掉队。',
    ],
    notes: ['指标口径见上方指标卡，本段不复述数字。'],
  },
  {
    name: 'capital',
    title: '资金结构与主力行为',
    narrative: [
      '封板结构上早盘封板占比偏高，午后炸板回封的次数不多，资金态度偏坚决而非反复试探。',
      '龙虎榜与连板梯队的交叉看，席位以游资主导为主，机构痕迹集中在少数题材龙头，次日溢价预期需打折。',
    ],
    notes: [],
  },
  {
    name: 'theme',
    title: '题材主线与轮动',
    narrative: [
      '人工智能仍是强度第一的主线，机器人、半导体紧随其后，三线同日共振是当日情绪上行的直接来源。',
      '军工、医药处在第二梯队，新能源与消费只有零星个股，金融题材偏弱，属于压舱而非进攻方向。',
    ],
    notes: [],
  },
  {
    name: 'dragon_tiger',
    title: '龙虎榜席位与性质',
    narrative: [
      '上榜个股集中在高位连板与首板人气股两类，前者席位换手充分，后者以量化与散户通道为主，持续性要分开评估。',
      '未见明显的机构对倒与一日游席位扎堆，本段结论以「结构正常、需次日验证」为主。',
    ],
    notes: [],
  },
  {
    name: 'leader',
    title: '龙头与梯队结构',
    narrative: [
      '空间板抬到五板且梯队没有断层，龙头位置明确；二到三板的卡位股是次日最值得观察的一档。',
      '补涨龙头要等主线分歧日才出现，今日一致加速，次日若高开低走则优先做低吸而非追涨。',
    ],
    notes: [],
  },
]

/** `partial` 变体：第三段降级、第五段失败，其余段落照常（§3.1 部分失败主场景）。 */
const DEGRADED_SECTION = 'theme'
const FAILED_SECTION = 'leader'

function sectionPayload(variant: ReviewDemoVariant) {
  return SECTIONS.map((section) => {
    const failed = variant === 'partial' && section.name === FAILED_SECTION
    const degraded = variant === 'partial' && section.name === DEGRADED_SECTION
    return {
      name: section.name,
      title: section.title,
      status: failed ? 'failed' : degraded ? 'degraded' : 'ok',
      mode: degraded ? 'template' : 'structured',
      model: failed ? null : degraded ? null : DEMO_MODEL,
      degraded,
      narrative: failed ? [] : section.narrative,
      notes: degraded
        ? ['模板降级：本段 AI 叙述未生成，以下要点由程序从事实包直出。', ...section.notes]
        : section.notes,
      metrics: {},
    }
  })
}

const HEADLINE = [
  '情绪偏暖且较昨日继续抬升，涨停家数与最高板同步走高。',
  '连板梯队 1 板 32 家、最高 5 板，梯队没有断层。',
  '题材主线集中在人工智能、机器人、半导体三线共振。',
]

function reviewEnvelope(variant: ReviewDemoVariant, date: string): LadderEnvelope<ReviewPayload> {
  if (variant === 'empty') {
    // 外壳口径同 `_envelope(available=false)`：`data_as_of=null`、payload 带 `data_source_status=missing`
    return {
      domain: 'review',
      topic: 'review',
      event_id: null,
      data_as_of: null,
      payload: { date, review: null, data_source_status: 'missing' },
      source: 'ladderstock',
      source_detail: `local review cache: ${date}.json 不存在`,
      fetched_at: DEMO_FETCHED_AT,
      freshness: 'cached',
      confidence: 'low',
      source_status: 'partial',
    }
  }
  const partial = variant === 'partial'
  return {
    domain: 'review',
    topic: 'review',
    event_id: null,
    data_as_of: date,
    payload: {
      date,
      review: {
        title: `${date} 短线复盘`,
        date,
        generated_at: '2026-10-30T15:20:00+08:00',
        freshness: 'cached',
        source_status: partial ? 'partial' : 'ok',
        source_detail: `local review cache: ${date}.json`,
        ai: { model: DEMO_MODEL, degraded: partial },
        headline: HEADLINE,
        sections: sectionPayload(variant),
        ai_failures: partial
          ? [
              {
                section: 'leader',
                kind: 'network',
                message: '网络请求失败：连接超时（10s）',
                action: '重试该段，或查看 ldst ai status',
                retryable: true,
              },
            ]
          : [],
        validation: { date: '2026-10-31', conditions: [] },
        footer: '演示数据：本段内容不是真实行情，仅用于页面结构与状态走查。',
      },
      data_source_status: 'ok',
    },
    source: 'ladderstock',
    source_detail: `local review cache: ${date}.json`,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: partial ? 'low' : 'medium',
    // §5.2：`partial` = 数据集缺项 → danger；`degraded` = 走了别的路 → warn
    source_status: partial ? 'partial' : 'ok',
  }
}

function sentimentEnvelope(variant: ReviewDemoVariant, date: string): LadderEnvelope<{ sentiment: SentimentResult }> {
  const available = variant !== 'empty'
  return {
    domain: 'sentiment',
    topic: 'shortline_sentiment',
    event_id: null,
    data_as_of: available ? date : null,
    payload: {
      sentiment: available
        ? { ...DEMO_SENTIMENT, date }
        : {
            ...DEMO_SENTIMENT,
            date,
            available: false,
            reason: '当日涨停池未同步：本机还没有该交易日的同步产物。',
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
          },
      data_source_status: available ? 'ok' : 'missing',
    },
    source: 'ladderstock',
    source_detail: DEMO_SENTIMENT_SOURCE.source_detail,
    fetched_at: DEMO_FETCHED_AT,
    freshness: DEMO_SENTIMENT_SOURCE.freshness,
    confidence: available ? 'medium' : 'low',
    // 演示态把两档降级语义都铺出来：复盘正文 partial（danger）、程序指标 degraded（warn）
    source_status: variant === 'partial' ? 'degraded' : available ? 'ok' : 'partial',
  }
}

export function demoReviewSource(variant: ReviewDemoVariant): ReviewSource {
  return {
    loadReview: async (query) => reviewEnvelope(variant, query.date || DEMO_REVIEW_DATE),
    loadSentiment: async (query) => sentimentEnvelope(variant, query.date || DEMO_REVIEW_DATE),
  }
}
