import { formatTime, MISSING, formatAmount, formatChangePct } from '../../lib/format'
import { cn } from '../../lib/cn'
import { quoteSourceLabel, indexMarketLabel, type IndicesPayload, type IndexCell } from '../../api/radar'
import {
  Button,
  CardRefreshButton,
  EmptyState,
  ErrorBlock,
  FreshnessBadge,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
} from '../../components/ui'
import type { BlockView } from './useHomeSource'

/**
 * 首页 A 块「大盘与全球指数」（SPEC-HOME-V2 §2）。
 *
 * 一屏只回答「大盘在哪」：八格由后端白名单写死（4 个 A 股：上证 / 沪深300 / 深证成指 /
 * 创业板指；2 个美股：道琼斯 / 纳斯达克综合；2 个港股：恒生指数 / 恒生科技），
 * 前端不排序、不补格、不指定代码——`index_list` 是按涨幅排序的榜，不含这些指数。
 * 版式主次分层（QUOT-479，只改视觉）：A 股四格是主卡（`min-h-index-cell` 72px 三行），
 * 海外四格降为「名称 · 源未接入」胶囊行（28px 一行），拿到点位才升级为正常 cell——
 * 八格始终全部渲染，缺数不出 0、不补格、不回落。
 *
 * 三条缺数口径（§2.2 / §2.3）：
 * - 点位取不到 = `—`，**不显示 0**；第三行落到「源未接入」或「点位未取到」二者之一，不留白；
 * - 涨跌幅取不到 = `—` + 无箭头，点位照常显示（两者是独立事实，不互相抹掉）；
 * - 端点 404 = 整块「指数数据源未接入」空态，不回落演示数据。
 *
 * 标题仍用「大盘与全球指数」：后端恒定回 8 格，港股两格自 QUOT-560 起走
 * `index_quote_hk`（tickhub_us 港股指数别名，零 key），取不到时与其余格一样按格降级，
 * 八格全部渲染，故不降级为「A 股大盘指数」。
 */

/** 一格的三行：名称 + 市场后缀 / 点位 / 涨跌或缺席原因。 */
function IndexCellView({ cell }: { cell: IndexCell }) {
  const label = indexMarketLabel(cell.market)
  const change = formatChangePct(cell.change_pct)
  const missing = cell.price === null
  // 「源未接入」与「点位未取到」是两种缺席：整格不可用（`available=false`）才是前者
  const missingNote = !cell.available ? '源未接入' : '点位未取到'
  // A 股恒为主卡（哪怕某格缺数也走三行，口径不变）；海外行只有取到点位才升级为正常 cell
  const primary = label === 'A 股' || !missing
  if (!primary) {
    return (
      <div
        className="flex h-7 items-center gap-1 rounded-full bg-surface-2 px-3 text-11px text-text-muted"
        title={`未接入时显示「${MISSING}」，不补 0。${cell.reason ?? ''}`}
      >
        <span className="min-w-0 truncate text-text-secondary">{cell.name ?? MISSING}</span>
        <span className="shrink-0">· {missingNote}</span>
      </div>
    )
  }
  return (
    <div className="flex min-h-index-cell flex-col justify-between gap-0.5 rounded border border-border bg-surface p-2">
      <div className="flex items-center gap-1 text-11px text-text-secondary">
        <span className="min-w-0 truncate" title={cell.name ?? MISSING}>
          {cell.name ?? MISSING}
        </span>
        {/* 后缀是身份说明不是状态，用行内标签不用 Badge（§2.1）；取不到就不显示这一行 */}
        {label && <span className="shrink-0 text-text-muted">{label}</span>}
      </div>
      <div className="truncate text-section ldst-num text-text" title={missing ? missingNote : undefined}>
        {formatAmount(cell.price)}
      </div>
      {missing ? (
        <div className="truncate text-11px text-text-muted" title={cell.reason ?? undefined}>
          {missingNote}
        </div>
      ) : (
        <div className="flex items-center gap-1">
          <SignedNumber {...change} className="text-11px" />
          {change.missing && <span className="text-11px text-text-muted">涨跌幅未取到</span>}
        </div>
      )}
    </div>
  )
}

