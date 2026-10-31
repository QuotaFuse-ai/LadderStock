import type { StageDocument } from '../../api/research'
import { MISSING, formatMoney } from '../../lib/format'

/**
 * 研究 `facts` 的展示层。
 *
 * `facts` 是后端注入的**无类型字典**（DEC-025）：页面只做「已知键 → 中文标签 + 格式化」，
 * 未知键原样显示键名，不猜单位、不补数值、不重算比率。
 */

const LABELS: Record<string, string> = {
  name: '公司名称',
  industry: '所属行业',
  main_business: '主营业务',
  controlling_shareholder: '控股股东',
  listed_board: '上市板块',
  employees: '员工数',
  revenue: '营业收入',
  revenue_yoy: '营收同比',
  revenue_series: '营业收入序列',
  net_profit: '归母净利润',
  net_profit_yoy: '净利润同比',
  net_profit_series: '归母净利润序列',
  roe: 'ROE',
  eps_2026: '2026E EPS',
  eps_2027: '2027E EPS',
  net_profit_growth: '净利润增速',
  institution_count: '覆盖机构数',
  pe: 'PE',
  pb: 'PB',
  drawdown_60d: '近 60 日最大回撤',
  pledge_ratio: '质押比例',
  bad_loan_ratio: '不良率',
  pe_percentile_5y: 'PE 近五年分位',
  pb_percentile_5y: 'PB 近五年分位',
  pe_percentile: 'PE 分位',
  pb_percentile: 'PB 分位',
  valuation_percentile: '估值分位',
}

/** 由其他区块专门呈现、不在通用列表里重复的键。 */
const HIDDEN = new Set([
  'basis',
  'basis_echo',
  'default_basis',
  'available_bases',
  'pe_reason',
  'pb_reason',
  'roe_reason',
  'quarter_basis',
  'upstream',
  'risks',
])

export const MONEY = new Set(['revenue', 'net_profit'])
const PERCENT = new Set([
  'revenue_yoy',
  'net_profit_yoy',
  'roe',
  'net_profit_growth',
  'drawdown_60d',
  'pledge_ratio',
  'bad_loan_ratio',
])

export function factLabel(key: string): string {
  return LABELS[key] ?? key
}

function trimNumber(value: number, digits: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(digits).replace(/\.?0+$/, '')
}

export function formatFactValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return MISSING
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string') return value
  if (typeof value !== 'number' || !Number.isFinite(value)) return MISSING
  if (MONEY.has(key)) return formatMoney(value)
  if (PERCENT.has(key)) return `${trimNumber(value, 2)}%`
  if (key === 'employees' || key === 'institution_count') return value.toLocaleString('zh-CN')
  if (Number.isInteger(value)) return value.toLocaleString('zh-CN')
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function factUnit(key: string): string | null {
  if (MONEY.has(key)) return null
  if (PERCENT.has(key)) return null
  if (key === 'employees') return '人'
  if (key === 'institution_count') return '家'
  if (key === 'eps_2026' || key === 'eps_2027') return '元'
  return null
}

export interface ScalarFact {
  key: string
  label: string
  value: string
  unit: string | null
}

/** 标量键值（字符串 / 数字 / 布尔），排除已知容器键与所有 `*_series`。 */
export function scalarFacts(facts: Record<string, unknown>): ScalarFact[] {
  return Object.entries(facts).flatMap(([key, value]) => {
    if (HIDDEN.has(key) || key.endsWith('_series')) return []
    if (!['string', 'number', 'boolean'].includes(typeof value)) return []
    return [{ key, label: factLabel(key), value: formatFactValue(key, value), unit: factUnit(key) }]
  })
}

export function stringFact(facts: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = facts[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  }
  return null
}

export function numberFact(facts: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = facts[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

export interface SeriesFact {
  key: string
  label: string
  values: number[]
}

/** `*_series`：只认「全部是有限数字且至少两个点」的数组，否则不画。 */
export function seriesFacts(facts: Record<string, unknown>): SeriesFact[] {
  return Object.entries(facts).flatMap(([key, value]) => {
    if (!key.endsWith('_series') || !Array.isArray(value)) return []
    if (value.length < 2) return []
    if (!value.every((item): item is number => typeof item === 'number' && Number.isFinite(item))) return []
    return [{ key, label: factLabel(key), values: value }]
  })
}

export interface PercentileFact {
  key: string
  label: string
  value: number
}

/** 分位：只展示 `facts` 里现成的 0–100 数字，不由页面计算。 */
export function percentileFacts(facts: Record<string, unknown>): PercentileFact[] {
  return Object.entries(facts).flatMap(([key, value]) => {
    if (!/percentile|分位/i.test(key)) return []
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100) return []
    return [{ key, label: factLabel(key), value }]
  })
}

/** `ai.risks` 可能是字符串数组或对象数组；只提取可读文本，不造严重度。 */
export function riskItems(ai: Record<string, unknown> | undefined): string[] {
  const raw = ai?.risks
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item === 'string') return item.trim() ? [item.trim()] : []
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      for (const key of ['title', 'description', 'message', 'text']) {
        const value = record[key]
        if (typeof value === 'string' && value.trim()) return [value.trim()]
      }
    }
    return []
  })
}

/** 结构字段之外、仍然值得读的 AI 文本（报告要点等）。 */
export function textList(ai: Record<string, unknown> | undefined, key: string): string[] {
  const raw = ai?.[key]
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is string => typeof item === 'string' && item.trim() !== '')
}

/**
 * 优先用磁盘上的 `<stage>.md`；缺失时用同一份 document 的叙述 + 备注拼一个等价视图。
 * 只做排版，不改写文案、不补数字。
 */
export function artifactMarkdown(document: StageDocument, markdown: string): string {
  if (markdown.trim()) return markdown
  const lines = [...document.narrative]
  if (document.notes.length > 0) {
    lines.push('', '## 备注', '', ...document.notes.map((note) => `- ${note}`))
  }
  return lines.join('\n\n')
}
