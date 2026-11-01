/**
 * 周度复盘页演示数据（`?demo=1` 或设置页「虚拟数据」开关，页面带「演示数据」徽标）。
 *
 * 形状与 `/api/weekly` 契约逐键对齐（含 `LadderEnvelope` 外壳，`domain` 复用 `review`——
 * `LadderEnvelope` 的域名枚举里没有 weekly）。`facts` 是程序聚合的周度事实包，
 * 演示值全部落在与其他屏同一份市场事实上：周五 2026-10-30 温度 68、涨停 47、最高 5 板。
 *
 * **绝不在真实通道里回落到这份数据**：演示源只由 `useDemoEnabled()` 选中。
 */

import type { LadderEnvelope, WeeklyFacts, WeeklyPayload } from '../../api'
import type { WeeklySource } from './useWeeklyView'
import { DEMO_FETCHED_AT } from '../review/demo'

export type WeeklyDemoVariant = 'full' | 'partial' | 'empty'

/** 2026-10-26 ~ 2026-10-30 那一周（ISO-8601 周历），末日即演示交易日基线。 */
export const DEMO_WEEK = '2026-W44'
export const DEMO_WEEK_START = '2026-10-26'
export const DEMO_WEEK_END = '2026-11-01'

const SESSION_DAYS = [
  '2026-10-26',
  '2026-10-27',
  '2026-10-28',
  '2026-10-29',
  '2026-10-30',
]

const DEMO_MODEL = 'deepseek-v3'

/** 每日事实：末日与 `/api/sentiment`、`/api/limit-pool` 的演示基线逐键对齐。 */
const DAILY = [
  { date: '2026-10-26', temperature: 58.0, promotion: 26.3, seal: 82.5, limit: 38, broken: 8, height: 3 },
  { date: '2026-10-27', temperature: 61.0, promotion: 28.0, seal: 84.1, limit: 41, broken: 7, height: 3 },
  { date: '2026-10-28', temperature: 63.5, promotion: 29.4, seal: 85.0, limit: 43, broken: 7, height: 4 },
  { date: '2026-10-29', temperature: 62.0, promotion: 30.6, seal: 84.2, limit: 45, broken: 8, height: 4 },
  { date: '2026-10-30', temperature: 68.0, promotion: 32.6, seal: 85.5, limit: 47, broken: 8, height: 5 },
]

/** 涨跌分箱（`weekly/facts.py:BREADTH_BANDS` 的九档，合计 = 样本数 5100）。 */
const DISTRIBUTION: Array<[string, number]> = [
  ['跌≥9%', 12],
  ['跌5~9%', 86],
  ['跌2~5%', 640],
  ['跌0~2%', 1180],
  ['平盘', 96],
  ['涨0~2%', 1420],
  ['涨2~5%', 1150],
  ['涨5~9%', 430],
  ['涨≥9%', 86],
]
const BREADTH_SAMPLES = DISTRIBUTION.reduce((sum, [, count]) => sum + count, 0)
const BREADTH_UP = 1420 + 1150 + 430 + 86

/** 主线题材（键名同 `WeeklyThemeRow`；`window` 同后端 `THEME_WINDOW = 10`，`observed` 是本周进过窗口的去重题材数）。 */
const WEEK_DAYS = SESSION_DAYS

const THEMES = [
  theme('人工智能', 5, 5, '2026-10-26', '2026-10-30', 6.8, 3.9),
  theme('机器人', 5, 5, '2026-10-26', '2026-10-30', 6.2, 3.5),
  theme('半导体', 4, 4, '2026-10-26', '2026-10-29', 5.4, 2.8),
  theme('军工', 3, 2, '2026-10-27', '2026-10-30', 4.1, 1.9),
  theme('医药', 3, 2, '2026-10-26', '2026-10-28', 3.6, 1.6),
  theme('新能源', 2, 1, '2026-10-28', '2026-10-30', 3.2, 1.2),
  theme('消费', 1, 1, '2026-10-27', '2026-10-27', 2.4, 1.1),
  theme('金融', 1, 1, '2026-10-29', '2026-10-29', 1.8, 0.7),
]

/** 本周落在窗口内的交易日：`days` 与 `days_on_top` 逐一对得上，不只给计数。 */
function theme(
  name: string,
  daysOnTop: number,
  streakDays: number,
  firstDate: string,
  lastDate: string,
  bestChangePct: number,
  avgChangePct: number,
) {
  const from = WEEK_DAYS.indexOf(firstDate)
  return {
    name,
    days_on_top: daysOnTop,
    streak_days: streakDays,
    first_date: firstDate,
    last_date: lastDate,
    best_change_pct: bestChangePct,
    avg_change_pct: avgChangePct,
    days: WEEK_DAYS.slice(from, from + daysOnTop),
  }
}

