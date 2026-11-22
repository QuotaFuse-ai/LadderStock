/**
 * 复盘统计（`/analytics`）：B 包——把「为什么赚 / 为什么亏」摆到一页。
 *
 * 六块：组合指标卡（总收益/年化/回撤/夏普/胜率/盈亏比/超额）→ 净值曲线 →
 * 基准对比 → 交易归因 → 行为统计 → 错题本。全部数字来自
 * `/api/analytics/summary`（DEC-089），口径在 `analytics/engine.py` 模块表里，
 * 页面不重算、不补 0：缺数显示「—」并给原因。
 *
 * 回看窗口 90/180/365/730 天可选；切窗口重新取数。
 * 导出（DEC-090）：CSV 按节下载（归因/行为/错题本）+ XLSX 一簿四表；
 * 打印：`print:` 样式隐藏导航与按钮，指标卡按两列排（见 index.css 的 `@media print`）。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, BarChart3, Download, FileText, Frown, Loader2, PieChart, Printer, Share2, TrendingUp } from 'lucide-react'

import { describeApiError } from '../api'
import {
  downloadAnalyticsExport,
  fetchAnalytics,
  type AnalyticsSummary,
  type ExportSection,
} from '../api/analytics'
import { OfflineBanner, PageHeader } from '../components/PageShell'
import {
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  ErrorBlock,
  SectionCard,
  Segmented,
  Sparkline,
} from '../components/ui'
import { cn } from '../lib/cn'
import { MISSING } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import {
  ANALYTICS_SHARE_SIZE,
  analyticsShareSummary,
  buildAnalyticsShareCard,
  drawAnalyticsShareCard,
  type AnalyticsShareCardData,
} from '../lib/analyticsShareCard'

const DAYS_OPTIONS = [
  { value: '90', label: '近 3 个月' },
  { value: '180', label: '近半年' },
  { value: '365', label: '近一年' },
  { value: '730', label: '近两年' },
] as const

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return MISSING
  return `${(value * 100).toFixed(digits)}%`
}

function signedTone(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'text-text-muted'
  return value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-text'
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return MISSING
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <Card className="p-4">
      <div className="text-caption text-text-muted">{label}</div>
      <div className={cn('mt-1 text-title font-semibold ldst-num', tone ?? 'text-text')}>{value}</div>
      {sub ? <div className="mt-0.5 text-caption text-text-muted">{sub}</div> : null}
    </Card>
  )
}

export default function AnalyticsScreen() {
  const [days, setDays] = useState<(typeof DAYS_OPTIONS)[number]['value']>('365')
  const [exporting, setExporting] = useState<ExportSection | 'xlsx' | 'pdf' | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
  const data = useAsync(
    async () => fetchAnalytics(Number(days)),
    [days],
  )
  const summary: AnalyticsSummary | null = data.data
  const errorText = data.status === 'error' ? describeApiError(data.error) : null

  const onExport = async (kind: ExportSection | 'xlsx' | 'pdf') => {
    setExportError(null)
    setExporting(kind)
    try {
      if (kind === 'xlsx') {
        await downloadAnalyticsExport('xlsx', 'attribution', Number(days))
      } else if (kind === 'pdf') {
        await downloadAnalyticsExport('pdf', 'attribution', Number(days))
      } else {
        await downloadAnalyticsExport('csv', kind, Number(days))
      }
    } catch (error) {
      setExportError(describeApiError(error).detail)
    } finally {
      setExporting(null)
    }
  }

  const metrics = summary?.metrics
  const realized = metrics?.realized
  const curve = summary?.curve
  const curveValues = curve?.twr_index ?? []
  const curveTone =
    curveValues.length >= 2
      ? curveValues[curveValues.length - 1] >= curveValues[0]
        ? 'up'
        : 'down'
      : 'flat'

  return (
    <div className="mx-auto max-w-table px-page pb-16">
      <PageHeader
        title="复盘统计"
        subtitle="组合指标 · 基准对比 · 交易归因 · 行为统计 · 错题本（只读本机数据）"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              ariaLabel="回看窗口"
              value={days}
              onChange={(next) => setDays(next)}
              options={DAYS_OPTIONS.map(({ value, label }) => ({ value, label }))}
            />
            <div className="no-print flex flex-wrap items-center gap-1">
              <Button
                size="md"
                variant="secondary"
                onClick={() => onExport('attribution')}
                disabled={exporting !== null || !summary?.ok}
                title="下载归因表 CSV"
              >
                {exporting === 'attribution' ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Download size={14} />
                )}
                归因 CSV
              </Button>
              <Button
                size="md"
                variant="secondary"
                onClick={() => onExport('mistakes')}
                disabled={exporting !== null || !summary?.ok}
                title="下载错题本 CSV"
              >
                {exporting === 'mistakes' ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Download size={14} />
                )}
                错题 CSV
              </Button>
              <Button
                size="md"
                variant="secondary"
                onClick={() => onExport('xlsx')}
                disabled={exporting !== null || !summary?.ok}
                title="下载 XLSX（指标 + 归因 + 行为 + 错题本四张表；需要 openpyxl）"
              >
                {exporting === 'xlsx' ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Download size={14} />
                )}
                XLSX
              </Button>
              <Button
                size="md"
                variant="secondary"
                onClick={() => onExport('pdf')}
                disabled={exporting !== null || !summary?.ok}
                title="下载 PDF（A4 竖版：指标 + 三张明细表；需要 reportlab 与中文字体）"
              >
                {exporting === 'pdf' ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <FileText size={14} />
                )}
                PDF
              </Button>
              <Button
                size="md"
                variant="secondary"
                onClick={() => setShareOpen(true)}
                disabled={!summary?.ok}
                title="生成本页聚合的长图 / 文字 / 链接（与页面同源，缺数不补 0）"
              >
                <Share2 size={14} />
                分享
              </Button>
              <Button
                size="md"
                variant="ghost"
                onClick={() => window.print()}
                title="打印 / 存 PDF（浏览器打印对话框）"
              >
                <Printer size={14} />
              </Button>
            </div>
          </div>
        }
      />

      {exportError ? (
        <p className="mt-2 text-caption text-danger">{exportError}</p>
      ) : null}

      {errorText ? (
        <>
          <OfflineBanner onRetry={() => data.reload()} />
          <ErrorBlock title={errorText.title} detail={errorText.detail} onRetry={() => data.reload()} />
        </>
      ) : null}

      {data.status === 'ready' && summary && !summary.ok ? (
        <EmptyState
          icon={<BarChart3 size={20} />}
          title="还差一点数据，统计算不出"
          description={summary.reason || '缺少交易流水或日线缓存。'}
          action={
            <div className="flex gap-2">
              <Link className="text-caption text-action underline" to="/journal">
                去记一笔交易
              </Link>
              <Link className="text-caption text-action underline" to="/my-stocks?tab=hold">
                看持仓
              </Link>
            </div>
          }
        />
      ) : null}

      {data.status === 'ready' && summary?.ok && metrics ? (
        <div className="mt-4 flex flex-col gap-4">
          <AnalyticsShareDrawer
            open={shareOpen}
            onClose={() => setShareOpen(false)}
            summary={summary}
            days={Number(days)}
          />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Metric
              label="总收益（TWR）"
              value={pct(metrics.twr)}
              sub={`年化 ${pct(metrics.twr_annualized)}`}
              tone={signedTone(metrics.twr)}
            />
            <Metric
              label="最大回撤"
              value={pct(metrics.max_drawdown)}
              sub="peak-to-trough"
              tone={metrics.max_drawdown && metrics.max_drawdown > 0 ? 'text-down' : 'text-text'}
            />
            <Metric
              label="夏普"
              value={metrics.sharpe === null ? MISSING : metrics.sharpe.toFixed(2)}
              sub={`年化波动 ${pct(metrics.annual_volatility)}`}
            />
            <Metric
              label={`超额（vs ${summary?.benchmark.label ?? '沪深300'}）`}
              value={pct(summary?.excess_return)}
              sub={`基准 ${pct(summary?.benchmark.return)}`}
              tone={signedTone(summary?.excess_return)}
            />
            <Metric
              label="已实现胜率"
              value={pct(realized?.win_rate)}
              sub={`${realized?.count ?? 0} 笔 · 盈 ${realized?.wins ?? 0} / 亏 ${realized?.losses ?? 0}`}
            />
            <Metric
              label="盈亏比"
              value={realized?.profit_factor == null ? MISSING : realized.profit_factor.toFixed(2)}
              sub={`均盈 ${money(realized?.avg_win)} / 均亏 ${money(realized?.avg_loss)}`}
            />
            <Metric label="净投入" value={money(metrics.invested)} sub={`期末市值 ${money(metrics.end_value)}`} />
            <Metric
              label="总盈亏"
              value={money(metrics.gain)}
              sub={`分红 ${money(metrics.dividends)} · 费用 ${money(metrics.fees)}`}
              tone={signedTone(metrics.gain)}
            />
          </div>

          <SectionCard title="净值曲线（TWR）" subtitle={summary.start && summary.as_of ? `${summary.start} → ${summary.as_of} · ${summary.span_days} 天` : undefined}>
            <div className="flex items-center gap-4">
              <Sparkline values={curveValues} tone={curveTone} ariaLabel="组合净值走势" width={320} height={64} />
              <div className="text-caption text-text-muted">
                <div>期末净值 {curveValues.length ? curveValues[curveValues.length - 1].toFixed(4) : MISSING}</div>
                <div>样本 {curveValues.length} 个交易日</div>
              </div>
            </div>
            {summary.warnings.length > 0 ? (
              <ul className="mt-2 list-inside list-disc text-caption text-text-muted">
                {summary.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
          </SectionCard>

          <SectionCard title="交易归因（按标的净贡献）" subtitle="已实现 + 分红 − 费用">
            {summary.attribution.length === 0 ? (
              <EmptyState icon={<PieChart size={20} />} title="没有可归因的记录" description="录入买卖流水后这里会出现每个标的的净贡献。" />
            ) : (
              <div className="flex flex-col">
                {summary.attribution.map((row) => (
                  <div
                    key={row.symbol}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-2.5 text-body last:border-b-0"
                  >
                    <span className="font-medium text-text">
                      {row.name || row.symbol} <span className="text-caption text-text-muted">{row.symbol}</span>
                    </span>
                    <Badge tone={row.status === 'closed' ? 'neutral' : 'info'}>
                      {row.status === 'closed' ? '已清仓' : '持仓中'}
                    </Badge>
                    <span className={cn('ldst-num font-semibold', signedTone(row.net))}>{money(row.net)}</span>
                    <span className="text-caption text-text-muted">
                      已实现 {money(row.realized)} · 分红 {money(row.dividends)} · 费用 {money(row.fees)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard
            title="行为统计"
            subtitle={`买入价在当日振幅中的位置 → 第 ${summary.behavior.window_days ?? 5} 个交易日收盘的收益`}
          >
            {summary.behavior.buckets.length === 0 ? (
              <EmptyState
                icon={<TrendingUp size={20} />}
                title="还没有可判定的买入"
                description={summary.behavior.reason || '需要日线里带 high / low，且买入后至少还有 5 个交易日。'}
              />
            ) : (
              <div className="flex flex-col">
                {summary.behavior.buckets.map((row) => (
                  <div
                    key={row.bucket}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-2.5 text-body last:border-b-0"
                  >
                    <span className="font-medium text-text">{row.label}</span>
                    <span className="text-caption text-text-muted">{row.count} 笔</span>
                    <span className={cn('ldst-num', signedTone(row.avg_forward_return))}>
                      均值 {pct(row.avg_forward_return)}
                    </span>
                    <span className="text-caption text-text-muted">5 日胜率 {pct(row.win_rate)}</span>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="错题本（已实现亏损最大）" subtitle="不是自责清单，是下一笔之前该看的证据">
            {summary.mistakes.length === 0 ? (
              <EmptyState icon={<Frown size={20} />} title="没有亏损的已实现交易" description="保持住——或者数据还没录入。" />
            ) : (
              <div className="flex flex-col">
                {summary.mistakes.map((row) => (
                  <div
                    key={`${row.symbol}-${row.date}-${row.pnl}`}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-2.5 text-body last:border-b-0"
                  >
                    <span className="text-text-muted">{row.date}</span>
                    <span className="font-medium text-text">{row.symbol}</span>
                    <span className={cn('ldst-num font-semibold', signedTone(row.pnl))}>
                      {money(row.pnl)}（{pct(row.pnl_pct)}）
                    </span>
                    <span className="text-caption text-text-muted">
                      持有 {row.hold_days === null ? MISSING : `${row.hold_days} 天`} · 卖出 {money(row.proceeds)} / 成本 {money(row.cost)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <p className="text-caption-2 text-text-muted">
            <AlertTriangle size={12} className="mr-1 inline" />
            {summary.notes.join(' ')}
          </p>
        </div>
      ) : null}
    </div>
  )
}

/**
 * 分享抽屉（C 包第二半 / DEC-090 §2）：长图 / 文字 / 链接三选一。
 *
 * - 长图与文字都由 `lib/analyticsShareCard.ts` 从**当前渲染的 summary** 构建：
 *   页面缺数卡片就写「—」+ 原因，两边永远一致；
 * - 链接只复制本页地址（带 `?days=` 窗口参数），不含任何数据——数据在本机，
 *   对方打开同地址读到的是**他自己机器**的账本；
 * - canvas 在 jsdom 里拿不到 2D 上下文：`getContext` 返回 null 时静默跳过绘制，
 *   测试环境只验证数据构建与文字版。
 */
