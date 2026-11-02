/**
 * 明日验证（`/validate`）演示数据（`?demo=1` 或「设置 → 虚拟数据」开启，页面带「演示数据」徽标）。
 *
 * 形状与 `GET /api/validation` 逐键对齐：外壳是 `core/models.py:LadderEnvelope`
 * （`web/server.py:_envelope()`），载荷是 `{trade_date, conditions, reconcile, data_source_status}`，
 * 条件行只有 `{code, trigger, note}` 三键（`web/server.py:_validation_conditions()` 的投影），
 * 逐条判定行只有 `{id, text, metric, operator, target, actual, passed}`（`core/validation.py:reconcile()`）。
 * 投影看不到的字段（盘上的 `metric` / 自由文本 `note`）这里一律不出现。
 *
 * 演示基准与其余屏同一套事实：交易日 `2026-10-30`，取数 `2026-10-30T15:00:04+08:00`，
 * 当日情绪温度 68.0、涨停 47 家、最高板 5 板（同 `limitup/demo.ts`）。
 * 温度可复算：`0.45×51.1 + 0.35×81.0 + 0.20×(5/6×100) = 68.0`
 * （封板率 47/(47+11)=81.0，晋级率 23/45=51.1，口径同 `core/sentiment.py`）。
 * 下一交易日 2026-11-02（10-30 是周五，周末无盘）：涨停 41 = 26+9+4+1+1、炸板 15
 * → 封板率 41/56=73.2，昨日涨停 47 家中晋级 15 家 → 晋级率 31.9，最高板 5
 * → 温度 `0.45×31.9 + 0.35×73.2 + 0.20×83.3 = 56.6`。
 *
 * 条件阈值全部按 `core/validation.py:_threshold()` 的口径由当日指标回推
 * （`max(floor, 当日值 − 15)`，情绪地板 30 / 晋级地板 10 / 封板地板 40 / 高度地板 `max(2, 高度−1)`），
 * 所以「明日条件」这一份是程序能写出来的形状，不是随手编的文案。
 *
 * **绝不在真实通道里回落到这份数据**：真实请求 404 / 断连时页面进空态 / 离线态。
 * 演示态不写控制面（`PUT /api/control/validation/conditions`），也不读写本机草稿。
 */

import type { LadderEnvelope, ValidationPayload } from '../../api'

export type ValidationDemoVariant = 'full' | 'partial' | 'empty'

export const DEMO_VALIDATION_DATE = '2026-10-30'
export const DEMO_VALIDATION_NEXT_SESSION = '2026-11-02'
export const DEMO_VALIDATION_FETCHED_AT = '2026-10-30T15:00:04+08:00'
/** `core/validation.py:_now_iso()` 写的是 UTC ISO（秒级）：15:30:12 +08:00 = 07:30:12+00:00。 */
const GENERATED_AT = '2026-11-02T07:30:12+00:00'

/** 读投影后的条件行（`ValidationCondition`）：`code` / `trigger` 缺失时后端给 `null`，不是空串。 */
interface DemoCondition {
  code: string | null
  trigger: string | null
  note: string | null
}

/** `reconcile.results[]` 行；`note` 由这里反推，口径见 `web/server.py:_validation_conditions()`。 */
interface DemoReconcileRow {
  id: string
  text: string
  metric: string | null
  operator: string | null
  target: number | null
  actual: number | null
  passed: boolean | null
}

/**
 * 四条程序条件（`build_conditions()` 的全部产出：当日梯队 1–5 板无断层，
 * 所以 `gap_fill` 这一条不会出现）。判定值取 2026-11-02 的真实指标。
 */
const PROGRAM_ROWS: DemoReconcileRow[] = [
  {
    id: 'temperature_floor',
    text: '明日情绪温度不低于 53.0（今日 68.0）',
    metric: 'temperature',
    operator: '>=',
    target: 53.0,
    actual: 56.6,
    passed: true,
  },
  {
    id: 'promotion_floor',
    text: '明日晋级率不低于 36.1%（今日 51.1%）',
    metric: 'promotion_rate',
    operator: '>=',
    target: 36.1,
    actual: 31.9,
    passed: false,
  },
  {
    id: 'seal_floor',
    text: '明日封板率不低于 66.0%（今日 81.0%）',
    metric: 'seal_rate',
    operator: '>=',
    target: 66.0,
    actual: 73.2,
    passed: true,
  },
  {
    id: 'height_floor',
    text: '明日最高板不低于 4 板（今日 5 板）',
    metric: 'max_board',
    operator: '>=',
    target: 4,
    actual: 5,
    passed: true,
  },
]

/**
 * 人自己写进后端的 8 条条件（`PUT /api/control/validation/conditions` 只落 `{id, text, code?, note?}`，
 * **没有 `metric` / `operator` / `value`**）。`reconcile()` 对没有 `metric` 的条件不产出可判定行，
 * 所以它们不在 `results` 里、读投影里 `note` 也就是 `null`（界面显示「—」）——
 * 这是后端的现状，不是页面漏显示，演示夹具如实保留这个缺口。
 */
