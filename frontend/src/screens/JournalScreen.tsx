import { useMemo, useState } from 'react'
import { Download, Lock, NotebookPen } from 'lucide-react'

import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatInt, formatMoney, formatTime } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SectionCard,
  type Column,
} from '../components/ui'

/**
 * 交易日志（P-05 / DESIGN-SPEC §4.6 / UI-SPEC §3.4）。
 *
 * 隐私硬约束：日志只写本机 SQLite（`data/journal.sqlite`），本屏**没有任何**
 * AI 内容、同步、分享、云端入口；导出必须二次确认并说明去向。
 *
 * 本轮没有 `/api/journal`（后端把 journal.sqlite 的内容从一切响应里剥掉，
 * 前端也没有本机库桥接），因此本屏如实显示「本机日志库未接入」：
 * 统计卡一律「样本不足」、表格走空态，**不造任何交易、不补 0%**。
 */

type Direction = 'buy' | 'sell'

interface JournalRow {
  id: string
  time: string
  code: string
  name: string
  direction: Direction
  price: number | null
  quantity: number | null
  reason: string | null
  planned: boolean | null
}

const MIN_SAMPLE = 5

/** 方向是买卖，不是涨跌：`买入` 实心中性、`卖出` 描边中性，只有价格/盈亏才用涨跌色。 */
function DirectionBadge({ direction }: { direction: Direction }) {
  if (direction === 'buy') {
    return (
      <Badge tone="neutral" className="bg-text text-text-inverse">
        买入
      </Badge>
    )
  }
  return <Badge tone="neutral">卖出</Badge>
}

const JOURNAL_COLUMNS: Array<Column<JournalRow>> = [
  {
    key: 'time',
    header: '时间',
    width: '128px',
    render: (row) => <span className="ldst-num text-text-secondary">{formatTime(row.time)}</span>,
  },
  {
    key: 'code',
    header: '代码',
    width: '92px',
    render: (row) => <span className="ldst-num text-text-secondary">{row.code || MISSING}</span>,
  },
  { key: 'name', header: '名称', render: (row) => <span className="truncate font-medium">{row.name || MISSING}</span> },
  {
    key: 'direction',
    header: '方向',
    width: '72px',
    render: (row) => <DirectionBadge direction={row.direction} />,
  },
  {
    key: 'price',
    header: '价格',
    align: 'right',
    width: '96px',
    render: (row) => <span className="ldst-num">{formatMoney(row.price)}</span>,
  },
  {
    key: 'quantity',
    header: '数量',
    align: 'right',
    width: '88px',
    render: (row) => <span className="ldst-num">{row.quantity === null ? MISSING : formatInt(row.quantity)}</span>,
  },
  {
    key: 'reason',
    header: '理由标签',
    render: (row) => (row.reason ? <Badge tone="neutral">{row.reason}</Badge> : <span className="text-text-muted">{MISSING}</span>),
  },
  {
    key: 'planned',
    header: '按计划',
    width: '88px',
    align: 'center',
    render: (row) =>
      row.planned === null ? (
        <span className="text-text-muted">{MISSING}</span>
      ) : (
        <Badge tone={row.planned ? 'ok' : 'warn'}>{row.planned ? '是' : '否'}</Badge>
      ),
  },
]

/** 纪律统计卡：样本 <5 时显示「样本不足」，绝不显示 0%。 */
function DisciplineCard({
  label,
  formula,
  sample,
}: {
  label: string
  formula: string
  sample: number
}) {
  const enough = sample >= MIN_SAMPLE
  return (
    <MetricCard
      label={label}
      value={
        enough ? (
          MISSING
        ) : (
          <span className="text-subtitle font-medium text-text-secondary">样本不足</span>
        )
      }
      note={enough ? `样本 ${formatInt(sample)} 笔` : `样本 ${formatInt(sample)} 笔 · 少于 ${MIN_SAMPLE} 笔不计算`}
      badge={<Badge tone="neutral" title={formula}>口径</Badge>}
    />
  )
}