/** 本周龙头（当日最高板合并，`LEADER_TOP_N = 5`），与每日 `height` 逐日对得上。 */
const LEADERS = [
  { code: '002230', name: '科大讯飞', boards: 5, date: '2026-10-30' },
  { code: '600410', name: '华胜天成', boards: 4, date: '2026-10-29' },
  { code: '688041', name: '海光信息', boards: 4, date: '2026-10-28' },
  { code: '000938', name: '紫光股份', boards: 3, date: '2026-10-27' },
  { code: '000625', name: '长安汽车', boards: 3, date: '2026-10-26' },
]

const VALIDATION_ITEMS = [
  {
    generated_date: '2026-10-26',
    id: 'cond-1026-1',
    text: '次日情绪温度不低于 60（晋级率维持 25% 以上）',
    metric: 'temperature',
    operator: '>=',
    target: 60,
    actual: 61.0,
    settled: true,
    passed: true,
    next_session: '2026-10-27',
  },
  {
    generated_date: '2026-10-27',
    id: 'cond-1027-1',
    text: '人工智能题材当日涨幅前 10 名里至少 3 只',
    metric: 'theme_count',
    operator: '>=',
    target: 3,
    actual: 2,
    settled: true,
    passed: false,
    next_session: '2026-10-28',
  },
  {
    generated_date: '2026-10-28',
    id: 'cond-1028-1',
    text: '连板高度抬升到 4 板且梯队无断层',
    metric: 'ladder_height',
    operator: '>=',
    target: 4,
    actual: 4,
    settled: true,
    passed: true,
    next_session: '2026-10-29',
  },
  {
    generated_date: '2026-10-28',
    id: 'cond-1028-2',
    text: '当日炸板家数不超过 10 家',
    metric: 'broken_count',
    operator: '<=',
    target: 10,
    actual: 7,
    settled: true,
    passed: true,
    next_session: '2026-10-29',
  },
  {
    generated_date: '2026-10-29',
    id: 'cond-1029-1',
    text: '次日涨停家数回到 45 家以上',
    metric: 'limit_up_count',
    operator: '>=',
    target: 45,
    actual: 47,
    settled: true,
    passed: true,
    next_session: '2026-10-30',
  },
  {
    generated_date: '2026-10-29',
    id: 'cond-1029-2',
    text: '高位股（4 板及以上）次日不给核按钮',
    metric: 'high_board_change_pct',
    operator: '>=',
    target: -9.0,
    actual: null,
    settled: false,
    passed: null,
    next_session: '2026-10-30',
  },
  {
    generated_date: '2026-10-30',
    id: 'cond-1030-1',
    text: '情绪温度不回落超过 5 个点',
    metric: 'temperature',
    operator: '>=',
    target: 63,
    actual: null,
    settled: false,
    passed: null,
    next_session: '2026-11-02',
  },
]

const CALIBER: Record<string, string> = {
  temperature:
    '复用 core/sentiment.py：温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一',
  promotion_rate: '复用 core/sentiment.py：昨日涨停股中今日继续涨停的比例',
  ladder_height: '复用 core/ladder.py：涨停池按 code 去重后的最大连板数',
  breadth:
    '本周 daily_k 个股涨跌幅分箱计数（跌≥9% … 涨≥9%），同日同代码只计首条，与天梯 D-04 去重口径一致',
  theme_streak:
    '本周 concept_board 每日涨幅前 10 名记为一个窗口；days_on_top = 进入窗口的交易日数，streak_days = 本周内的最长连续交易日数',
  validation:
    '复用 core/validation.py 的 reconcile 结果：settled = 已有对账结论的条件数，未对账的进 pending，判定不出的进 unknown',
  ai_coverage: '读 state/ai_checkpoint/<date>.json 的段落状态计数（只读，不重跑模型）',
}

const SECTION_TITLES: Array<[string, string, string]> = [
  ['sentiment', '一周情绪温度与晋级率', '情绪'],
  ['capital', '一周资金流向', '资金'],
  ['theme', '一周题材轮动与主线持续', '题材'],
  ['dragon_tiger', '一周龙虎榜与席位', '龙虎榜'],
  ['leader', '一周龙头与梯队', '龙头'],
]