const FREE_TEXT_CONDITIONS: DemoCondition[] = [
  { code: '002594', trigger: '竞价高开 3% 以上且量比大于 2，回踩不破昨收' },
  { code: '300750', trigger: '锂电池板块涨停家数不低于 3 家时跟随' },
  { code: '688981', trigger: '半导体板块开盘 30 分钟内封板家数大于等于 2' },
  { code: '600000', trigger: '银行板块指数涨幅大于 0.8% 时不追高' },
  { code: null, trigger: '跌停家数不少于 10 家则当日不开新仓' },
  { code: null, trigger: '炸板率高于 40% 时把仓位上限压到三成' },
  { code: '600519', trigger: '高价股尾盘 14:30 后拉升不追' },
  { code: null, trigger: '题材梯队断层重新出现 3 板断档则减仓' },
].map((item) => ({ ...item, note: null }))

/** `note` 由对账结论反推：`reconcile.results[].passed` → 通过 / 未通过 / 无法判定。 */
function verdictNote(row: DemoReconcileRow): string {
  if (row.passed === true) return '对账：通过'
  if (row.passed === false) return '对账：未通过'
  return '对账：无法判定'
}

/** 程序条件行同样走 `{code, trigger, note}` 投影：`code` 恒为 `null`（源文件没有标的代码）。 */
const PROGRAM_CONDITIONS: DemoCondition[] = PROGRAM_ROWS.map((row) => ({
  code: null,
  trigger: row.text,
  note: verdictNote(row),
}))

const FULL: LadderEnvelope<ValidationPayload> = {
  domain: 'validation',
  topic: 'validation',
  event_id: null,
  data_as_of: DEMO_VALIDATION_DATE,
  payload: {
    trade_date: DEMO_VALIDATION_DATE,
    // 表体顺序 = 程序条件在前、人写的在后（`merge` 模式就是追加语义）。
    conditions: [...PROGRAM_CONDITIONS, ...FREE_TEXT_CONDITIONS],
    reconcile: {
      status: 'ok',
      reason: null,
      next_session: DEMO_VALIDATION_NEXT_SESSION,
      checked: PROGRAM_ROWS.length,
      passed: PROGRAM_ROWS.filter((row) => row.passed === true).length,
      failed: PROGRAM_ROWS.filter((row) => row.passed === false).length,
      unknown: 0,
      results: PROGRAM_ROWS,
      generated_at: GENERATED_AT,
    },
    data_source_status: 'ok',
  },
  source: 'ladderstock',
  source_detail: `local validation cache: ${DEMO_VALIDATION_DATE}.json；对账 ok（下一交易日 ${DEMO_VALIDATION_NEXT_SESSION}）`,
  fetched_at: DEMO_VALIDATION_FETCHED_AT,
  freshness: 'cached',
  confidence: 'medium',
  source_status: 'ok',
}

/**
 * partial = **CLI 退出码 8** 那一档：条件在，下一交易日的涨停池还没同步，
 * `reconcile()` 直接给空 `results` + 说明原因（`core/validation.py:reconcile()` 的两条早退分支）。
 * 黄条要读的「缺哪个数据集」就在 `reason` 里（涨停池 / `ldst sync`），界面不替它下结论。
 */
const PARTIAL: LadderEnvelope<ValidationPayload> = {
  ...FULL,
  payload: {
    trade_date: DEMO_VALIDATION_DATE,
    // 条件照常展示（对账没跑成不代表条件消失），但 `note` 全部没有结论。
    conditions: [
      ...PROGRAM_ROWS.map((row) => ({ code: null, trigger: row.text, note: null })),
      ...FREE_TEXT_CONDITIONS,
    ],
    reconcile: {
      status: 'partial',
      reason: '本地还没有下一交易日的涨停池数据，无法对账',
      next_session: null,
      results: [],
      generated_at: GENERATED_AT,
    },
    data_source_status: 'ok',
  },
  source_detail: `local validation cache: ${DEMO_VALIDATION_DATE}.json；对账 partial`,
}

/** empty = 这一天没有 validation 文件：`_envelope(available=false)` → `data_as_of=null` + `missing`。 */
const EMPTY: LadderEnvelope<ValidationPayload> = {
  domain: 'validation',
  topic: 'validation',
  event_id: null,
  data_as_of: null,
  payload: {
    trade_date: DEMO_VALIDATION_DATE,
    conditions: [],
    reconcile: null,
    data_source_status: 'missing',
  },
  source: 'ladderstock',
  source_detail: `local validation cache 不存在：${DEMO_VALIDATION_DATE}.json（先运行 ldst review --date ${DEMO_VALIDATION_DATE}）`,
  fetched_at: DEMO_VALIDATION_FETCHED_AT,
  freshness: 'cached',
  confidence: 'low',
  source_status: 'partial',
}

const BY_VARIANT: Record<ValidationDemoVariant, LadderEnvelope<ValidationPayload>> = {
  full: FULL,
  partial: PARTIAL,
  empty: EMPTY,
}

export function demoValidationEnvelope(variant: ValidationDemoVariant): LadderEnvelope<ValidationPayload> {
  return BY_VARIANT[variant]
}

/** 与 `api.validation(date, signal)` 同构：屏侧只做一件事 `demo ? 演示 : 真实`。 */
export function demoValidationSource(variant: ValidationDemoVariant) {
  return {
    load: async (_date: string, _signal?: AbortSignal): Promise<LadderEnvelope<ValidationPayload>> =>
      demoValidationEnvelope(variant),
  }
}
