/**
 * 分享卡（QUOT-453）：只把「本页已取到的原始数据 + 缺口」画成图片 / 文字。
 *
 * 参考库 `ShareCardPanel` + `lib/shareCard`：图画出来是为了把这次研究看到的
 * 事实发出去，所以：
 * - 所有数字都来自调用方传入的 `overview`（页面正在渲染的那份），**不在卡片里
 *   重算涨跌幅 / 比率**；
 * - 取不到的字段保留 `—`，并在「没取到的」里逐条写原因，绝不用 0 或占位文本糊过去；
 * - 不放研究结论、论点正文、持仓或任何私有字段（这些不在入参里）。
 */

import type { ResearchOverview } from '../api/research'
import { MISSING, formatChangePct, formatMoney } from './format'
import { cssVar } from '../charts/cssVar'
import { fmtPrice, fmtVolume } from '../charts/klineView'

export type ShareLayout = 'portrait' | 'landscape'

export const SHARE_CARD_SIZE: Record<ShareLayout, { width: number; height: number }> = {
  portrait: { width: 720, height: 1020 },
  landscape: { width: 1080, height: 660 },
}

export interface ShareRow {
  label: string
  value: string
  missing: boolean
}

export interface ShareCardData {
  code: string
  name: string | null
  date: string
  rows: ShareRow[]
  gaps: string[]
  source: string | null
  fetchedAt: string | null
  generatedAt: string
}

const STATEMENT = '数字只来自本页已取到的数据；缺数显示 —，不补 0、不重算。'

export function shareStatement(): string {
  return STATEMENT
}

function row(label: string, value: string, missing = false): ShareRow {
  return { label, value, missing }
}

function pct(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return MISSING
  return `${value.toFixed(digits)}%`
}

/** 带方向的读数走页面同一个格式化入口：卡片上的涨跌幅要和页面上看到的字一模一样。 */
function signed(value: number | null | undefined): string {
  return formatChangePct(value).text
}

function localNow(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
}

/** 概览 → 卡片数据；缺口逐条写原因，页面与卡片共用同一份判断。 */
export function buildShareCard(input: {
  code: string
  name: string | null
  date: string
  overview: ResearchOverview | null
  generatedAt?: string
}): ShareCardData {
  const { code, name, date, overview } = input
  const quote = overview?.quote.quote ?? null
  const financials = overview?.financials.financials ?? null
  const bars = overview?.kline.bars ?? []
  const gaps: string[] = []

  const rows: ShareRow[] = []
  if (quote) {
    const change = signed(quote.change_pct)
    rows.push(
      row('现价', fmtPrice(quote.price), quote.price === null),
      row('涨跌幅', change, quote.change_pct === null),
      row('今开', fmtPrice(quote.open), quote.open === null),
      row('最高', fmtPrice(quote.high), quote.high === null),
      row('最低', fmtPrice(quote.low), quote.low === null),
      row('成交量（手）', fmtVolume(quote.volume), quote.volume === null),
      row(
        '成交额（元）',
        quote.amount === null ? MISSING : formatMoney(quote.amount),
        quote.amount === null,
      ),
    )
    if (quote.amount === null) gaps.push('成交额：实时快照里没有这一项。')
    if (quote.price === null) gaps.push('现价：实时快照里没有这一项。')
  } else {
    const reason = overview?.quote.reason ?? '概览里没有实时快照'
    rows.push(
      row('现价', MISSING, true),
      row('涨跌幅', MISSING, true),
      row('成交量（手）', MISSING, true),
      row('成交额（元）', MISSING, true),
    )
    gaps.push(`当日行情：${reason}`)
  }

  if (bars.length > 0) {
    const first = bars[0]?.date ?? MISSING
    const last = bars[bars.length - 1]?.date ?? MISSING
    rows.push(row('K 线区间', `${first} 至 ${last}`, false))
    rows.push(row('日 K 根数', String(bars.length), false))
  } else {
    rows.push(row('K 线区间', MISSING, true))
    gaps.push(`K 线：${overview?.kline.reason ?? '本地缓存没有日线'}`)
  }

  if (financials) {
    const period = financials.report_name ?? financials.report_date ?? MISSING
    rows.push(row('最新报告期', period, period === MISSING))
    rows.push(
      row('营业收入', financials.revenue === null ? MISSING : formatMoney(financials.revenue), financials.revenue === null),
      row(
        '归母净利润',
        financials.net_profit === null ? MISSING : formatMoney(financials.net_profit),
        financials.net_profit === null,
      ),
      row('营收同比', signed(financials.revenue_yoy), financials.revenue_yoy === null),
      row('净利润同比', signed(financials.profit_yoy), financials.profit_yoy === null),
      row('ROE', pct(financials.roe), financials.roe === null),
    )
    for (const [label, value] of [
      ['营业收入', financials.revenue],
      ['归母净利润', financials.net_profit],
      ['营收同比', financials.revenue_yoy],
      ['净利润同比', financials.profit_yoy],
      ['ROE', financials.roe],
    ] as const) {
      if (value === null) gaps.push(`${label}：财务指标表里没有这一项。`)
    }
  } else {
    rows.push(row('最新报告期', MISSING, true))
    gaps.push(`财务指标：${overview?.financials.reason ?? '概览里没有财务指标'}`)
  }

  if (overview === null) gaps.push('概览接口还没有返回数据（加载中或失败）。')

  const sourceDetail = quote?.source_detail ?? null
  const source = [sourceDetail?.peer, sourceDetail?.tool].filter(Boolean).join(' · ') || null
  const fetchedAt = sourceDetail?.fetched_at
    ? sourceDetail.fetched_at.slice(0, 16).replace('T', ' ')
    : null

  return {
    code,
    name: name ?? quote?.name ?? overview?.name ?? null,
    date,
    rows,
    gaps,
    source,
    fetchedAt,
    generatedAt: input.generatedAt ?? localNow(),
  }
}