/** 每周叙述只回看事实包已给出的区间与均值，不复述精确指标数字。 */
const SECTION_NARRATIVE: Record<string, string[]> = {
  sentiment: [
    '本周温度重心逐日上移，周五创新高，赚钱效应稳定而不是单日脉冲。',
    '晋级率前半周偏低、周五回到中枢上方，说明接力意愿恢复而非透支。',
  ],
  capital: [
    '资金结构上，早盘封板占比全周稳定在高位，午后炸板回封的反复不多。',
    '炸板家数维持在个位数，没有出现放量分歧，资金态度偏坚决。',
  ],
  theme: [
    '人工智能与机器人全周都在涨幅窗口内，是唯一两条连续五天入窗的主线。',
    '半导体、军工、医药呈脉冲式轮动，新能源之后没有形成第二梯队。',
  ],
  dragon_tiger: [
    '席位以游资主导为主，机构痕迹集中在少数题材龙头，交叉验证后不改变主线判断。',
    '上榜个股里首板人气股占比偏高，这类标的的次日溢价需要打折看待。',
  ],
  leader: [
    '空间高度从 3 板抬到 5 板且梯队无断层，龙头位置一周内没有易主。',
    '补涨方向本周未出现，说明主线仍在吸筹阶段而非扩散末期。',
  ],
}

const HEADLINE = [
  '5 个交易日全部有数据，情绪重心逐日上移，周五创本周新高。',
  '连板高度 3 板 → 5 板，峰值 5 板，全周梯队无断层。',
  '主线题材以人工智能、机器人连续五天入窗，半导体随后。',
]

function weeklySections(variant: WeeklyDemoVariant) {
  return SECTION_TITLES.map(([name, title]) => {
    const degraded = variant === 'partial' && name === 'dragon_tiger'
    const failed = variant === 'partial' && name === 'leader'
    return {
      name,
      title,
      status: failed ? 'failed' : degraded ? 'degraded' : 'ok',
      mode: degraded ? 'template' : failed ? null : 'structured',
      model: failed || degraded ? null : DEMO_MODEL,
      degraded,
      narrative: failed
        ? []
        : degraded
          ? [`${title}：本周该段 AI 叙述未生成，以下要点由程序从事实包直出。`]
          : SECTION_NARRATIVE[name],
      notes: degraded ? ['模板降级：本段由程序从事实包直出要点。'] : [],
      metrics: { week: DEMO_WEEK },
    }
  })
}

function trend(first: number, last: number, values: number[]) {
  const round = (value: number) => Math.round(value * 10) / 10
  const avg = round(values.reduce((sum, item) => sum + item, 0) / values.length)
  return { first, last, change: round(last - first), avg, samples: values.length }
}

function sentimentFacts() {
  return {
    series: DAILY.map((day) => ({
      date: day.date,
      available: true,
      temperature: day.temperature,
      promotion_rate: day.promotion,
      seal_rate: day.seal,
      limit_up_count: day.limit,
      broken_count: day.broken,
      max_board: day.height,
      gap_levels: [],
      reason: null,
      source: 'artifact',
    })),
    temperature: trend(
      DAILY[0].temperature,
      DAILY[DAILY.length - 1].temperature,
      DAILY.map((day) => day.temperature),
    ),
    promotion_rate: trend(
      DAILY[0].promotion,
      DAILY[DAILY.length - 1].promotion,
      DAILY.map((day) => day.promotion),
    ),
    seal_rate: trend(
      DAILY[0].seal,
      DAILY[DAILY.length - 1].seal,
      DAILY.map((day) => day.seal),
    ),
    limit_up_total: DAILY.reduce((sum, day) => sum + day.limit, 0),
    broken_total: DAILY.reduce((sum, day) => sum + day.broken, 0),
    gap_days: 0,
  }
}

