import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, RefreshCw } from 'lucide-react'

import { isLimitPoolEndpointMissing, type LimitPoolRow } from '../../api/limitPool'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Banner,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorBlock,
  Skeleton,
  SkeletonRows,
  type Column,
} from '../../components/ui'
import { formatBoards, formatInt, formatSealTime, localDateIso, MISSING } from '../../lib/format'
import { SourceStatusBadge } from '../industry/sourceBadges'
import { DEMO_LIMIT_POOL_DATE } from './demo'
import { useLimitPoolDemoEnabled, useLimitUpView } from './useLimitUpView'

/**
 * 涨停样本（`/limit-up`，挂在「验证」分组下的二级入口）。
 *
 * 只读 `GET /api/limit-pool`，页面不重算任何指标：
 * - 连板数默认降序，点击表头可切换；
 * - 缺失值显示 `—` + 悬浮原因，不补 0；
 * - 封板时间按 §8 把后端裸 `HHMMSS` 格式化成 `HH:mm:ss`，不换算时区、不猜；
 * - 完整度一律走同源徽标 `../industry/sourceBadges`（§5.5），演示水印归 `PageShell`（R-18 / QUOT-320）。
 */

function MissingCell({ reason }: { reason: string }) {
  return (
    <span className="text-text-muted" title={reason}>
      {MISSING}
      <span className="sr-only">{reason}</span>
    </span>
  )
}

/** 连板数排序：缺失值按最小值参与排序，默认降序时落在末尾。 */
function compareStreak(a: LimitPoolRow, b: LimitPoolRow): number {
  return (a.streak ?? Number.NEGATIVE_INFINITY) - (b.streak ?? Number.NEGATIVE_INFINITY)
}

/**
 * 加载骨架：本屏正文只有一张表 + 一行说明，没有指标卡。
 *
 * `ScreenSkeleton` 的卡阵写死在 `PageShell.tsx:84`，会先排 2 张不存在的卡再跳成宽表
 * （清单 M-9，需要它补 `layout` 插槽）；这里用组件层 C-18 的件拼同构骨架。
 */
function TableSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载涨停样本…</span>
      <div className="rounded border border-border bg-surface">
        <div className="flex h-row items-center gap-3 border-b border-border bg-surface-2 px-3">
          <Skeleton className="w-20" />
          <Skeleton className="w-24" />
          <Skeleton className="w-14" />
          <Skeleton className="w-16" />
          <Skeleton className="w-16" />
          <Skeleton className="w-36" />
        </div>
        <SkeletonRows rows={8} slots={['14%', '18%', '8%', '10%', '10%', '30%']} />
      </div>
      <Skeleton className="h-3 w-2/3" />
    </div>
  )
}

