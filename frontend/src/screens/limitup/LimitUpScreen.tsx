import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, RefreshCw } from 'lucide-react'

import { isLimitPoolEndpointMissing, type LimitPoolRow } from '../../api/limitPool'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Banner,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorBlock,
  type Column,
} from '../../components/ui'
import { localDateIso, MISSING } from '../../lib/format'
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
            <span className="truncate text-body text-text">{row.name}</span>
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
        payload
          ? `${payload.date || date} · ${payload.count} 只 · 来源 ${payload.source_detail || MISSING}`
          : date
      }
      badge={<DemoStateBadge state={forced} />}
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
        <ScreenSkeleton metrics={2} rows={8} />
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
        caption={`${payload.date || date} 涨停样本（${payload.count} 只）；连板数默认降序，点击表头切换`}
        density="compact"
        maxHeight="calc(100vh - 320px)"
        initialSort={{ key: 'streak', dir: 'desc' }}
        sortToggle="flip"
        empty={
          <EmptyState
            compact
            title="当日涨停池未同步"
            description="先执行 ldst sync 再回来。"
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
