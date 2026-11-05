/**
 * 持仓页的展示口径：缺失一律 `—`，涨跌只走 `--ldst-up` / `--ldst-down`（红涨绿跌）。
 *
 * 收益率接口（`/api/portfolio/perf`）给的是小数（`0.0123` = +1.23%），
 * 持仓接口的 `pnl_pct` / `change_pct` 已经是百分数——两种口径分开格式化，不混用。
 */

import { MISSING, formatAmount, formatSignedAmount, isFiniteNumber, type Tone } from '../../lib/format'

export const TONE_CLASS: Record<Tone, string> = {
  up: 'text-up',
  down: 'text-down',
  flat: 'text-text-secondary',
}

export function toneOf(value: number | null | undefined): Tone {
  if (!isFiniteNumber(value)) return 'flat'
  return value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
}

/** 股数 / 数量：最多两位小数、带千分位，缺失 `—`。 */
export function plain(value: number | null | undefined, digits = 2): string {
  if (!isFiniteNumber(value)) return MISSING
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

/** 单价：最多 4 位小数（ETF / 基金常见 3–4 位，截成 2 位会和市值对不上）。 */
export function priceText(value: number | null | undefined): string {
  return plain(value, 4)
}

/** 金额：两位小数 + 千分位，缺失 `—`。 */
export function moneyText(value: number | null | undefined): string {
  return formatAmount(value)
}

/** 带方向的金额：`+1,234.56` / `-12.00`；`0` 是平不是缺。 */
export function signedMoney(value: number | null | undefined): string {
  return formatSignedAmount(value).text
}

/** 小数 → 百分比（`0.0123` → `+1.23%`）；缺失 `—`。 */
export function pctOfDecimal(value: number | null | undefined, digits = 2): { text: string; tone: Tone } {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat' }
  const rounded = Number((value * 100).toFixed(digits))
  return { text: `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}%`, tone: toneOf(rounded) }
}

/** 已经是百分数的值（`12.34` → `+12.34%`）；缺失 `—`。 */
export function pctSignedText(value: number | null | undefined, digits = 2): { text: string; tone: Tone } {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat' }
  const rounded = Number(value.toFixed(digits))
  return { text: `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}%`, tone: toneOf(rounded) }
}