function facts(variant: WeeklyDemoVariant): WeeklyFacts {
  const partial = variant === 'partial'
  const total = BREADTH_SAMPLES
  return {
    week: DEMO_WEEK,
    range: { start: DEMO_WEEK_START, end: DEMO_WEEK_END },
    session_days: SESSION_DAYS,
    missing_days: partial
      ? [{ date: '2026-10-31', reason: '该日非交易日，事实包不计入周度样本', has_report: false }]
      : [],
    available: true,
    reason: null,
    sentiment: sentimentFacts(),
    ladder: {
      series: DAILY.map((day) => ({
        date: day.date,
        available: true,
        height: day.height,
        count: day.limit,
        gap: false,
        gap_levels: [],
        unknown_board: 0,
        reason: null,
      })),
      height: trend(
        DAILY[0].height,
        DAILY[DAILY.length - 1].height,
        DAILY.map((day) => day.height),
      ),
      peak_height: 5,
      count_total: DAILY.reduce((sum, day) => sum + day.limit, 0),
      gap_days: 0,
      leaders: LEADERS,
    },
    breadth: partial
      ? {
          available: false,
          reason: 'daily_k 本周仅落 3 个交易日，样本不足，事实包不给出涨跌分布。',
          distribution: [],
          up_count: 0,
          down_count: 0,
          flat_count: 0,
          samples: 0,
          up_share_pct: null,
        }
      : {
          available: true,
          reason: null,
          distribution: DISTRIBUTION.map(([band, count]) => ({
            band,
            count,
            share_pct: Math.round((count * 1000) / total) / 10,
          })),
          up_count: BREADTH_UP,
          down_count: total - BREADTH_UP - 96,
          flat_count: 96,
          samples: total,
          up_share_pct: Math.round((BREADTH_UP * 1000) / total) / 10,
        },
    themes: {
      available: true,
      reason: null,
      window: 10,
      observed: partial ? 24 : 37,
      themes: partial ? THEMES.slice(0, 2) : THEMES,
    },
    validation: {
      available: true,
      reason: null,
      conditions: 12,
      settled: 9,
      passed: 6,
      failed: 2,
      unknown: 1,
      pending: 3,
      pass_rate: 66.7,
      items: VALIDATION_ITEMS,
    },
    ai_coverage: {
      days_with_checkpoint: 5,
      sections_expected: 25,
      sections_done: partial ? 21 : 23,
      sections_degraded: partial ? 3 : 1,
      sections_failed: partial ? 1 : 1,
      sections_pending: 0,
    },
    caliber: CALIBER,
    source_detail: `limit_pool: duckdb（周末日 2026-10-30 共 47 条）；daily_k: duckdb（${total} 条）；concept_board: duckdb（${THEMES.length} 个题材）`,
  }
}

function weeklyEnvelope(variant: WeeklyDemoVariant, week: string): LadderEnvelope<WeeklyPayload> {
  if (variant === 'empty') {
    // 外壳口径同 `/api/weekly` 无产物分支：200 + `data_source_status=missing`，不 404
    return {
      domain: 'review',
      topic: 'weekly_report',
      event_id: null,
      data_as_of: null,
      payload: { week, weekly: null, data_source_status: 'missing' },
      source: 'ladderstock',
      source_detail: `local weekly cache: ${week}.json 不存在`,
      fetched_at: DEMO_FETCHED_AT,
      freshness: 'cached',
      confidence: 'low',
      source_status: 'partial',
    }
  }
  const partial = variant === 'partial'
  return {
    domain: 'review',
    topic: 'weekly_report',
    event_id: null,
    data_as_of: DEMO_WEEK_END,
    payload: {
      week,
      weekly: {
        week,
        title: `${week} 周度复盘`,
        range: { start: DEMO_WEEK_START, end: DEMO_WEEK_END },
        generated_at: '2026-11-01T18:00:00+08:00',
        available: true,
        reason: null,
        headline: HEADLINE,
        ai: { model: DEMO_MODEL, degraded: partial },
        checkpoint: `data/state/weekly_checkpoint/${week}.json`,
        sections: weeklySections(variant),
        ai_failures: partial
          ? [
              {
                section: 'leader',
                kind: 'quota',
                message: '额度或频率受限：HTTP 429',
                action: '切换备用模型，或稍后重试',
                retryable: true,
              },
            ]
          : [],
        facts: facts(variant),
        footer: '演示数据：本周报不是真实行情，仅用于页面结构与状态走查。',
      },
      data_source_status: 'ok',
    },
    source: 'ladderstock',
    source_detail: `local weekly cache: ${week}.json`,
    fetched_at: DEMO_FETCHED_AT,
    freshness: 'cached',
    confidence: partial ? 'low' : 'medium',
    // §5.2：段与段缺项 → `partial`（danger）；整段走模板直出 → `degraded`（warn）
    source_status: partial ? 'partial' : 'ok',
  }
}

export function demoWeeklySource(variant: WeeklyDemoVariant): WeeklySource {
  // 演示周报只有 2026-W44 这一份：切「上周 / 下周」也返回同一份事实，
  // 页面按载荷自己的 `week` 显示（见 WeeklyScreen 的 displayWeek），不会出现「标题 W45 / 正文 W44」的错位。
  return {
    load: async () => weeklyEnvelope(variant, DEMO_WEEK),
  }
}
