import { formatTime, MISSING, formatAmount, formatChangePct } from '../../lib/format'
import { quoteSourceLabel, indexMarketLabel, type IndicesPayload, type IndexCell } from '../../api/radar'
import {
  Button,
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
 * 版式是 4 列 × 2 行的算术结论（§1.2 / §12-L02）：`text-section`（20px）的点位
 * `12,641.86` 需要 132px，1440 档单排 8 格只有 128px，放不下；8 格在 4 列下自然回流成两行。
 *
 * 三条缺数口径（§2.2 / §2.3）：
 * - 点位取不到 = `—`，**不显示 0**；第三行落到「源未接入」或「点位未取到」二者之一，不留白；
 * - 涨跌幅取不到 = `—` + 无箭头，点位照常显示（两者是独立事实，不互相抹掉）；
 * - 端点 404 = 整块「指数数据源未接入」空态，不回落演示数据。
 *
 * 标题仍用「大盘与全球指数」：后端恒定回 8 格，港股两格以「源未接入」占位（QUOT-471
 * 无合规源，按 DEC-006 闸门 4 不自采），八格全部渲染，故不降级为「A 股大盘指数」。
 */

/** 一格的三行：名称 + 市场后缀 / 点位 / 涨跌或缺席原因。 */
function IndexCellView({ cell }: { cell: IndexCell }) {
  const label = indexMarketLabel(cell.market)
  const change = formatChangePct(cell.change_pct)
  const missing = cell.price === null
  // 「源未接入」与「点位未取到」是两种缺席：整格不可用（`available=false`）才是前者
  const missingNote = !cell.available ? '源未接入' : '点位未取到'
  return (
    <div className="flex min-h-metric flex-col justify-between gap-1 rounded border border-border bg-surface p-3">
      <div className="flex items-center gap-1 text-caption text-text-secondary">
        <span className="min-w-0 truncate" title={cell.name ?? MISSING}>
          {cell.name ?? MISSING}
        </span>
        {/* 后缀是身份说明不是状态，用行内标签不用 Badge（§2.1）；取不到就不显示这一行 */}
        {label && <span className="shrink-0 text-text-muted">{label}</span>}
      </div>
      <div className="truncate text-section font-semibold ldst-num text-text" title={missing ? missingNote : undefined}>
        {formatAmount(cell.price)}
      </div>
      {missing ? (
        <div className="truncate text-caption text-text-muted" title={cell.reason ?? undefined}>
          {missingNote}
        </div>
      ) : (
        <div className="flex items-center gap-1">
          <SignedNumber {...change} className="text-caption" />
          {change.missing && <span className="text-caption text-text-muted">涨跌幅未取到</span>}
        </div>
      )}
    </div>
  )
}

export function HomeIndexStrip({ view }: { view: BlockView<IndicesPayload> }) {
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const cells = payload?.cells ?? []
  const missingCells = cells.filter((cell) => cell.price === null)
  const quoteTime = cells
    .map((cell) => cell.as_of)
    .filter((value): value is string => Boolean(value))
    .sort()
    .pop()
  // 非交易时段的 `as_of` 就是上一交易日收盘时刻，必须和口径句一起显示，不得当成当日（§9.1）
  const closed = payload?.freshness === 'cached' || payload?.freshness === 'archive'

  return (
    <SectionCard
      title="大盘与全球指数"
      tone={cells.length > 0 && missingCells.length > 0 ? 'warn' : 'neutral'}
      subtitle={
        <span className="flex flex-wrap items-center gap-1">
          <span>{closed ? '休市 · 显示最近交易日读数' : '快照 · 不自动刷新'}</span>
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
      bodyClassName="p-3"
    >
      {view.state === 'loading' && (
        <div className="grid grid-cols-2 gap-4 desk:grid-cols-4" aria-hidden="true">
          {Array.from({ length: 8 }, (_, index) => (
            <div key={index} className="min-h-metric rounded border border-border bg-surface-2" />
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

      <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
        {payload?.footnote ?? '指数为行情快照，不是逐笔实时'}；港股 / 美股指数源未接入时该格显示「{MISSING}」与原因，不补 0。
        源：<span className="ldst-num">{(payload?.datasets ?? ['index_quote']).map(quoteSourceLabel).join(' + ')}</span>
      </p>
    </SectionCard>
  )
}
