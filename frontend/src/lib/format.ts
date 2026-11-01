/**
 * 数字与文案格式（DESIGN-SPEC §10 / §7.1）。
 *
 * 硬规则：
 * - 缺失值一律 `—` + 原因，**绝不显示 0**（0 是有效数值）。
 * - 涨跌红涨绿跌：`+4.21%` ↑ / `-1.08%` ↓ / `0.00%` 无箭头。
 * - 颜色只作用于数字与箭头，不染整行整卡。
 */

export const MISSING = '—'

export type Tone = 'up' | 'down' | 'flat'

export interface SignedValue {
  /** 展示文本，如 `+4.21%`；缺失时为 `—` */
  text: string
  tone: Tone
  /** 方向箭头；零值/缺失为空串 */
  arrow: string
  missing: boolean
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** 涨跌幅：两位小数 + 正负号 + % */
export function formatChangePct(value: number | null | undefined): SignedValue {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat', arrow: '', missing: true }
  const rounded = Number(value.toFixed(2))
  const tone: Tone = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
  const sign = rounded > 0 ? '+' : ''
  return {
    text: `${sign}${rounded.toFixed(2)}%`,
    tone,
    arrow: tone === 'up' ? '↑' : tone === 'down' ? '↓' : '',
    missing: false,
  }
}

/** 任意带方向的数值（如「较前一日 +6」）：整数 + 正负号 */
export function formatSignedInt(value: number | null | undefined, unit = ''): SignedValue {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat', arrow: '', missing: true }
  const rounded = Math.round(value)
  const tone: Tone = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
  const sign = rounded > 0 ? '+' : ''
  return {
    text: `${sign}${rounded}${unit}`,
    tone,
    arrow: tone === 'up' ? '↑' : tone === 'down' ? '↓' : '',
    missing: false,
  }
}

/** 整数（情绪温度、涨停家数、断层档位） */
export function formatInt(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING
  return String(Math.round(value))
}

/**
 * 计数缺失口径（§8 / QUOT-320 缺口 4）：`null` / `undefined` / 非有限数 → `—`。
 * `0` 是真实读数，不能和「没取到」显示成同一个东西，屏侧也不许再写 `count ?? 0`。
 */
export function countOrMissing(value: number | null | undefined): string {
  return isFiniteNumber(value) ? String(value) : MISSING
}

/** 一位小数（晋级率、题材强度） */
export function formatOneDecimal(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING
  return value.toFixed(1)
}

/** 百分比，一位小数：`38.5%` */
export function formatRatePct(value: number | null | undefined): SignedValue {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat', arrow: '', missing: true }
  return { text: `${value.toFixed(1)}%`, tone: 'flat', arrow: '', missing: false }
}

/** 连板数：`7 板`（中文量词前有空格） */
export function formatBoards(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING
  return `${Math.round(value)} 板`
}

/** 金额：两位小数 + 千分位 + 万/亿 */
export function formatMoney(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 万`
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** 时间：当日 `HH:mm:ss`，跨日 `MM-DD HH:mm`（本地时区） */
export function formatTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return MISSING
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 日期：`YYYY-MM-DD` */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return MISSING
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 本地日期串（`YYYY-MM-DD`），用于「今天」这类展示 */
export function localDateIso(now = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** 补零的交易日 `MM-DD`（图表 X 轴） */
export function shortDate(iso: string): string {
  const parts = iso.split('-')
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso
}

export const TONE_TEXT_CLASS: Record<Tone, string> = {
  up: 'text-up',
  down: 'text-down',
  flat: 'text-text-secondary',
}

export const TONE_BG_CLASS: Record<Tone, string> = {
  up: 'bg-up-soft text-up',
  down: 'bg-down-soft text-down',
  flat: 'bg-flat-soft text-flat',
}
