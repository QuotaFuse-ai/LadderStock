/**
 * 持仓页的纯函数：成本法偏好、流水表单口径、流水明细文案。
 *
 * 校验与后端 `journal/portfolio.py:trade_write_fields` 同一条：
 * - 代码归一成裸 6 位 A 股，B 股（900 / 200 开头）明确拒收；
 * - 日期必须是日历上真实存在的 `YYYY-MM-DD`；
 * - 数量 / 成交价 / 到账金额 / 拆股比例 > 0，费用 ≥ 0，期初每股成本可负；
 * - 只提交该类型需要的数字字段（多传一个后端就 400）。
 *
 * 🔴 提交字段名用 `trade_date`（写接口白名单），读回来的 `ledger[]` 里叫 `date`。
 */

import type { TradeInput, TradeType, PortfolioTrade, PortfolioMethod } from '../../api/radar'

/** 成本法偏好（只记在本机浏览器；存储不可用时用移动加权平均）。 */
export const COST_METHOD_KEY = 'ldst.portfolio.method'

export const TRADE_TYPES: readonly TradeType[] = ['buy', 'sell', 'dividend', 'split', 'opening']

export const TRADE_TYPE_LABELS: Record<TradeType, string> = {
  buy: '买入',
  sell: '卖出',
  dividend: '现金分红',
  split: '送转 / 拆股',
  opening: '期初持仓',
}

export type NumericField = 'shares' | 'price' | 'fee' | 'amount' | 'ratio'

export const FIELDS_OF: Record<TradeType, readonly NumericField[]> = {
  buy: ['shares', 'price', 'fee'],
  sell: ['shares', 'price', 'fee'],
  dividend: ['amount'],
  split: ['ratio'],
  opening: ['shares', 'price'],
}

export const FIELD_LABEL: Record<TradeType, Partial<Record<NumericField, readonly [string, string]>>> = {
  buy: {
    shares: ['数量（股）', '如 100'],
    price: ['成交价', '如 12.5'],
    fee: ['费用（选填）', '佣金、税费合计'],
  },
  sell: {
    shares: ['数量（股）', '如 100'],
    price: ['成交价', '如 13.2'],
    fee: ['费用（选填）', '佣金、税费合计'],
  },
  dividend: { amount: ['到账金额（税后）', '如 320'] },
  split: { ratio: ['每 1 股变成几股', '10 转 5 填 1.5'] },
  opening: {
    shares: ['数量（股）', '如 100'],
    price: ['每股成本（可负）', '如 12.5'],
  },
}

export const TYPE_HINT: Record<TradeType, string> = {
  buy: '费用（佣金、印花税、过户费等）计入买入成本。只记已经发生的交易，不下单。',
  sell: '卖出所得扣掉费用后算已实现盈亏，成本按上面选的成本法算。',
  dividend: '记账户实际到账的金额（税后）。分红单独算收入，不改持仓成本。',
  split: '送股、转增、拆股、合股都记这里：10 送 3 转 2 填 1.5，1 拆 4 填 4，10 合 1 填 0.1。',
  opening: '开始记流水之前就有的持仓：写当时的数量和每股成本，日期写建仓日（不记得就写开始记账那天）。',
}

export function readCostMethod(): PortfolioMethod {
  try {
    return window.localStorage.getItem(COST_METHOD_KEY) === 'fifo' ? 'fifo' : 'average'
  } catch {
    return 'average'
  }
}

export function saveCostMethod(method: PortfolioMethod): void {
  try {
    window.localStorage.setItem(COST_METHOD_KEY, method)
  } catch {
    // 记不住就每次用默认，不影响本次选择
  }
}

export interface TradeForm {
  type: TradeType
  symbol: string
  name: string
  date: string
  shares: string
  price: string
  fee: string
  amount: string
  ratio: string
  note: string
}

export function emptyTradeForm(type: TradeType = 'buy'): TradeForm {
  return {
    type,
    symbol: '',
    name: '',
    date: '',
    shares: '',
    price: '',
    fee: '',
    amount: '',
    ratio: '',
    note: '',
  }
}

const DAY = /^\d{4}-\d{2}-\d{2}$/
const B_SHARE = /^(?:900|200)\d{3}$/

