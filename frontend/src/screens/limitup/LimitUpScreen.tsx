import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, RefreshCw } from 'lucide-react'

import { isLimitPoolEndpointMissing, type LimitPoolRow } from '../../api/limitPool'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Badge,
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
import { formatInt, localDateIso, MISSING } from '../../lib/format'
import { DEMO_LIMIT_POOL_DATE } from './demo'
import { useLimitPoolDemoEnabled, useLimitUpView } from './useLimitUpView'

/**
 * 涨停样本（`/limit-up`，挂在「验证」分组下的二级入口）。
 *
 * 只读 `GET /api/limit-pool`，页面不重算任何指标：
 * - 连板数默认降序，点击表头可切换；
 * - 缺失值显示 `—` + 悬浮原因，不补 0；
 * - 封板时间按后端给的 HHMMSS 原样展示，不换算时区。
 */

/**
 * 演示源水印（清单 §1-A / M-11 的屏侧止血）。
 *
 * 根因在 `components/PageShell.tsx:47` 的 `DemoStateBadge` 只认 `?state=`，`?demo=1` 无标识；
 * 本轨不许改 PageShell（S3c 负责），所以三屏各挂一枚同写法的 C-13 徽标。
 * M-11 落进 PageShell 后，这三处一并删除。
 */
function DemoWatermark() {
  return <Badge tone="info" title="本页数据来自内置演示源，不是本机真实行情">演示数据 · ?demo=1</Badge>
}

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
        render: (row) => <span className="ldst-num text-body text-text">{row.code}</span>,
      },
      {
        key: 'name',
        header: '名称',
        width: '112px',
        render: (row) =>
          row.name === null ? (
            <MissingCell reason="后端未提供名称" />
          ) : (
            <span className="truncate text-body text-text" title={row.name}>
              {row.name}
            </span>
          ),
      },
      {
        key: 'streak',
        header: '连板数',
        align: 'right',
        width: '88px',
        sort: compareStreak,
        render: (row) =>
          row.streak === null ? (
            <MissingCell reason="后端未提供连板数" />
          ) : (
            <span className="ldst-num text-body text-text">{row.streak}</span>
          ),
      },
      {
        key: 'first_seal_time',
        header: '首次封板',
        align: 'right',
        width: '96px',
        render: (row) =>
          row.first_seal_time === null ? (
            <MissingCell reason="后端未提供首次封板时间" />
          ) : (
            <span className="ldst-num text-body text-text">{row.first_seal_time}</span>
          ),
      },
      {
        key: 'last_seal_time',
        header: '末次封板',
        align: 'right',
        width: '96px',
        render: (row) =>
          row.last_seal_time === null ? (
            <MissingCell reason="后端未提供末次封板时间" />
          ) : (
            <span className="ldst-num text-body text-text">{row.last_seal_time}</span>
          ),
      },
      {
        key: 'reason',
        header: '涨停原因',
        render: (row) =>
          row.reason === null ? (
            <MissingCell reason="后端未提供涨停原因" />
          ) : (
            <span className="text-body text-text-secondary">{row.reason}</span>
          ),
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
          {demo && <DemoWatermark />}
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
        <Banner tone="warn" title="部分涨停池数据缺失">
          {payload.reason ?? '部分记录缺少字段'}。缺失值显示「—」，不补 0；页面不重算任何指标。
        </Banner>
      )}

      <DataTable<LimitPoolRow>
        columns={columns}
        rows={payload.rows}
        rowKey={(row) => row.code}
        caption={`${payload.date || date} 涨停样本（${formatInt(payload.count)} 只）；连板数默认降序，点击表头切换`}
        density="compact"
        maxHeight="calc(100vh - 320px)"
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
        封板时间为后端原始 HHMMSS，不做时区换算；「—」表示后端未提供该字段，不补 0。
        数据只读展示，页面不重算任何指标。
      </p>
    </div>
  )
}