export interface HomeIndexStripProps {
  view: BlockView<IndicesPayload>
  /** 交易所是否在连续竞价时段（真判定，见 `lib/tradingSession.ts`）；休市时说「休市」并停轮询。 */
  session: boolean
  /** 轮询节拍（秒）；盘中文案「每 N 秒刷新」读它，与真实 `usePolling` 同源，不写字面量。 */
  pollSeconds: number
  /** 本轮已发出的指数请求数；只作 `data-*` 暴露，不改视觉（验收「2 分钟 ≈ 8 次」按它取证）。 */
  pollTicks?: number
  /** 连续失败次数；退避中时值 > 0。 */
  pollFailures?: number
  className?: string
}

export function HomeIndexStrip({ view, session, pollSeconds, pollTicks = 0, pollFailures = 0, className }: HomeIndexStripProps) {
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const cells = payload?.cells ?? []
  const missingCells = cells.filter((cell) => cell.price === null)
  const quoteTime = cells
    .map((cell) => cell.as_of)
    .filter((value): value is string => Boolean(value))
    .sort()
    .pop()
  // 休市判据是「不在竞价时段」，不是 `freshness`——后端指数条恒发 `realtime`，
  // 拿它当休市信号永远为假（QUOT-470 的 bug 就出在这里）。休市读数即上一交易日收盘，和口径句一起显示（§9.1）。
  const closed = !session

  return (
    <SectionCard
      title="大盘与全球指数"
      tone="neutral"
      className={cn('flex flex-col', className)}
      subtitle={
        <span className="flex flex-wrap items-center gap-1">
          <span data-poll-ticks={pollTicks} data-poll-failures={pollFailures}>
            {closed ? '休市 · 显示最近交易日读数' : `交易时段 · 每 ${pollSeconds} 秒刷新`}
          </span>
          {payload && (
            <>
              <FreshnessBadge freshness={payload.freshness ?? null} asOf={payload.data_as_of} />
              <SourceStatusBadge
                status={payload.source_status ?? payload.data_source_status}
                detail={
                  missingCells.length > 0
                    ? missingCells.map((cell) => `${cell.name ?? cell.code}：${cell.reason ?? '未取到'}`).join('；')
                    : null
                }
              />
            </>
          )}
          {quoteTime && <span className="ldst-num text-text-muted">{formatTime(quoteTime)}</span>}
        </span>
      }
      actions={
        <CardRefreshButton
          label="刷新大盘与全球指数"
          onClick={view.onRefresh}
          loading={view.refreshing}
        />
      }
      bodyClassName="flex grow flex-col p-3"
    >
      {view.state === 'loading' && (
        <div className="grid grid-cols-2 gap-4 desk:grid-cols-4" aria-hidden="true">
          {Array.from({ length: 8 }, (_, index) => (
            <div key={index} className="min-h-index-cell rounded border border-border bg-surface-2" />
          ))}
        </div>
      )}

      {view.state === 'error' && (
        <ErrorBlock
          variant="banner"
          title="指数条取数失败"
          detail={view.error instanceof Error ? view.error.message : String(view.error ?? '')}
          onRetry={view.onRetry}
          retryLabel="重试"
        />
      )}

      {view.state === 'empty' &&
        (view.endpointMissing ? (
          <EmptyState
            compact
            title="指数数据源未接入"
            description="本机没有指数数据集；`GET /api/indices` 由 QUOT-377 S2 交付，接入后此块自动出现。"
            action={
              <Button variant="secondary" onClick={view.onRetry}>
                重新检查
              </Button>
            }
          />
        ) : (
          <EmptyState
            compact
            title="这个交易日没有指数读数"
            description={view.offline ? '本机服务未连接，指数读数不上屏。' : (payload?.reason ?? '指数快照里所有格都没有点位。')}
            action={
              <Button variant="secondary" onClick={view.onRetry}>
                重新检查
              </Button>
            }
          />
        ))}

      {(view.state === 'normal' || view.state === 'partial') && (
        <div className="grid grid-cols-2 gap-4 desk:grid-cols-4">
          {cells.map((cell) => (
            <IndexCellView key={cell.code} cell={cell} />
          ))}
        </div>
      )}

      {view.state === 'offline' && (
        <EmptyState compact title="本机服务未连接" description="指数条不落缓存，断连时不显示上一次的读数。" />
      )}

      <div className="mt-auto pt-2">
        <p className="border-t border-border pt-1 text-11px leading-body text-text-muted">
          {payload?.footnote ?? '指数为行情快照，不是逐笔实时'} · 源：
          <span className="ldst-num">{(payload?.datasets ?? ['index_quote']).map(quoteSourceLabel).join(' + ')}</span>
        </p>
      </div>
    </SectionCard>
  )
}