function AnalyticsShareDrawer({
  open,
  onClose,
  summary,
  days,
}: {
  open: boolean
  onClose: () => void
  summary: AnalyticsSummary
  days: number
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  const shareData: AnalyticsShareCardData | null = useMemo(
    () => (summary.ok ? buildAnalyticsShareCard({ summary, days }) : null),
    [summary, days],
  )

  useEffect(() => {
    if (!open || !shareData) return
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    drawAnalyticsShareCard(ctx, shareData)
  }, [open, shareData])

  if (!shareData) return null

  const downloadPng = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    canvas.toBlob((blob) => {
      if (!blob) {
        setFeedback('长图生成失败：浏览器拒绝导出画布。')
        return
      }
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `ladderstock-analytics-${days}d.png`
      anchor.click()
      URL.revokeObjectURL(url)
      setFeedback('长图已保存到下载目录。')
    }, 'image/png')
  }

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(analyticsShareSummary(shareData).join('\n'))
      setFeedback('文字版已复制。')
    } catch {
      setFeedback('复制失败：浏览器剪贴板不可用，请手动选择文本。')
    }
  }

  const copyLink = async () => {
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('days', String(days))
      url.searchParams.delete('state')
      url.searchParams.delete('demo')
      await navigator.clipboard.writeText(url.toString())
      setFeedback('链接已复制——只有地址，不含数据；对方打开看到的是他自己机器的账本。')
    } catch {
      setFeedback('复制失败：浏览器剪贴板不可用。')
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title="分享本页统计" width="md">
      <div className="flex flex-col gap-3">
        <p className="text-caption leading-body text-text-muted">
          长图与文字都取自本页已算出的聚合：缺数写「—」并给原因，不补 0、不重算；
          不含持仓明细与交易流水。链接只含地址与窗口参数，不含数据。
        </p>
        <canvas
          ref={canvasRef}
          width={ANALYTICS_SHARE_SIZE.width}
          height={ANALYTICS_SHARE_SIZE.height}
          aria-label="复盘统计分享长图预览"
          className="max-h-80 w-full rounded border border-border object-contain"
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={downloadPng}>
            下载长图 PNG
          </Button>
          <Button variant="secondary" onClick={() => void copyText()}>
            复制文字版
          </Button>
          <Button variant="secondary" onClick={() => void copyLink()}>
            复制链接
          </Button>
        </div>
        {feedback ? <p className="text-caption text-text-secondary">{feedback}</p> : null}
        <details className="rounded border border-border bg-surface-2 p-2">
          <summary className="cursor-pointer text-caption text-text-secondary">文字版预览</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-caption leading-body text-text-secondary">
            {analyticsShareSummary(shareData).join('\n')}
          </pre>
        </details>
      </div>
    </Drawer>
  )
}