/** 复制成文字：与图片同源，顺序一致，缺数照样写 `—`。 */
export function shareSummary(data: ShareCardData): string[] {
  const title = data.name ? `${data.name}（${data.code}）` : data.code
  const lines = [`${title} · ${data.date}`, STATEMENT, '']
  for (const item of data.rows) lines.push(`${item.label}：${item.value}`)
  lines.push('', data.gaps.length > 0 ? '没取到的：' : '没取到的：无')
  for (const gap of data.gaps) lines.push(`- ${gap}`)
  lines.push('', `来源：${data.source ?? MISSING}${data.fetchedAt ? ` · 取数 ${data.fetchedAt}` : ''}`)
  lines.push(`生成：${data.generatedAt}`)
  return lines
}

/**
 * 画布取不到 CSS 变量，所以颜色一律经 `cssVar()` 读设计令牌（与图表同一口径）：
 * 页面切主题时重新读一遍，导出的图自然跟着屏幕走，源码里不出现颜色字面量。
 */
function cardPalette(): Record<'bg' | 'card' | 'text' | 'muted' | 'accent' | 'line' | 'warn', string> {
  return {
    bg: cssVar('--ldst-bg'),
    card: cssVar('--ldst-surface-2'),
    text: cssVar('--ldst-text'),
    muted: cssVar('--ldst-text-muted'),
    accent: cssVar('--ldst-accent'),
    line: cssVar('--ldst-border'),
    warn: cssVar('--ldst-warn'),
  }
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number,
): string[] {
  const chars = Array.from(text)
  const lines: string[] = []
  let current = ''
  for (const char of chars) {
    const next = current + char
    if (ctx.measureText(next).width > maxWidth && current) {
      lines.push(current)
      current = char
      if (lines.length >= maxLines) break
    } else {
      current = next
    }
  }
  if (lines.length < maxLines && current) lines.push(current)
  if (lines.length === maxLines && chars.length > lines.join('').length) {
    lines[maxLines - 1] = `${lines[maxLines - 1].slice(0, -1)}…`
  }
  return lines
}

const FONT_STACK = '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif'

