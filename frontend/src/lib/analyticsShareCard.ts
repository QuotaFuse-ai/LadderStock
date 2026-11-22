/**
 * 复盘统计分享卡（C 包第二半 / DEC-090 §2「分享长图 / 链接」）。
 *
 * 与研究页分享卡（`lib/shareCard.ts`，QUOT-453）同一套纪律：
 * - 图上/文字里的数字**全部来自页面正在渲染的那份聚合**（`AnalyticsSummary`），
 *   不在卡片里重算任何比率；取不到的字段保留 `—` 并逐条写原因，不补 0；
 * - 不含持仓明细、交易流水、自选清单（DEC-090 §1「不导的东西」）；归因只带
 *   逐票聚合结果，错题本只带已实现亏损票的匿名行（代码 + 日期 + 盈亏）；
 * - 文字版与图片同一份数据、同一个顺序，缺数照样写 `—`。
 *
 * 长图是竖版 canvas（720×1280）：画布取不到 CSS 变量，颜色一律经 `cssVar()`
 * 读设计令牌，源码不出现颜色字面量（与 `lib/shareCard.ts` 同口径）。
 */

import type { AnalyticsSummary } from '../api/analytics'
import { cssVar } from '../charts/cssVar'
import { MISSING } from './format'

export const ANALYTICS_SHARE_SIZE = { width: 720, height: 1280 }

export const ANALYTICS_SHARE_STATEMENT =
  '数字只来自本机「复盘统计」页已算出的聚合；缺数显示 —，不补 0、不重算。'

export interface AnalyticsShareRow {
  label: string
  value: string
  missing: boolean
}

export interface AnalyticsShareLine {
  label: string
  value: string
  missing: boolean
}

export interface AnalyticsShareCardData {
  windowLabel: string
  start: string | null
  asOf: string | null
  rows: AnalyticsShareRow[]
  attributionTop: AnalyticsShareLine[]
  mistakesTop: AnalyticsShareLine[]
  warnings: string[]
  benchmarkLabel: string
  benchmarkMissing: string | null
  generatedAt: string
}

function pct(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return MISSING
  return `${(value * 100).toFixed(digits)}%`
}

function money(value: number | null | undefined): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  return MISSING
}

function line(label: string, value: string, missing = false): AnalyticsShareLine {
  return { label, value, missing }
}