export function isRealDay(value: string): boolean {
  const text = value.trim()
  if (!DAY.test(text)) return false
  const stamp = Date.parse(`${text}T00:00:00Z`)
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === text
}

/** `sh600519` / `600519.SH` / `SH600519` → 裸 6 位；认不出返回空串。 */
export function bareCode(value: string): string {
  return value
    .trim()
    .toUpperCase()
    .replace(/^(SH|SZ|BJ)[.:]?/, '')
    .replace(/\.(SH|SZ|BJ)$/, '')
}

/** 空串 = 没填（null）；填了但不是数 = NaN（报错），不当 0。 */
function numOrNull(value: string): number | null {
  const text = value.trim()
  if (text === '') return null
  return Number(text)
}

const positive = (value: number | null): value is number =>
  value !== null && Number.isFinite(value) && value > 0

export type TradeRecord = { record: TradeInput } | { error: string }

/** 表单 → 请求体；与后端同一套检查（后端仍会再查一遍）。 */
export function tradeRecord(form: TradeForm): TradeRecord {
  const symbol = bareCode(form.symbol)
  if (!/^\d{6}$/.test(symbol)) {
    return { error: '代码要 6 位数字（可带 sh / sz / bj 前缀）' }
  }
  if (B_SHARE.test(symbol)) {
    return { error: 'B 股（900、200 开头）暂不支持：按美元、港元计价，并进人民币合计会算错' }
  }
  if (!isRealDay(form.date)) {
    return { error: '日期要是真实存在的日期，格式 YYYY-MM-DD' }
  }

  const values: Record<NumericField, number | null> = {
    shares: numOrNull(form.shares),
    price: numOrNull(form.price),
    fee: numOrNull(form.fee),
    amount: numOrNull(form.amount),
    ratio: numOrNull(form.ratio),
  }
  const label = TRADE_TYPE_LABELS[form.type]

  if (form.type === 'opening') {
    if (!positive(values.shares)) return { error: '期初持仓要写大于 0 的数量' }
    if (values.price === null || !Number.isFinite(values.price)) {
      return { error: '期初持仓要写每股成本（可以是负数）' }
    }
  } else if (form.type === 'buy' || form.type === 'sell') {
    if (!positive(values.shares)) return { error: `${label}要写大于 0 的数量` }
    if (!positive(values.price)) return { error: `${label}要写大于 0 的成交价` }
    if (values.fee !== null && !(Number.isFinite(values.fee) && values.fee >= 0)) {
      return { error: '费用不能是负数（没有就留空）' }
    }
  } else if (form.type === 'dividend') {
    if (!positive(values.amount)) return { error: '现金分红要写大于 0 的到账金额' }
  } else if (!positive(values.ratio)) {
    return { error: '送转拆股要写大于 0 的比例（每 1 股变成几股，如 10 转 5 写 1.5）' }
  }

  const numerics: Partial<Record<NumericField, number>> = {}
  for (const key of FIELDS_OF[form.type]) {
    const value = values[key]
    if (value !== null) numerics[key] = value
  }
  const record: TradeInput = {
    symbol,
    type: form.type,
    trade_date: form.date.trim(),
    ...numerics,
  }
  const name = form.name.trim().slice(0, 40)
  if (name) record.name = name
  const note = form.note.trim().slice(0, 1000)
  if (note) record.note = note
  return { record }
}

/** 流水一行里的数字说明（交易流水表用）。 */
export function tradeDetail(trade: PortfolioTrade): string {
  switch (trade.type) {
    case 'buy':
    case 'sell':
      return `${trade.shares ?? '?'} 股 × ${trade.price ?? '?'}${trade.fee ? `，费用 ${trade.fee}` : ''}`
    case 'opening':
      return `${trade.shares ?? '?'} 股，每股成本 ${trade.price ?? '?'}`
    case 'dividend':
      return `到账 ${trade.amount ?? '?'}`
    case 'split':
      return `每 1 股变 ${trade.ratio ?? '?'} 股`
    default:
      return ''
  }
}

export function tradeTypeLabel(type: string): string {
  return TRADE_TYPE_LABELS[type as TradeType] ?? type
}