export default function JournalScreen() {
  const forced = useForcedState()
  const [notice, setNotice] = useState<string | null>(null)

  // 本轮没有任何本机日志数据源：结果集恒为空，状态如实是「空态」。
  const rows = useMemo<JournalRow[]>(() => [], [])
  const derived = useMemo<PageState>(() => (rows.length === 0 ? 'empty' : 'normal'), [rows.length])
  const state = resolveState(forced, derived)

  const recordBlockedReason = '本机日志写入由 ldst journal 命令提供，接口本轮未接入'

  const summary =
    state === 'offline'
      ? '本地服务未连接'
      : state === 'error'
        ? '本地日志库不可读'
        : `本机私有 · 共 ${formatInt(rows.length)} 笔 · 最近记录 ${rows.length > 0 ? formatTime(rows[0].time) : MISSING}`

  const header = (
    <PageHeader
      title="交易日志"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="primary"
            disabled
            disabledReason={recordBlockedReason}
            leadingIcon={<NotebookPen className="size-3.5" aria-hidden="true" />}
          >
            记录一笔
          </Button>
          <Button
            variant="secondary"
            disabled
            disabledReason="导出需要先接入本机日志库；导出会二次确认并说明文件去向"
            leadingIcon={<Download className="size-3.5" aria-hidden="true" />}
          >
            导出 CSV
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={4} rows={5} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock
          title="本地日志库无法读取"
          detail="运行 ldst doctor 检查数据目录权限；本屏不会上传或重建任何交易记录。"
          onRetry={() => setNotice('仍无法读取：请先在终端运行 ldst doctor 检查 data/ 目录权限。')}
          retryLabel="重新检查"
        />
        {notice && (
          <p className="text-caption text-text-secondary" role="status">
            {notice}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'offline' && <OfflineBanner onRetry={() => setNotice('本机日志不依赖行情服务：离线时仍可查看本机记录。')} />}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="本机日志库未接入"
          detail="前端还没有读取 data/journal.sqlite 的通道，统计与交易列表暂不可用；日志本身仍只留在本机。"
        />
      )}

      <Banner tone="info" title="本机私有">
        交易日志、持仓、自选只写入本机 SQLite，不上传、不进 AI、不被 MCP 读取。
      </Banner>

      {notice && (
        <p className="text-caption text-text-secondary" role="status">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 min-[1024px]:grid-cols-2 min-[1280px]:grid-cols-4">
        <DisciplineCard label="按计划执行率" formula="按计划的笔数 ÷ 总笔数" sample={rows.length} />
        <DisciplineCard label="追高次数" formula="买入价高于当日均价 3% 以上的笔数" sample={rows.length} />
        <DisciplineCard label="止损执行率" formula="触及止损线当日卖出的笔数 ÷ 触止损笔数" sample={rows.length} />
        <DisciplineCard label="连亏后加仓次数" formula="同日两笔亏损后继续加仓的次数" sample={rows.length} />
      </div>

      <SectionCard
        title="交易记录"
        subtitle={`共 ${formatInt(rows.length)} 笔 · 时间倒序 · 只读展示本机记录`}
          bodyClassName="p-0"
        actions={<Badge tone="neutral" icon={<Lock className="size-3" aria-hidden="true" />}>本机</Badge>}
      >
        <DataTable<JournalRow>
          columns={JOURNAL_COLUMNS}
          rows={rows}
          rowKey={(row) => row.id}
          caption="交易记录（本机 SQLite），时间倒序"
          density="default"
          maxHeight="480px"
          empty={
            <EmptyState
              title="从今天的一笔开始"
              description="记录代码、价格、理由，30 秒即可。写入通道由 ldst journal 提供，本轮前端未接入，因此这里不显示任何示例数据。"
            />
          }
        />
      </SectionCard>

      <SectionCard title="日志写在哪、谁能读" subtitle="隐私边界（本屏不出现任何 AI 内容）" tone="neutral" bodyClassName="p-4">
        <ul className="flex flex-col gap-2 text-body leading-body text-text-secondary">
          <li>
            · 存储位置：本机 <code>data/journal.sqlite</code>，随数据目录一起备份或删除。
          </li>
          <li>
            · 读取方式：<code>ldst journal</code> 命令（本轮前端未接入读取通道）。
          </li>
          <li>· 边界：不上传、不进 AI 上下文、不被 MCP 读取；本屏没有同步、分享、云端入口。</li>
          <li>· 口径：纪律统计少于 {MIN_SAMPLE} 笔一律显示「样本不足」，不显示 0%。</li>
        </ul>
      </SectionCard>

      <p className="text-caption text-text-muted">
        本屏不提供盈亏排行榜、收益预测或「AI 点评你的交易」。
      </p>
    </div>
  )
}