function localNow(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`
}

/** 聚合 → 卡片数据；「为什么亏」只进文字/图片的前几行，不展开持仓。 */
export function buildAnalyticsShareCard(input: {
  summary: AnalyticsSummary
  days: number
  generatedAt?: string
}): AnalyticsShareCardData {
  const { summary, days } = input
  const metrics = summary.metrics
  const realized = metrics?.realized ?? null
  const benchmark = summary.benchmark
  const rows: AnalyticsShareRow[] = []
  const warnings = [...summary.warnings]

  const push = (label: string, value: string, missing: boolean) => rows.push({ label, value, missing })

  push('总收益（TWR）', pct(metrics?.twr), metrics?.twr == null)
  push('年化（TWR）', pct(metrics?.twr_annualized), metrics?.twr_annualized == null)
  push('最大回撤', pct(metrics?.max_drawdown), metrics?.max_drawdown == null)
  push('夏普', metrics?.sharpe == null ? MISSING : metrics.sharpe.toFixed(2), metrics?.sharpe == null)
  push(
    '已实现胜率',
    pct(realized?.win_rate),
    !realized || realized.win_rate == null,
  )
  push(
    '盈亏比',
    realized?.profit_factor == null ? MISSING : realized.profit_factor.toFixed(2),
    realized?.profit_factor == null,
  )
  push(
    `超额（vs ${benchmark.label || '沪深300'}）`,
    pct(summary.excess_return),
    summary.excess_return == null,
  )
  push(`基准收益（${benchmark.label || '沪深300'}）`, pct(benchmark.return), benchmark.return == null)
  push('净投入', money(metrics?.invested), metrics?.invested == null)
  push('总盈亏', money(metrics?.gain), metrics?.gain == null)

  if (benchmark.return == null && benchmark.reason) {
    warnings.push(`基准（${benchmark.label || '沪深300'}）：${benchmark.reason}`)
  }

  const attributionTop = (summary.attribution ?? []).slice(0, 5).map((row) =>
    line(
      `${row.name || row.symbol}（${row.symbol}）${row.status === 'holding' ? ' · 持有中' : ' · 已了结'}`,
      `已实现 ${money(row.realized)} · 分红 ${money(row.dividends)} · 净 ${money(row.net)}`,
      false,
    ),
  )

  const mistakesTop = (summary.mistakes ?? []).slice(0, 5).map((row) =>
    line(
      `${row.symbol} · ${row.date} · 持有 ${row.hold_days == null ? MISSING : `${row.hold_days} 天`}`,
      `亏损 ${money(row.pnl)}（${pct(row.pnl_pct)}）`,
      row.pnl_pct == null,
    ),
  )

  return {
    windowLabel: `近 ${Math.round(days / 30)} 个月（${days} 天窗口）`,
    start: summary.start,
    asOf: summary.as_of,
    rows,
    attributionTop,
    mistakesTop,
    warnings,
    benchmarkLabel: benchmark.label || '沪深300',
    benchmarkMissing: benchmark.return == null ? (benchmark.reason ?? null) : null,
    generatedAt: input.generatedAt ?? localNow(),
  }
}

/** 复制成文字：与长图同源同序，缺数照样写 `—`。 */
export function analyticsShareSummary(data: AnalyticsShareCardData): string[] {
  const lines = [`组合统计 · ${data.windowLabel}`, ANALYTICS_SHARE_STATEMENT, '']
  lines.push(
    `区间：${data.start ?? MISSING} → ${data.asOf ?? MISSING}`,
    '',
    '指标：',
  )
  for (const item of data.rows) lines.push(`- ${item.label}：${item.value}`)
  lines.push('', data.attributionTop.length > 0 ? '归因（按已实现盈亏前五）：' : '归因：无')
  for (const item of data.attributionTop) lines.push(`- ${item.label}：${item.value}`)
  lines.push('', data.mistakesTop.length > 0 ? '错题本（最近前五）：' : '错题本：无')
  for (const item of data.mistakesTop) lines.push(`- ${item.label}：${item.value}`)
  if (data.warnings.length > 0) {
    lines.push('', '注意：')
    for (const warning of data.warnings) lines.push(`- ${warning}`)
  }
  lines.push('', `生成：${data.generatedAt} · 数据只在本机，不随图片上传。`)
  return lines
}

const FONT_STACK = '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif'

function palette(): Record<'bg' | 'card' | 'text' | 'muted' | 'accent' | 'line' | 'warn', string> {
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

function truncate(ctx: CanvasRenderingContext2D, value: string, maxWidth: number): string {
  let text = value
  while (ctx.measureText(text).width > maxWidth && text.length > 1) {
    text = `${text.slice(0, -2)}…`
  }
  return text
}

/** 画竖版长图（720×1280）：指标面板 + 归因前五 + 错题本前五 + 声明脚注。 */
export function drawAnalyticsShareCard(
  ctx: CanvasRenderingContext2D,
  data: AnalyticsShareCardData,
): void {
  const { width, height } = ANALYTICS_SHARE_SIZE
  const colors = palette()
  const pad = 52

  ctx.save()
  ctx.fillStyle = colors.bg
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = colors.accent
  ctx.fillRect(0, 0, width, 8)

  ctx.textBaseline = 'top'
  ctx.fillStyle = colors.muted
  ctx.font = `500 18px ${FONT_STACK}`
  ctx.fillText('LadderStock · 复盘统计', pad, pad - 12)

  ctx.fillStyle = colors.text
  ctx.font = `700 40px ${FONT_STACK}`
  ctx.fillText(`组合统计 · ${data.windowLabel}`, pad, pad + 18)

  ctx.fillStyle = colors.muted
  ctx.font = `400 19px ${FONT_STACK}`
  ctx.fillText(
    `区间 ${data.start ?? MISSING} → ${data.asOf ?? MISSING} · 生成 ${data.generatedAt}`,
    pad,
    pad + 72,
  )

  // 指标面板：两列网格（与页面指标卡同序）
  const panelTop = pad + 118
  const panelHeight = 336
  ctx.fillStyle = colors.card
  ctx.beginPath()
  ctx.roundRect(pad, panelTop, width - pad * 2, panelHeight, 18)
  ctx.fill()
  const columnWidth = (width - pad * 2 - 40) / 2
  data.rows.forEach((item, index) => {
    const column = index % 2
    const rowIndex = Math.floor(index / 2)
    const x = pad + 20 + column * columnWidth
    const y = panelTop + 20 + rowIndex * 62
    ctx.fillStyle = colors.muted
    ctx.font = `400 16px ${FONT_STACK}`
    ctx.fillText(item.label, x, y)
    ctx.fillStyle = item.missing ? colors.warn : colors.text
    ctx.font = `600 24px ${FONT_STACK}`
    ctx.fillText(truncate(ctx, item.value, columnWidth - 30), x, y + 20)
  })

  // 归因 / 错题本：各取前五，行式文本
  let cursor = panelTop + panelHeight + 26
  ctx.fillStyle = colors.text
  ctx.font = `600 20px ${FONT_STACK}`
  ctx.fillText('归因（按已实现盈亏前五）', pad, cursor)
  ctx.font = `400 17px ${FONT_STACK}`
  cursor += 30
  if (data.attributionTop.length === 0) {
    ctx.fillStyle = colors.muted
    ctx.fillText('本窗口没有已了结/持有票的归因行。', pad, cursor)
    cursor += 26
  } else {
    for (const item of data.attributionTop) {
      ctx.fillStyle = colors.muted
      ctx.fillText(truncate(ctx, item.label, width - pad * 2), pad, cursor)
      ctx.fillStyle = colors.text
      ctx.fillText(truncate(ctx, item.value, width - pad * 2), pad + 14, cursor + 22)
      cursor += 50
    }
  }

  cursor += 10
  ctx.fillStyle = colors.text
  ctx.font = `600 20px ${FONT_STACK}`
  ctx.fillText('错题本（最近前五）', pad, cursor)
  cursor += 30
  ctx.font = `400 17px ${FONT_STACK}`
  if (data.mistakesTop.length === 0) {
    ctx.fillStyle = colors.muted
    ctx.fillText('本窗口没有已实现亏损票——这不是「没有亏损风险」。', pad, cursor)
    cursor += 26
  } else {
    for (const item of data.mistakesTop) {
      ctx.fillStyle = colors.muted
      ctx.fillText(truncate(ctx, item.label, width - pad * 2), pad, cursor)
      ctx.fillStyle = colors.warn
      ctx.fillText(truncate(ctx, item.value, width - pad * 2), pad + 14, cursor + 22)
      cursor += 50
    }
  }

  // 脚注：声明 + 警示
  const footerTop = height - 150
  ctx.strokeStyle = colors.line
  ctx.beginPath()
  ctx.moveTo(pad, footerTop)
  ctx.lineTo(width - pad, footerTop)
  ctx.stroke()

  ctx.fillStyle = colors.muted
  ctx.font = `400 16px ${FONT_STACK}`
  const statementLines: string[] = [ANALYTICS_SHARE_STATEMENT]
  for (const warning of data.warnings.slice(0, 2)) statementLines.push(`注意：${warning}`)
  statementLines.forEach((text, index) => {
    ctx.fillText(truncate(ctx, text, width - pad * 2), pad, footerTop + 16 + index * 24)
  })
  ctx.fillText('本机数据 · 图片由本机页面生成，不含持仓明细', pad, height - 46)
  ctx.restore()
}
