import { RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import {
  isApiError,
  isEndpointMissing,
  type IndicesPayload,
  type LadderEnvelope,
} from '../../api'
import {
  Button,
  EmptyState,
  ErrorBlock,
  SectionCard,
  SignedNumber,
  Skeleton,
  SourceBadges,
} from '../../components/ui'
import { formatChangePct, MISSING } from '../../lib/format'

/** 市场后缀是身份说明，不是状态：用行内标签，不用 Badge（SPEC-HOME-V2 §2.1）。 */
const MARKET_LABEL: Record<NonNullable<IndicesPayload['items'][number]['market']>, string> = {
  cn: 'A 股',
  hk: '港股',
  us: '美股',
}

/** 指数点位是读数不是金额：`12,641.86` 两位小数 + 千分位，不走 `formatMoney` 的万 / 亿缩写。 */
function formatPoint(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return MISSING
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const CELL = 'flex min-h-metric flex-col justify-between gap-1 rounded border border-border bg-surface p-3'

function IndexCell({ item }: { item: IndicesPayload['items'][number] }) {
  const change = formatChangePct(item.change_pct)
  return (
    <article className={CELL}>
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-caption text-text-secondary">{item.name ?? MISSING}</span>
        {item.market && (
          <span className="shrink-0 text-caption text-text-muted">{MARKET_LABEL[item.market]}</span>
        )}
      </div>
      <span className="ldst-num truncate text-section font-semibold text-text">{formatPoint(item.value)}</span>
      {item.value === null ? (
        <span className="truncate text-caption text-text-muted" title={item.reason ?? undefined}>
          {item.reason || '点位未取到'}
        </span>
      ) : (
        <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} className="text-caption" />
      )}
    </article>
  )
}

function StripSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 rail:grid-cols-4 desk:grid-cols-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载指数快照…</span>
      {Array.from({ length: 8 }).map((_, index) => (
        <div key={index} className={CELL}>
          <Skeleton className="w-2/3" />
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="w-1/3" />
        </div>
      ))}
    </div>
  )
}

export interface HomeIndexStripProps {
  envelope: LadderEnvelope<IndicesPayload> | null
  status: 'loading' | 'ready' | 'error'
  error: unknown
  offline: boolean
  onRetry: () => void
}

/**
 * 块 A：大盘与全球指数（SPEC-HOME-V2 §2）。
 *
 * 只渲染后端 `items[]`，前端不排序、不补格；`value === null` 的格显示 `—` + 原因，
 * 块级只给「源未接入 / 取数失败」两种非正常态，不把单格缺数升格成整块错误。
 */
export function HomeIndexStrip({ envelope, status, error, offline, onRetry }: HomeIndexStripProps) {
  const items = envelope?.payload.items ?? []
  const missingEndpoint = isEndpointMissing(error)
  const failed = status === 'error' && !missingEndpoint

  const retry = (
    <Button
      size="md"
      variant="secondary"
      onClick={onRetry}
      leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
    >
      重新检查
    </Button>
  )

  let body: ReactNode
  if (status === 'loading') {
    body = <StripSkeleton />
  } else if (failed) {
    body = (
      <ErrorBlock
        variant="banner"
        title="指数快照取数失败"
        detail={isApiError(error) ? error.message : '本机接口返回异常，稍后重试。'}
        onRetry={onRetry}
      />
    )
  } else if (items.length === 0) {
    body = (
      <EmptyState
        compact
        title="指数数据源未接入"
        description={
          offline
            ? '本地服务未连接，先运行 ldst run 再回来。'
            : '本机数据集里还没有指数行情；接入 TickHub-CN 的 index_quote 后，此块会自动出现。'
        }
        action={offline ? undefined : retry}
      />
    )
  } else {
    body = (
      <div className="grid grid-cols-2 gap-4 rail:grid-cols-4 desk:grid-cols-4">
        {items.map((item) => (
          <IndexCell key={item.code} item={item} />
        ))}
      </div>
    )
  }

  return (
    <SectionCard
      title="大盘与全球指数"
      subtitle="快照 · 不自动刷新"
      actions={<SourceBadges envelope={envelope} />}
      bodyClassName="p-3"
    >
      {body}
      <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
        指数为行情快照，不是逐笔实时；港股 / 美股指数源未接入时该格显示「—」与原因，不补 0。
      </p>
    </SectionCard>
  )
}