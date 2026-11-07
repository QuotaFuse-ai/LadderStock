/**
 * 从表格导入流水（QUOT-437）：CSV / 表格软件复制来的文本 → 一笔笔流水请求体。
 *
 * **全部在本机解析**：解析、类型换算、数字清洗都不离开浏览器，后端只收到和「记一笔」
 * 同一种形状的 `records`。不引第三方 CSV 库（口径要能自己说清，包体积也是预算）。
 *
 * 四条硬口径：
 * 1. 每一行都过 `tradeRecord`，和手动「记一笔」同一条检查；不合法的行写原因、不导入，
 *    不猜、不补 0、不取绝对值（有的券商导出卖出数量是负的，那是导出的写法，方向要用户自己确认）。
 * 2. **1–5 位纯数字代码一律拒**：表格软件会把 `000001` 存成 `1`。这时分不清是 A 股还是
 *    港股，而本页只做 A 股，猜成 A 股会把别的市场的仓位记进来。
 * 3. 表头按列名认（`日期 / 类型 / 代码` 必填），认不出的列只提示「没用上」，不报错。
 * 4. 一次最多 `CSV_MAX_ROWS` 行；超了在解析阶段就整体退回「分成几份」，不发请求。
 */

import { emptyTradeForm, tradeRecord, TRADE_TYPE_LABELS, type TradeForm } from './portfolioForm'
import type { TradeInput, TradeType } from '../../api/radar'

export const CSV_MAX_ROWS = 1000

type Field = 'date' | 'type' | 'symbol' | 'name' | 'shares' | 'price' | 'fee' | 'amount' | 'ratio' | 'note'

const FIELD_LABEL: Record<Field, string> = {
  date: '日期',
  type: '类型',
  symbol: '代码',
  name: '名称',
  shares: '数量',
  price: '价格',
  fee: '费用',
  amount: '金额',
  ratio: '比例',
  note: '备注',
}

/** 列名别名：比较前去掉空白与括号里的单位，英文不分大小写。 */
const HEADER_ALIASES: Record<Field, readonly string[]> = {
  date: ['日期', '成交日期', '交易日期', '发生日期', 'date'],
  type: ['类型', '操作', '买卖', '买卖标志', '业务名称', 'type'],
  symbol: ['代码', '证券代码', '股票代码', 'symbol', 'code'],
  name: ['名称', '证券名称', '股票名称', 'name'],
  shares: ['数量', '成交数量', '股数', 'shares'],
  price: ['价格', '成交价', '成交价格', '成交均价', '每股成本', 'price'],
  fee: ['费用', '手续费', '佣金', '交易费用', 'fee'],
  amount: ['金额', '到账金额', '分红金额', '发生金额', 'amount'],
  ratio: ['比例', '送转比例', 'ratio'],
  note: ['备注', 'note'],
}

const REQUIRED: readonly Field[] = ['date', 'type', 'symbol']

const TYPE_ALIASES: Record<TradeType, readonly string[]> = {
  buy: ['买入', '买', '证券买入', '基金买入', 'buy'],
  sell: ['卖出', '卖', '证券卖出', '基金卖出', 'sell'],
  dividend: ['现金分红', '分红', '红利', '红利入账', '股息', '股息入账', 'dividend'],
  split: ['送转', '送股', '转增', '拆股', '合股', 'split'],
  opening: ['期初持仓', '期初', 'opening'],
}

/** 归一列名 / 类型值：去空白（含全角空格）、去尾部括号单位、英文转小写。 */
function norm(value: string): string {
  return value.replace(/[\s　]/g, '').replace(/[(（][^)）]*[)）]$/, '').toLowerCase()
}

const HEADER_OF = new Map<string, Field>()
for (const [field, names] of Object.entries(HEADER_ALIASES) as Array<[Field, readonly string[]]>) {
  for (const name of names) HEADER_OF.set(norm(name), field)
}

const TYPE_OF = new Map<string, TradeType>()
for (const [type, names] of Object.entries(TYPE_ALIASES) as Array<[TradeType, readonly string[]]>) {
  for (const name of names) TYPE_OF.set(norm(name), type)
}

export interface CsvRow {
  /** 原文里第几行（从 1 数，含表头）：预览里回指用户看到的行号 */
  line: number
  /** 原样的关键几格，预览列表直接展示 */
  cells: { date: string; type: string; symbol: string; name: string }
  /** 与后端白名单同形的请求体；本机检查没过时是 `null`，原因在 `error` */
  record: TradeInput | null
  error: string | null
}