/** 把卡片数据画到 2D 画布；`width/height` 由调用方按 `SHARE_CARD_SIZE` 设置。 */
export function drawShareCard(
  ctx: CanvasRenderingContext2D,
  data: ShareCardData,
  layout: ShareLayout,
): void {
  const size = SHARE_CARD_SIZE[layout]
  const palette = cardPalette()
  const pad = layout === 'portrait' ? 52 : 56
  const width = size.width
  const height = size.height

  ctx.save()
  ctx.fillStyle = palette.bg
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = palette.accent
  ctx.fillRect(0, 0, width, 8)

  ctx.textBaseline = 'top'
  ctx.fillStyle = palette.muted
  ctx.font = `500 18px ${FONT_STACK}`
  ctx.fillText('LadderStock · 个股深挖', pad, pad - 12)

  ctx.fillStyle = palette.text
  ctx.font = `700 ${layout === 'portrait' ? 44 : 42}px ${FONT_STACK}`
  const title = data.name ? `${data.name}  ${data.code}` : data.code
  ctx.fillText(title, pad, pad + 18)

  ctx.fillStyle = palette.muted
  ctx.font = `400 20px ${FONT_STACK}`
  ctx.fillText(`研究日期 ${data.date} · 生成 ${data.generatedAt}`, pad, pad + (layout === 'portrait' ? 78 : 74))

  const top = pad + (layout === 'portrait' ? 128 : 118)
  const footerHeight = 116
  const gapsHeight = 118
  const panelTop = top
  const panelHeight = height - top - footerHeight - gapsHeight
  ctx.fillStyle = palette.card
  ctx.beginPath()
  ctx.roundRect(pad, panelTop, width - pad * 2, panelHeight, 18)
  ctx.fill()

  const columns = layout === 'portrait' ? 2 : 3
  const rowGap = layout === 'portrait' ? 46 : 44
  const columnWidth = (width - pad * 2 - 40) / columns
  const rowsPerColumn = Math.ceil(data.rows.length / columns)
  data.rows.forEach((item, index) => {
    const column = Math.floor(index / rowsPerColumn)
    const rowIndex = index % rowsPerColumn
    const x = pad + 20 + column * columnWidth
    const y = panelTop + 22 + rowIndex * rowGap
    ctx.fillStyle = palette.muted
    ctx.font = `400 17px ${FONT_STACK}`
    ctx.fillText(item.label, x, y)
    ctx.fillStyle = item.missing ? palette.warn : palette.text
    ctx.font = `600 25px ${FONT_STACK}`
    const maxWidth = columnWidth - 30
    let value = item.value
    while (ctx.measureText(value).width > maxWidth && value.length > 1) {
      value = `${value.slice(0, -2)}…`
    }
    ctx.fillText(value, x, y + 22)
  })

  const gapsTop = panelTop + panelHeight + 22
  ctx.fillStyle = palette.text
  ctx.font = `600 20px ${FONT_STACK}`
  ctx.fillText(data.gaps.length > 0 ? '没取到的' : '没取到的：无', pad, gapsTop)
  ctx.font = `400 17px ${FONT_STACK}`
  ctx.fillStyle = palette.muted
  if (data.gaps.length > 0) {
    const lines = wrapText(ctx, data.gaps.join('；'), width - pad * 2, 3)
    lines.forEach((line, index) => ctx.fillText(line, pad, gapsTop + 30 + index * 26))
  } else {
    ctx.fillText('这一页需要的字段都取到了。', pad, gapsTop + 30)
  }

  ctx.strokeStyle = palette.line
  ctx.beginPath()
  ctx.moveTo(pad, height - footerHeight + 6)
  ctx.lineTo(width - pad, height - footerHeight + 6)
  ctx.stroke()

  ctx.fillStyle = palette.muted
  ctx.font = `400 16px ${FONT_STACK}`
  const source = `来源：${data.source ?? MISSING}${data.fetchedAt ? ` · 取数 ${data.fetchedAt}` : ''}`
  ctx.fillText(source, pad, height - footerHeight + 22)
  const statement = wrapText(ctx, STATEMENT, width - pad * 2, 2)
  statement.forEach((line, index) => ctx.fillText(line, pad, height - footerHeight + 50 + index * 24))
  ctx.restore()
}
