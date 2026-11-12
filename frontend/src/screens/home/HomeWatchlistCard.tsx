import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import {
  quoteSourceLabel,
  type WatchlistItem,
  type WatchlistPayload,
} from '../../api/radar'
import {
  Button,
  CardRefreshButton,
  DataTable,
  EmptyState,
  ErrorBlock,
  FreshnessBadge,
  PrivacyBadge,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import { MISSING, countOrMissing, formatChangePct, formatInboxTime } from '../../lib/format'
import { cn } from '../../lib/cn'
import { useDensity } from '../../lib/prefs'
import type { BlockView } from './useHomeSource'

/**
 * 首页「我的自选」紧凑摘要（QUOT-552 / SPEC-HOME-V2 v2.0 §4）。
 *
 * 首页只压 **3 列**：名称（含代码）/ 现价 / 涨跌，列宽 剩余/88/88；
 * 全列宽表原样保留在 `/watchlist`（管理自选 →），这里不丢信息只收起来。
 * 排序、溯源徽标（QUOT-478）、五态与缺数口径一条不动：
 * 代码缺 → 行不渲染；名称缺 → 「名称未接入」，不拿代码顶替；现价 / 涨跌缺 → `—`，不补 0。
 *
 * 隐私长文案收进卡头「仅本机」徽标（QUOT-552 §5.4）：hover 出全文、点击展开
 * `PrivacyNotice` 本体；加载中 / 失败 / 空态徽标常驻，不随数据状态消失。
 */

const COLUMNS: Array<Column<WatchlistItem>> = [
  {
    key: 'name',
    header: '名称',
    truncate: true,
    cellTitle: (row) => `${row.name ?? '名称未接入'} ${row.code}`,
    render: (row) => (
      <>
        <span className={row.name ? 'font-medium' : 'text-text-muted'}>{row.name ?? '名称未接入'}</span>{' '}
        <span className="ldst-num text-caption text-text-muted">{row.code}</span>
      </>
    ),
  },
  {
    key: 'price',
    header: '现价',
    width: '88px',
    numeric: true,
    sort: (a, b) => (a.latest_price ?? Number.NEGATIVE_INFINITY) - (b.latest_price ?? Number.NEGATIVE_INFINITY),
    render: (row) => (
      <span className={row.latest_price === null ? 'text-text-muted' : undefined}>
        {row.latest_price === null ? MISSING : row.latest_price.toFixed(2)}
      </span>
    ),
  },
  {
    key: 'change',
    header: '涨跌',
    width: '88px',
    numeric: true,
    sort: (a, b) => (a.change_pct ?? Number.NEGATIVE_INFINITY) - (b.change_pct ?? Number.NEGATIVE_INFINITY),
    render: (row) => {
      const change = formatChangePct(row.change_pct)
      return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
    },
  },
]

export function HomeWatchlistCard({ view, className }: { view: BlockView<WatchlistPayload>; className?: string }) {
  const density = useDensity()
  const navigate = useNavigate()
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const items = (payload?.items ?? []).filter((item) => Boolean(item.code))
  const count = countOrMissing(payload?.watch_count ?? (payload ? items.length : null))
  const privacyBadge = <PrivacyBadge subject="自选" />

  return (
    <SectionCard
      title={payload ? `我的自选 · (${count})` : '我的自选'}
      tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
      className={cn('flex flex-col', className)}
      subtitle={
        payload ? (
          <span className="flex flex-wrap items-center gap-1">
            <FreshnessBadge
              freshness={payload.freshness ?? null}
              asOf={payload.price_as_of ?? payload.data_as_of}
              sourceDetail={payload.source_detail}
            />
            <SourceStatusBadge
              status={payload.data_source_status}
              detail={payload.reason ?? payload.source_detail}
            />
          </span>
        ) : undefined
      }
      actions={
        <>
          {/* 隐私徽标常驻卡头操作区（§5.4）：副标题让位给溯源徽标 + 快照时间，一行放下 */}
          {privacyBadge}
          <CardRefreshButton
            label="刷新我的自选"
            onClick={view.onRefresh}
            loading={view.refreshing}
          />
        </>
      }
      bodyClassName="flex grow flex-col p-0"
    >
      {view.state === 'loading' && (
        <div className="flex flex-col gap-2 p-4" aria-hidden="true">
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="h-row rounded bg-surface-2" />
          ))}
        </div>
      )}

      {view.state === 'error' && (
        <div className="p-4">
          <ErrorBlock
            variant="banner"
            title="自选取数失败"
            detail={view.error instanceof Error ? view.error.message : String(view.error ?? '')}
            onRetry={view.onRetry}
            retryLabel="重试"
          />
        </div>
      )}

      {view.state === 'offline' && (
        <div className="p-4">
          <EmptyState compact title="本机服务未连接" description="自选只经 127.0.0.1 回环读取，断连时不显示上一次的结果。" />
        </div>
      )}

      {(view.state === 'normal' || view.state === 'partial') && (
        <DataTable<WatchlistItem>
          columns={COLUMNS}
          rows={items}
          rowKey={(row) => row.code}
          caption={`我的自选（${count} 只），现价与涨跌来自本机行情快照，非逐笔实时`}
          density={density}
          maxHeight="240px"
          empty={
            <EmptyState
              compact
              title="还没有自选"
              description="在「我的自选」页加一只 6 位代码，这里就会出现。"
            />
          }
        />
      )}

      {view.state === 'empty' &&
        (view.endpointMissing ? (
          <div className="p-4">
            <EmptyState
              compact
              title="自选接口未接入"
              description="本机看板没有注册 GET /api/watchlist；升级到当前版本后，这块会自动出现。"
              action={
                <Button variant="secondary" onClick={view.onRetry} leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}>
                  重新检查
                </Button>
              }
            />
          </div>
        ) : (
          <div className="p-4">
            <EmptyState
              compact
              title="还没有自选"
              description={payload?.reason ?? '在「我的自选」页加一只 6 位代码，这里就会出现。'}
              action={
                <Button variant="secondary" onClick={() => navigate('/watchlist')}>
                  管理自选
                </Button>
              }
            />
          </div>
        ))}

      {/* 一行页脚：口径短句 + 全表入口（322px 宽卡放不下长文案，全文在 title） */}
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border px-4 py-1">
        <p
          className="min-w-0 truncate text-11px leading-body text-text-muted"
          title={`现价与涨跌来自本机行情快照（${quoteSourceLabel(payload?.price_source ?? 'spot_quote')}），非逐笔实时；全列宽表在「我的自选」页。`}
        >
          源：{quoteSourceLabel(payload?.price_source ?? 'spot_quote')}
          {payload?.price_as_of ? ` · ${formatInboxTime(payload.price_as_of)}` : ''}
        </p>
        <button
          type="button"
          onClick={() => navigate('/watchlist')}
          className="inline-flex h-5 shrink-0 items-center rounded px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3 hover:text-text"
        >
          管理自选 →
        </button>
      </div>
    </SectionCard>
  )
}