export type CsvParse =
  | { ok: true; rows: CsvRow[]; columns: Field[]; unused: string[]; delimiter: ',' | '\t' }
  | { ok: false; error: string }

/** 一段表格文本 → 一行行格子（RFC 4180：引号里可以有分隔符和换行，`""` 是一个引号）。 */
export function splitTable(text: string, delimiter: ',' | '\t'): Array<{ line: number; cells: string[] }> {
  const out: Array<{ line: number; cells: string[] }> = []
  let cells: string[] = []
  let cell = ''
  let quoted = false
  let line = 1
  let start = 1
  const end = () => {
    cells.push(cell)
    out.push({ line: start, cells })
    cells = []
    cell = ''
  }
  for (let i = 0; i < text.length; i++) {
    const char = text[i] as string
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        cell += '"'
        i++
      } else if (char === '"') {
        quoted = false
      } else {
        if (char === '\n') line++
        cell += char
      }
    } else if (char === '"' && cell.trim() === '') {
      quoted = true
      cell = ''
    } else if (char === delimiter) {
      cells.push(cell)
      cell = ''
    } else if (char === '\n') {
      end()
      line++
      start = line
    } else {
      cell += char
    }
  }
  if (cell !== '' || cells.length) end()
  return out
}

/** 首个非空行里制表符更多就按制表符（从表格软件复制来的），否则按逗号。 */
function delimiterOf(text: string): ',' | '\t' {
  const first = text.split('\n').find((row) => row.trim() !== '') ?? ''
  const tabs = first.match(/\t/g)?.length ?? 0
  const commas = first.match(/,/g)?.length ?? 0
  return tabs >= commas && tabs > 0 ? '\t' : ','
}

/** `YYYY-MM-DD` / `YYYY/M/D` / `YYYY.M.D` / `YYYYMMDD` / `YYYY年M月D日`；带时间的只取日期。 */
export function normalizeDate(raw: string): string {
  const value = raw.trim().replace(/[T\s].*$/, '')
  let matched = /^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/.exec(value)
  if (!matched) matched = /^(\d{4})(\d{2})(\d{2})$/.exec(value)
  if (!matched) return value
  return `${matched[1]}-${matched[2]!.padStart(2, '0')}-${matched[3]!.padStart(2, '0')}`
}

/** 数字：只去千分位逗号与空格；写错的交给 `tradeRecord` 报错，不当 0。 */
function normalizeNumber(raw: string): string {
  return raw.replace(/[,，\s　]/g, '')
}

/** 表格文本 → 每行一笔流水（或这一行的问题）。表头不对 / 行数超限整体报错。 */
export function parseTradeCsv(input: string): CsvParse {
  const text = input.replace(/^﻿/, '').replace(/\r\n?/g, '\n')
  if (!text.trim()) return { ok: false, error: '没有内容：请粘贴表格，或选一个 CSV 文件' }
  const delimiter = delimiterOf(text)
  const table = splitTable(text, delimiter).filter((row) => row.cells.some((cell) => cell.trim() !== ''))
  const head = table.shift()
  if (!head) return { ok: false, error: '没有内容：请粘贴表格，或选一个 CSV 文件' }

  const column = new Map<Field, number>()
  const unused: string[] = []
  for (const [index, cell] of head.cells.entries()) {
    const field = HEADER_OF.get(norm(cell))
    if (!field) {
      if (cell.trim()) unused.push(cell.trim())
      continue
    }
    if (column.has(field)) {
      return {
        ok: false,
        error: `有两列都是「${FIELD_LABEL[field]}」：第 ${column.get(field)! + 1} 列和第 ${index + 1} 列，请删掉一列`,
      }
    }
    column.set(field, index)
  }

  const missing = REQUIRED.filter((field) => !column.has(field))
  if (missing.length > 0) {
    return {
      ok: false,
      error:
        `第一行要是列名，至少要有${missing.map((field) => `「${FIELD_LABEL[field]}」`).join('、')}这几列` +
        `（认得的写法：${missing.map((field) => HEADER_ALIASES[field].join(' / ')).join(';')}）。` +
        '可以先点「下载模板」照着填',
    }
  }
  if (table.length > CSV_MAX_ROWS) {
    return { ok: false, error: `一次最多 ${CSV_MAX_ROWS} 行，这份有 ${table.length} 行：请分成几份导入` }
  }

  const cellOf = (cells: string[], field: Field) =>
    column.has(field) ? (cells[column.get(field)!] ?? '').trim() : ''

  const rows: CsvRow[] = table.map(({ line, cells }) => {
    const shown = {
      date: cellOf(cells, 'date'),
      type: cellOf(cells, 'type'),
      symbol: cellOf(cells, 'symbol'),
      name: cellOf(cells, 'name'),
    }
    const fail = (error: string): CsvRow => ({ line, cells: shown, record: null, error })

    const type = TYPE_OF.get(norm(shown.type))
    if (!type) {
      return fail(`类型「${shown.type || '空'}」不认识（写 买入 / 卖出 / 现金分红 / 送转 / 期初持仓）`)
    }
    // 表格软件吃掉开头的 0：位数不足就明确退回，不补 0 也不当港股
    if (/^\d{1,5}$/.test(shown.symbol)) {
      return fail(
        `代码「${shown.symbol}」只有 ${shown.symbol.length} 位：表格软件常把开头的 0 去掉，` +
          '本页只做 A 股，请把代码写满 6 位（如 000001）',
      )
    }

    const form: TradeForm = {
      ...emptyTradeForm(type),
      type,
      symbol: shown.symbol,
      name: shown.name,
      date: normalizeDate(shown.date),
      shares: normalizeNumber(cellOf(cells, 'shares')),
      price: normalizeNumber(cellOf(cells, 'price')),
      fee: normalizeNumber(cellOf(cells, 'fee')),
      amount: normalizeNumber(cellOf(cells, 'amount')),
      ratio: normalizeNumber(cellOf(cells, 'ratio')),
      note: cellOf(cells, 'note'),
    }
    const parsed = tradeRecord(form)
    if ('error' in parsed) return fail(parsed.error)
    return { line, cells: shown, record: parsed.record, error: null }
  })

  return { ok: true, rows, columns: [...column.keys()], unused, delimiter }
}

