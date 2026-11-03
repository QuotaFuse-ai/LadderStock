import { RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import {
  isApiError,
  isEndpointMissing,
  type WatchlistItem,
  type WatchlistPayload,
} from '../../api'
import {
  Button,
  CardFooter,
  DataTable,
  EmptyState,
  ErrorBlock,
  SectionCard,
  SignedNumber,
  SkeletonRows,
  SourceBadges,
  type Column,
  type SourceBadgeMeta,
} from '../../components/ui'
import { countOrMissing, formatChangePct, formatMoney, MISSING } from '../../lib/format'

export interface HomeWatchlistCardProps {
  payload: WatchlistPayload | null
  status: 'loading' | 'ready' | 'error'
  error: unknown
  offline: boolean
  onRetry: () => void
  density: 'compact' | 'default' | 'comfortable'
}

/**
 * 自选是扁平 JSON（DEC-049），没有信封；徽标需要的四个公共字段在这里按载荷映射，
 * 映射只发生在取数口，不复制 `SourceBadges` 的标签表（SPEC-HOME-V2 §8）。
 */
function badgeMetaOf(payload: WatchlistPayload | null): SourceBadgeMeta | null {
  if (!payload) return null
  return {
    freshness: payload.as_of ? 'cached' : null,
    source_status: payload.data_source_status ?? null,
    source_detail: payload.price_source ?? null,
    data_as_of: payload.as_of ?? null,
  }
}

const columns: Array<Column<WatchlistItem>> = [
  {
    key: 'code',
    header: '代码',
    width: '88px',
    render: (row) => <span className="ldst-num text-text-secondary">{row.code || MISSING}</span>,
  },
  {
    key: 'name',
    header: '名称',
    truncate: true,
    render: (row) =>
      row.name ? (
        <span className="font-medium">{row.name}</span>
      ) : (
        <span className="text-text-muted">名称未接入</span>
      ),
  },
  {
    key: 'price',
    header: '现价',
    width: '112px',
    numeric: true,
    sort: (a, b) => (a.latest_price ?? -Infinity) - (b.latest_price ?? -Infinity),
    render: (row) => (
      <span className="text-text" title={row.latest_price === null ? row.reason ?? '现价未取到' : undefined}>
        {formatMoney(row.latest_price)}
      </span>
    ),
  },
  {
    key: 'change',
    header: '涨跌',
    width: '112px',
    numeric: true,
    className: 'whitespace-nowrap',
    sort: (a, b) => (a.change_pct ?? -Infinity) - (b.change_pct ?? -Infinity),
    render: (row) => {
      const change = formatChangePct(row.change_pct)
      return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
    },
  },
]

/**
 * 块 C：我的自选（SPEC-HOME-V2 §4）。
 *
 * 接口未接入（404）走块级空态并直说缺的是读接口；真实失败走块级 banner；
 * 两种情形都不回落演示数据（§5.6）。
 */
export function HomeWatchlistCard({
  payload,
  status,
  error,
  offline,
  onRetry,
  density,
}: HomeWatchlistCardProps) {
  const rows = payload?.items ?? []
  const count = countOrMissing(payload?.watch_count)
  const missingEndpoint = isEndpointMissing(error)
  const failed = status === 'error' && !missingEndpoint

  let body: ReactNode
  if (status === 'loading') {
    body = (
      <div className="p-4">
        <SkeletonRows rows={5} />
      </div>
    )
  } else if (failed) {
    body = (
      <div className="p-4">
        <ErrorBlock
          variant="banner"
          title="自选取数失败"
          detail={isApiError(error) ? error.message : '本机接口返回异常，稍后重试。'}
          onRetry={onRetry}
        />
      </div>
    )
  } else if (rows.length === 0) {
    body = (
      <div className="p-4">
        <EmptyState
          compact
          title={missingEndpoint ? '自选接口未接入' : '还没有自选'}
          description={
            offline
              ? '本地服务未连接，先运行 ldst run 再回来。'
              : missingEndpoint
                ? 'GET /api/watchlist 由 QUOT-377 S2 交付；本机隐私库已有 watchlist 表，缺的是读接口。'
                : payload?.reason ?? '自选只写本机 SQLite；用 ldst watch add <代码> 添加。'
          }
          action={
            offline ? undefined : (
              <Button
                size="md"
                variant="secondary"
                onClick={onRetry}
                leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
              >
                重新检查
              </Button>
            )
          }
        />
      </div>
    )
  } else {
    body = (
      <DataTable<WatchlistItem>
        columns={columns}
        rows={rows}
        rowKey={(row) => row.code}
        caption={`我的自选（${count} 只），现价与涨跌来自本机行情快照`}
        density={density}
      />
    )
  }

  return (
    <SectionCard
      title={`我的自选 · (${count})`}
      subtitle="本机回环只读 · 快照"
      actions={<SourceBadges envelope={badgeMetaOf(payload)} />}
      bodyClassName="p-0"
    >
      {body}
      <CardFooter className="border-t border-border bg-transparent">
        现价与涨跌来自本机行情快照，非逐笔实时；自选只存本机，不上传、不进 AI。
      </CardFooter>
    </SectionCard>
  )
}