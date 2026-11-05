import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import {
  quoteSourceLabel,
  type WatchlistItem,
  type WatchlistPayload,
} from '../../api/radar'
import {
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  FreshnessBadge,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import { MISSING, countOrMissing, formatChangePct, formatTime } from '../../lib/format'
import { useDensity } from '../../lib/prefs'
import type { BlockView } from './useHomeSource'

/**
 * 首页 C 块「我的自选」（SPEC-HOME-V2 §4）。
 *
 * 4 列满宽单块：代码 / 名称 / 现价 / 涨跌，列宽 88 / 剩余 / 112 / 112 全落在 §7.3 刻度上。
 * 不做参考页的「名称 + 代码两行格」和「调整顺序」按钮：前者要新行高档，后者是排序的第二套入口。
 *
 * 缺数口径（§4.2）：代码缺 → 行不渲染（主键缺是脏数据）；名称缺 → 「名称未接入」，
 * 不拿代码顶替；现价 / 涨跌缺 → `—` + 原因，不回落到成本价。只数缺 → `—`，不 `?? 0`。
 */

const COLUMNS: Array<Column<WatchlistItem>> = [
  {
    key: 'code',
    header: '代码',
    width: '88px',
    render: (row) => <span className="ldst-num text-text-secondary">{row.code}</span>,
  },
  {
    key: 'name',
    header: '名称',
    truncate: true,
    render: (row) => (
      <span className={row.name ? 'font-medium' : 'text-text-muted'}>{row.name ?? '名称未接入'}</span>
    ),
  },
  {
    key: 'price',
    header: '现价',
    width: '112px',
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
    width: '112px',
    numeric: true,
    sort: (a, b) => (a.change_pct ?? Number.NEGATIVE_INFINITY) - (b.change_pct ?? Number.NEGATIVE_INFINITY),
    render: (row) => {
      const change = formatChangePct(row.change_pct)
      return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
    },
  },
]

export function HomeWatchlistCard({ view }: { view: BlockView<WatchlistPayload> }) {
  const density = useDensity()
  const navigate = useNavigate()
  const payload = view.state === 'normal' || view.state === 'partial' ? view.data : null
  const items = (payload?.items ?? []).filter((item) => Boolean(item.code))
  const count = countOrMissing(payload?.watch_count ?? (payload ? items.length : null))

  return (
    <SectionCard
      title={payload ? `我的自选 · (${count})` : '我的自选'}
      tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
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
            {payload.price_as_of && (
              <span className="ldst-num text-text-muted">{formatTime(payload.price_as_of)}</span>
            )}
          </span>
        ) : (
          '自选只存本机'
        )
      }
      actions={
        <Button variant="ghost" onClick={() => navigate('/watchlist')}>
          管理自选 →
        </Button>
      }
      bodyClassName="p-0"
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
          caption={`我的自选（${count} 只），现价与涨跌来自本机行情快照`}
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

      <p className="border-t border-border px-4 py-3 text-caption leading-body text-text-muted">
        现价与涨跌来自本机行情快照（{quoteSourceLabel(payload?.price_source ?? 'spot_quote')}），非逐笔实时；
        自选只存本机，不上传、不进 AI。
      </p>
    </SectionCard>
  )
}