/** 文件字节 → 文本：先按 UTF-8 严格解，解不了按 GB18030（券商 / 旧表格软件导出的中文 CSV 常是 GBK）。 */
export function decodeTableFile(buffer: ArrayBuffer | Uint8Array): { text: string } | { error: string } {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer)
  if (bytes.length === 0) return { error: '文件是空的' }
  // PK\x03\x04：xlsx / ods 都是 zip，别让用户以为选了个"CSV"就能读
  if (bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04) {
    return { error: '这是 Excel 工作簿（.xlsx），读不了：请在表格软件里「另存为 CSV」，或整表复制后粘贴' }
  }
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
  } catch {
    // 不是 UTF-8，往下试 GB18030
  }
  try {
    return { text: new TextDecoder('gb18030').decode(bytes) }
  } catch {
    return { error: '文件编码认不出：请另存为「CSV UTF-8」再选' }
  }
}

/** 模板：列名 + 每种类型一行。数字只是格式示例，不是任何人的真实交易。 */
export const TRADE_CSV_TEMPLATE = [
  '日期,类型,代码,名称,数量,价格,费用,金额,比例,备注',
  '2020-01-02,期初持仓,600519,贵州茅台,100,1500.00,,,,示例：开始记账前就有的写期初持仓（价格=每股成本）',
  '2025-01-02,买入,600519,贵州茅台,100,1500,5,,,示例：费用没有就留空',
  '2025-02-03,卖出,600519,贵州茅台,50,1700,8,,,示例',
  '2025-06-20,现金分红,600519,贵州茅台,,,,1500,,示例：金额写实际到账（税后）',
  '2025-07-01,送转,600519,贵州茅台,,,,,1.5,示例：10 送 3 转 2 填 1.5；1 拆 4 填 4',
].join('\n')

/** 预览里展示用的「明细」：与 `tradeDetail` 同口径，但输入是请求体不是台账行。 */
export function recordDetail(record: TradeInput): string {
  const { type } = record
  if (type === 'buy' || type === 'sell') {
    const fee = record.fee ? `，费用 ${record.fee}` : ''
    return `${record.shares ?? '?'} 股 × ${record.price ?? '?'}${fee}`
  }
  if (type === 'opening') return `${record.shares ?? '?'} 股，每股成本 ${record.price ?? '?'}`
  if (type === 'dividend') return `到账 ${record.amount ?? '?'}`
  if (type === 'split') return `每 1 股变 ${record.ratio ?? '?'} 股`
  return ''
}

/** 类型中文名（模板、预览共用一份，避免和 `TRADE_TYPE_LABELS` 走偏）。 */
export function typeLabel(type: TradeType | string): string {
  return TRADE_TYPE_LABELS[type as TradeType] ?? type
}