export default function LimitUpScreen() {
  const [params] = useSearchParams()
  const demo = useLimitPoolDemoEnabled()
  const date = params.get('date') ?? (demo ? DEMO_LIMIT_POOL_DATE : localDateIso())

  const { forced, state, payload, error, reload } = useLimitUpView({ date })

  const columns = useMemo<Array<Column<LimitPoolRow>>>(
    () => [
      {
        key: 'code',
        header: '代码',
        width: '88px',
        numeric: true,
        render: (row) => row.code,
      },
      {
        key: 'name',
        header: '名称',
        width: '176px',
        truncate: true,
        cellTitle: (row) => row.name ?? '后端未提供名称',
        render: (row) => (row.name === null ? <MissingCell reason="后端未提供名称" /> : row.name),
      },
      {
        key: 'streak',
        header: '连板数',
        align: 'right',
        width: '88px',
        numeric: true,
        sort: compareStreak,
        render: (row) =>
          row.streak === null ? <MissingCell reason="后端未提供连板数" /> : formatBoards(row.streak),
      },
      {
        key: 'first_seal_time',
        header: '首次封板',
        align: 'right',
        width: '144px',
        numeric: true,
        render: (row) =>
          row.first_seal_time === null ? (
            <MissingCell reason="后端未提供首次封板时间" />
          ) : (
            formatSealTime(row.first_seal_time)
          ),
      },
      {
        key: 'last_seal_time',
        header: '末次封板',
        align: 'right',
        width: '144px',
        numeric: true,
        render: (row) =>
          row.last_seal_time === null ? (
            <MissingCell reason="后端未提供末次封板时间" />
          ) : (
            formatSealTime(row.last_seal_time)
          ),
      },
      {
        key: 'reason',
        header: '涨停原因',
        truncate: true,
        cellTitle: (row) => row.reason ?? '后端未提供涨停原因',
        render: (row) =>
          row.reason === null ? <MissingCell reason="后端未提供涨停原因" /> : row.reason,
      },
    ],
    [],
  )

  const header = (
    <PageHeader
      title="涨停样本"
      subtitle={
        payload ? (
          <span>
            <span className="ldst-num">{payload.date || date}</span> · <span className="ldst-num">{formatInt(payload.count)}</span>{' '}
            只 · 来源 {payload.source_detail || MISSING}
          </span>
        ) : (
          <span className="ldst-num">{date}</span>
        )
      }
      badge={
        <>
          <DemoStateBadge state={forced} />
          {payload && <SourceStatusBadge status={payload.data_source_status} detail={payload.reason} />}
        </>
      }
      actions={
        <Button
          variant="primary"
          onClick={reload}
          disabled={state === 'loading'}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新
        </Button>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <TableSkeleton />
      </div>
    )
  }

  if (state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={reload} />
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title="当日涨停池未同步"
            description={
              <>
                数据源暂缺：本机只读 API 未连接。先运行 <code className="ldst-num">ldst run</code>，再执行{' '}
                <code className="ldst-num">ldst sync</code> 后回来。
              </>
            }
            action={
              <Button variant="secondary" onClick={reload}>
                重新检查
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock
          variant="banner"
          title="涨停样本取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty' || !payload) {
    const endpointMissing = payload === null && isLimitPoolEndpointMissing(error)
    return (
      <div className="flex flex-col gap-5">
        {header}
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title="当日涨停池未同步"
            description={
              <>
                {endpointMissing
                  ? '数据源暂缺：本机只读接口 GET /api/limit-pool 尚未接入，接入后自动生效；当前不展示任何数据。'
                  : (payload?.reason ?? '本机没有该交易日的涨停池数据。')}{' '}
                先执行 <code className="ldst-num">ldst sync</code> 再回来。
              </>
            }
            action={
              <Button variant="secondary" onClick={reload}>
                刷新
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="danger" title="部分涨停池字段无数据">
          {payload.reason ?? '部分记录缺少字段'}。缺失值显示「{MISSING}」，不补 0；页面不重算任何指标。
        </Banner>
      )}

      <DataTable<LimitPoolRow>
        columns={columns}
        rows={payload.rows}
        rowKey={(row) => row.code}
        caption={`${payload.date || date} 涨停样本（${formatInt(payload.count)} 只）；连板数默认降序，点击表头切换`}
        density="compact"
        maxHeight="calc(100vh - 280px)"
        initialSort={{ key: 'streak', dir: 'desc' }}
        sortToggle="flip"
        empty={
          <EmptyState
            compact
            title="当日涨停池未同步"
            description="先执行 ldst sync 再回来。"
            action={
              <Button variant="secondary" onClick={reload}>
                刷新
              </Button>
            }
          />
        }
      />

      <p className="text-caption leading-body text-text-muted">
        封板时间是后端裸 <code className="ldst-num">HHMMSS</code>，本页按 §8 格式化为{' '}
        <span className="ldst-num">HH:mm:ss</span>，不做时区换算；「{MISSING}」表示后端未提供该字段，不补 0。
        数据只读展示，页面不重算任何指标。
      </p>
    </div>
  )
}
