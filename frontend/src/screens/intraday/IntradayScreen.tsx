import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  CircleDashed,
  CircleDot,
  CheckCircle2,
  CircleSlash2,
  Gauge,
  History,
  Layers,
  MinusCircle,
  Radar,
  RefreshCw,
  Timer,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import {
  isKnownSlotStatus,
  type IntradaySlotRow,
  type IntradaySnapshotPayload,
  type IntradaySummaryPayload,
} from '../../api/intraday'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SignedNumber,
  StatusDot,
  type Column,
  type DotTone,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { formatChangePct, formatInt, formatOneDecimal, localDateIso, MISSING } from '../../lib/format'
import type { PageState } from '../../lib/pageState'
import { describeApiError } from '../../api'
import type { IntradayStockReading } from '../../api/intraday'
import { DEMO_INTRADAY_DATE } from './demo'
import { useIntradayDemoEnabled, useIntradayView } from './useIntradayView'

/**
 * 盯盘 · 竞价与盘中（`/intraday`，QUOT-302 / DEC-035 §5）。
 *
 * 三个板块：**时点条**（当日 N 个时点各一格）→ **选中时点的快照节选**（昨日涨停池读数 /
 * 情绪面块 / `price_field` 说明）→ 逐只客观读数。
 *
 * 四条口径，写在这里是因为它们决定了页面结构：
 *
 * 1. **时点表来自后端**：格子数量与时刻全部是 `/api/intraday` 的 `slots[]`，页面没有
 *    自己的 `09:25 / 09:35 / …` 常量。采集侧加减时点（DEC-035 §2）不需要动这里。
 * 2. **不重算任何指标**：`count / fresh / avg_change_pct / seal_rate_pct / 1进2` 全部是
 *    载荷原值，与候选池屏同口径；缺失显示「—」+ 原因，**不显示 0**（DEC-028）。
 * 3. **状态双通道**：六种时点状态各有图形 + 文案，不只靠颜色（UI-SPEC §9 / §11）。
 *    `unknown` 状态原样透出文案，不折叠成「未取到」。
 * 4. **「不补跑」不是失败**：`skipped` 与没跑过的时点是**计划内的空**，文案是
 *    「已过窗口，不补跑」，不进错误态、不进空态（DEC-035 §2.3）。
 */

interface SlotVisual {
  label: string
  icon: LucideIcon
  dot: DotTone
  /** 单元格描边：让六种状态在不看颜色的情况下也能靠形状与文案区分 */
  cell: string
}

const SLOT_VISUALS: Record<string, SlotVisual> = {
  ok: { label: '已写入', icon: CheckCircle2, dot: 'ok', cell: 'border-ok' },
  degraded: { label: '降级写入', icon: AlertTriangle, dot: 'warn', cell: 'border-warn' },
  already: { label: '已存在·未覆盖', icon: CircleDot, dot: 'accent', cell: 'border-accent' },
  skipped: { label: '已过窗口，不补跑', icon: CircleSlash2, dot: 'idle', cell: 'border-dashed border-border-strong' },
  missing: { label: '未取到', icon: MinusCircle, dot: 'flat', cell: 'border-dashed border-border' },
  failed: { label: '抓取失败', icon: XCircle, dot: 'danger', cell: 'border-danger' },
}

/** `status.json` 里没有这条记录：与「跑过但没抓到读数」（`missing`）是两件事。 */
const UNRECORDED_VISUAL: SlotVisual = {
  label: '当天没跑过',
  icon: CircleDashed,
  dot: 'idle',
  cell: 'border-dashed border-border',
}

/** 未知状态：图形用中性占位，文案用后端原值——不猜、不改写。 */
const UNKNOWN_VISUAL: SlotVisual = {
  label: '未知状态',
  icon: CircleDashed,
  dot: 'flat',
  cell: 'border-border',
}

function slotVisual(row: IntradaySlotRow): SlotVisual {
  if (!row.recorded && row.status === 'missing') return UNRECORDED_VISUAL
  if (!isKnownSlotStatus(row.status)) {
    return { ...UNKNOWN_VISUAL, label: row.status }
  }
  return SLOT_VISUALS[row.status]
}

/** `price_field` 是采集侧的读数口径，不是时间；文案按取值映射。 */
const PRICE_FIELD_LABEL: Record<string, string> = {
  open: '开盘价（集合竞价成交价，涨跌幅按开盘价重算）',
  price: '最新价（连续竞价）',
}

function priceFieldText(value: string | null): string {
  if (value === null) return '未记录读数口径'
  return PRICE_FIELD_LABEL[value] ?? `未知读数口径：${value}`
}

// ---------------------------------------------------------------- 时点条

function SlotCell({
  row,
  selected,
  onSelect,
}: {
  row: IntradaySlotRow
  selected: boolean
  onSelect: (slot: string) => void
}) {
  const visual = slotVisual(row)
  const Icon = visual.icon
  return (
    <button
      type="button"
      onClick={() => onSelect(row.slot)}
      aria-pressed={selected}
      aria-label={`${row.slot} ${visual.label}`}
      className={cn(
        'flex min-w-0 flex-col items-start gap-1.5 rounded border bg-surface p-3 text-left transition-colors',
        'focus-visible:shadow-focus focus-visible:outline-none',
        visual.cell,
        selected ? 'ring-2 ring-accent' : 'hover:bg-surface-2',
      )}
    >
      <span className="ldst-num text-body font-semibold leading-tight text-text">{row.slot}</span>
      <span className="flex min-w-0 items-center gap-1.5">
        <Icon
          className={cn(
            'size-3.5 shrink-0',
            visual.dot === 'ok' && 'text-ok',
            visual.dot === 'warn' && 'text-warn',
            visual.dot === 'danger' && 'text-danger',
            visual.dot === 'accent' && 'text-accent',
            (visual.dot === 'flat' || visual.dot === 'idle') && 'text-text-muted',
          )}
          aria-hidden="true"
        />
        <span className="truncate text-caption font-medium text-text-secondary" title={visual.label}>
          {visual.label}
        </span>
      </span>
      <span className="ldst-num text-caption text-text-muted">
        {row.available ? `${readingCell(row.fresh)}/${readingCell(row.stocks)}` : MISSING}
      </span>
      <span className="sr-only">{`${selected ? '当前选中' : '可选'}时点`}</span>
    </button>
  )
}

function SlotRail({
  payload,
  selected,
  onSelect,
}: {
  payload: IntradaySummaryPayload
  selected: string | null
  onSelect: (slot: string) => void
}) {
  return (
    <Card>
      <CardHeader
        title="当日时点"
        subtitle={`${payload.snapshot_count}/${payload.slot_count} 个时点有快照${
          payload.status_updated_at ? ` · 状态更新 ${payload.status_updated_at}` : ''
        }`}
      />
      <CardBody>
        <div
          role="group"
          aria-label="当日快照时点"
          className="grid grid-cols-3 gap-2 desk:grid-cols-6"
        >
          {payload.slots.map((row) => (
            <SlotCell
              key={row.slot}
              row={row}
              selected={row.slot === selected}
              onSelect={onSelect}
            />
          ))}
        </div>
        <p className="mt-3 text-caption leading-body text-text-muted">
          六种时点状态以「图形 + 文案」双通道呈现，不只靠颜色；「已过窗口，不补跑」与「当天没跑过」
          都是计划内的空，不算加载失败。时点与时刻全部来自 <code className="ldst-num">GET /api/intraday</code> 的{" "}
          <code className="ldst-num">slots[]</code>，页面不持有自己的时点表。
        </p>
      </CardBody>
    </Card>
  )
}

// ---------------------------------------------------------------- 快照节选

function readingCell(value: number | null, render: (n: number) => string = formatInt): string {
  return value === null ? MISSING : render(value)
}

/** 百分比读数：数字后带 `%`；没有值就是「—」，不带单位。 */
function pctCell(value: number | null): string {
  return value === null ? MISSING : `${formatOneDecimal(value)}%`
}

function SnapshotDetail({
  slot,
  view,
}: {
  slot: IntradaySlotRow | null
  view: {
    snapshot: IntradaySnapshotPayload | null
    slotState: PageState
    slotError: unknown
    reloadSlot: () => void
  }
}) {
  if (slot === null) {
    return (
      <Card>
        <CardHeader title="时点快照" />
        <CardBody>
          <EmptyState compact title="没有时点" description="后端这次没有返回任何时点，页面不自己补时点表。" />
        </CardBody>
      </Card>
    )
  }

  if (view.slotState === 'loading') {
    return (
      <Card>
        <CardHeader title={`${slot.slot} 时点快照`} />
        <CardBody>
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">正在读取该时点的快照…</span>
            <div className="h-4 w-1/3 rounded bg-skeleton" />
            <div className="h-4 w-2/3 rounded bg-skeleton" />
            <div className="h-4 w-1/2 rounded bg-skeleton" />
          </div>
        </CardBody>
      </Card>
    )
  }

  if (view.slotState === 'error') {
    const described = describeApiError(view.slotError)
    return (
      <Card>
        <CardHeader title={`${slot.slot} 时点快照`} />
        <CardBody>
          <ErrorBlock title="快照取数失败" detail={described.detail} code={described.code} onRetry={view.reloadSlot} />
        </CardBody>
      </Card>
    )
  }

  const payload = view.snapshot
  const snapshot = payload?.snapshot ?? null

  if (snapshot === null) {
    const visual = slotVisual(slot)
    return (
      <Card>
        <CardHeader
          title={`${slot.slot} 时点快照`}
          subtitle={<StatusDot tone={visual.dot}>{visual.label}</StatusDot>}
        />
        <CardBody>
          {/* `skipped` / 没跑过的时点：这是计划内的空，不是取数失败（DEC-035 §2.3） */}
          <EmptyState
            compact
            icon={<Timer aria-hidden="true" />}
            title={`${slot.slot} 没有快照文件`}
            description={
              <>
                {slot.reason ?? slot.detail ?? '该时点没有落盘快照。'}
                {slot.status === 'skipped' && (
                  <span className="mt-1 block text-text-secondary">
                    窗口外不补跑：用当天之后的价格回填过去的时点会永久污染数据，采集侧只记状态不写文件。
                  </span>
                )}
              </>
            }
            action={<Button variant="secondary" onClick={view.reloadSlot}>重新读取</Button>}
          />
        </CardBody>
      </Card>
    )
  }

  return <SnapshotBody slot={slot} payload={payload ?? null} snapshot={snapshot} />
}

function SnapshotBody({
  slot,
  payload,
  snapshot,
}: {
  slot: IntradaySlotRow
  payload: IntradaySnapshotPayload | null
  snapshot: NonNullable<IntradaySnapshotPayload['snapshot']>
}) {
  const limitUp = snapshot.yesterday_limit_up
  const overall = limitUp?.overall ?? null
  const sentiment = snapshot.sentiment
  const oneToTwo = sentiment?.one_to_two ?? null

  const columns = useMemo<Array<Column<IntradayStockReading>>>(
    () => [
      {
        key: 'code',
        header: '代码',
        width: '112px',
        render: (row) => <span className="ldst-num text-body text-text">{row.code}</span>,
      },
      {
        key: 'name',
        header: '名称',
        width: '96px',
        render: (row) => (
          <span className="text-body text-text-secondary">{row.name ?? MISSING}</span>
        ),
      },
      {
        key: 'boards',
        header: '昨日连板',
        align: 'right',
        width: '88px',
        render: (row) => (
          <span className="ldst-num text-body text-text">
            {row.yesterday_streak === null ? MISSING : formatInt(row.yesterday_streak)}
          </span>
        ),
      },
      {
        key: 'reading',
        header: `读数（${snapshot.price_field ?? MISSING}）`,
        align: 'right',
        width: '104px',
        render: (row) => (
          <span className="ldst-num text-body text-text">
            {row.reading === null ? MISSING : formatOneDecimal(row.reading)}
          </span>
        ),
      },
      {
        key: 'change',
        header: '涨跌幅',
        align: 'right',
        width: '104px',
        render: (row) => {
          const signed = formatChangePct(row.change_pct)
          return <SignedNumber text={signed.text} tone={signed.tone} arrow={signed.arrow} />
        },
      },
      {
        key: 'limit',
        header: '是否封板',
        align: 'center',
        width: '88px',
        render: (row) =>
          row.at_limit_up === null ? (
            <span className="text-caption text-text-muted" title="报价没给 limit_up 字段，不按涨幅阈值猜">
              {MISSING}
            </span>
          ) : (
            <span className="text-caption text-text-secondary">{row.at_limit_up ? '封板' : '未封板'}</span>
          ),
      },
      {
        key: 'fresh',
        header: '读数来源',
        align: 'center',
        width: '96px',
        render: (row) =>
          row.fresh === false ? (
            <Badge tone="warn" title={row.reason ?? '该行没有今天的报价'}>
              非今日
            </Badge>
          ) : (
            <span className="text-caption text-text-muted">{row.reason ?? '今日读数'}</span>
          ),
      },
    ],
    [snapshot.price_field],
  )

  return (
    <div className="flex flex-col gap-4">
      <SectionHeadline slot={slot} payload={payload} snapshot={snapshot} />

      <div className="grid grid-cols-3 gap-3">
        <MetricCard
          label="昨日涨停池"
          hero
          value={readingCell(limitUp?.count ?? null)}
          unit="只"
          note={`其中 ${readingCell(limitUp?.fresh ?? null)} 只今天有读数`}
        />
        <MetricCard
          label="今日仍封板"
          value={readingCell(overall?.limit_up ?? null)}
          unit="只"
          note={`涨 ${readingCell(overall?.up ?? null)} · 平 ${readingCell(overall?.flat ?? null)} · 跌 ${readingCell(overall?.down ?? null)}`}
        />
        <MetricCard
          label="平均涨跌幅"
          value={
            overall === null || overall === undefined
              ? MISSING
              : (() => {
                  const signed = formatChangePct(overall.avg_change_pct)
                  return <SignedNumber text={signed.text} tone={signed.tone} arrow={signed.arrow} />
                })()
          }
          note="昨日涨停股在这个时点的平均涨跌幅（采集侧算好，页面只展示）"
        />
      </div>

      <Card>
        <CardHeader title="情绪面" subtitle={sentiment === null ? '该时点不并入情绪面' : undefined} />
        <CardBody>
          {sentiment === null ? (
            <p className="text-caption leading-body text-text-muted">
              09:25 只做集合竞价读数，情绪面从 09:35 起并入（SPEC-INTRADAY-SNAPSHOT §1）。
            </p>
          ) : sentiment.available ? (
            <div className="grid grid-cols-3 gap-3 desk:grid-cols-6">
              <SentimentCell label="涨停" value={readingCell(sentiment.limit_up)} />
              <SentimentCell label="炸板" value={readingCell(sentiment.broken)} />
              <SentimentCell label="跌停" value={readingCell(sentiment.limit_down)} />
              <SentimentCell label="最高板" value={readingCell(sentiment.max_streak)} />
              <SentimentCell label="封板率" value={pctCell(sentiment.seal_rate_pct)} />
              <SentimentCell label="炸板率" value={pctCell(sentiment.broken_rate_pct)} />
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <Banner tone="warn" title="情绪面未并入">
                {sentiment.reason ?? '情绪面载荷不可用；六项一律显示「—」，不补 0。'}
              </Banner>
              <div className="grid grid-cols-3 gap-3 desk:grid-cols-6">
                {(['涨停', '炸板', '跌停', '最高板', '封板率', '炸板率'] as const).map((label) => (
                  <SentimentCell key={label} label={label} value={MISSING} />
                ))}
              </div>
            </div>
          )}
          <div className="mt-3 border-t border-border pt-3">
            <p className="text-caption leading-body text-text-secondary">
              <span className="font-medium text-text">1 进 2：</span>
              {oneToTwo === null
                ? '该时点没有 1进2 载荷。'
                : oneToTwo.available
                  ? `昨日首板 ${readingCell(oneToTwo.yesterday_first_board)} 只 · 今晋级 ${readingCell(oneToTwo.today_second_board)} 只 · 成功 ${readingCell(oneToTwo.promoted)} 只 · 晋级率 ${pctCell(oneToTwo.rate_pct)}`
                  : `未取到：${oneToTwo.reason ?? '上游没有给出可判定的首板集合'}`}
            </p>
          </div>
        </CardBody>
      </Card>

      {limitUp && limitUp.by_board.length > 0 && (
        <Card>
          <CardHeader title="分板位" subtitle="空分组不输出（采集侧口径）" />
          <CardBody>
            <ul className="flex flex-wrap gap-2">
              {limitUp.by_board.map((bucket) => (
                <li key={bucket.board}>
                  <Badge tone="neutral" icon={<Layers className="size-3" aria-hidden="true" />}>
                    {bucket.board} · {readingCell(bucket.count)} 只
                    {bucket.avg_change_pct !== null && bucket.avg_change_pct !== undefined
                      ? ` · ${formatChangePct(bucket.avg_change_pct).text}`
                      : ''}
                  </Badge>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <DataTable<IntradayStockReading>
        columns={columns}
        rows={limitUp?.stocks ?? []}
        rowKey={(row) => row.code}
        caption={`${slot.slot} 昨日涨停股的逐只客观读数（保持载荷顺序，页面不排序、不评分、不推荐）`}
        density="compact"
        maxHeight="calc(100vh - 420px)"
        empty={<EmptyState compact title="没有逐只读数" description="载荷里 stocks 为空；页面不伪造样本。" />}
      />

      <p className="text-caption leading-body text-text-muted">
        口径：时点状态、昨日涨停池、情绪面、涨跌幅全部来自{" "}
        <code className="ldst-num">GET /api/intraday/snapshot</code> 的{" "}
        <code className="ldst-num">payload.snapshot</code>（<code className="ldst-num">&lt;HHMM&gt;.json</code> 原文），
        页面一个指标都不重算。「—」表示没有这个值，不是 0。来源{" "}
        {snapshot.source.source_detail ?? snapshot.source.tool ?? MISSING}
        {snapshot.source.trail.length > 0 ? ` · 链路 ${snapshot.source.trail.join(' → ')}` : ''}。
      </p>
    </div>
  )
}

function SectionHeadline({
  slot,
  payload,
  snapshot,
}: {
  slot: IntradaySlotRow
  payload: IntradaySnapshotPayload | null
  snapshot: NonNullable<IntradaySnapshotPayload['snapshot']>
}) {
  const visual = slotVisual(slot)
  const Icon = visual.icon
  return (
    <Card>
      <CardHeader
        title={`${slot.slot} 快照节选`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusDot tone={visual.dot}>
              <span className="flex items-center gap-1">
                <Icon className="size-3" aria-hidden="true" />
                {visual.label}
              </span>
            </StatusDot>
            <span className="ldst-num">{slot.file ?? MISSING}</span>
          </span>
        }
        actions={
          slot.recorded ? (
            <Badge tone="neutral" icon={<History className="size-3" aria-hidden="true" />}>
              状态写入 {slot.at ?? MISSING}
            </Badge>
          ) : (
            <Badge tone="neutral">status.json 无该时点记录</Badge>
          )
        }
      />
      <CardBody>
        <dl className="grid grid-cols-1 gap-3 desk:grid-cols-3">
          <DetailRow term="读数口径" desc={priceFieldText(snapshot.price_field)} />
          <DetailRow term="取数工具" desc={snapshot.source.tool ?? MISSING} />
          <DetailRow term="快照时刻" desc={snapshot.captured_at ?? MISSING} />
        </dl>
        {slot.detail && (
          <p className="mt-3 text-caption leading-body text-text-secondary">
            <span className="font-medium text-text">时点记录：</span>
            {slot.detail}
          </p>
        )}
        {payload?.reason && (
          <p className="mt-1 text-caption leading-body text-text-muted">{payload.reason}</p>
        )}
      </CardBody>
    </Card>
  )
}

function DetailRow({ term, desc }: { term: string; desc: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-text-muted">{term}</dt>
      <dd className="text-body break-words text-text">{desc}</dd>
    </div>
  )
}

function SentimentCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded border border-border bg-surface-2 px-3 py-2">
      <p className="text-caption text-text-muted">{label}</p>
      <p className="ldst-num text-body font-semibold text-text">{value}</p>
    </div>
  )
}

// ---------------------------------------------------------------- 屏

export default function IntradayScreen() {
  const [params] = useSearchParams()
  const demo = useIntradayDemoEnabled()
  const date = params.get('date') ?? (demo ? DEMO_INTRADAY_DATE : localDateIso())
  const view = useIntradayView({ date })
  const { state, payload, error, reload, selected, select } = view

  const slot = payload?.slots.find((row) => row.slot === selected) ?? null

  const header = (
    <PageHeader
      title="竞价与盘中"
      subtitle={
        <>
          {date} ·{' '}
          {payload
            ? `${payload.snapshot_count}/${payload.slot_count} 个时点有快照 · 来源 ${
                payload.slots.length > 0 ? 'status.json' : MISSING
              }`
            : '读取当日各时点快照'}
        </>
      }
      badge={<DemoStateBadge state={view.forced} />}
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
        <ScreenSkeleton metrics={3} rows={6} />
      </div>
    )
  }

  if (state === 'error') {
    const described = describeApiError(error)
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock variant="banner" title="盘中快照取数失败" detail={described.detail} code={described.code} onRetry={reload} />
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
            icon={<Radar aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入 GET /api/intraday；未连接时不展示任何时点读数。"
            action={<Button variant="secondary" onClick={reload}>重新检查</Button>}
          />
        </Card>
      </div>
    )
  }

  if (state === 'empty') {
    const endpointMissing = payload === null
    const showRail = payload !== null && !payload.available && payload.slots.length > 0
    // `?state=empty` 强制出来的空态不能反过来否认已经取到的数据
    const forcedDemo = !endpointMissing && payload?.available === true
    return (
      <div className="flex flex-col gap-5">
        {header}
        <Card>
          <EmptyState
            icon={<Gauge aria-hidden="true" />}
            title={endpointMissing ? '接口未接入' : '这一天没有落盘快照'}
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/intraday</code> 契约实现；接口提供后自动接入，
                  当前不伪造时点。
                </>
              ) : forcedDemo ? (
                <>
                  演示态：这一天实际有 {payload?.snapshot_count}/{payload?.slot_count} 个时点落盘，
                  <code className="ldst-num">?state=empty</code> 只把空态的样子摆出来，不改真实数据。
                </>
              ) : (
                <>
                  {payload?.reason ?? '本机没有该日的快照记录。'}
                  <span className="mt-1 block text-text-secondary">
                    快照只在窗口内当场落盘，窗口外不补跑（DEC-035 §2.3）；
                    {payload?.has_status_file === false
                      ? '这一天调度器没有跑过。'
                      : `status.json 存在，但 ${payload?.slot_count ?? MISSING} 个时点都没有快照文件。`}
                  </span>
                </>
              )
            }
            action={<Button variant="secondary" onClick={reload}>刷新</Button>}
          />
        </Card>
        {/* 空态仍然列出后端返回的时点行：六个格子都在，只是都没有文件 */}
        {showRail && <SlotRail payload={payload} selected={selected} onSelect={select} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && payload && (
        <Banner tone="warn" title="部分时点没有落盘">
          {payload.slots
            .filter((row) => !row.available)
            .map((row) => `${row.slot} ${slotVisual(row).label}`)
            .join('、')}
          。这些时点按各自状态显示，不显示 0；<code className="ldst-num">skipped</code>
          的时点是窗口外不补跑，不算取数失败。
        </Banner>
      )}

      {payload && <SlotRail payload={payload} selected={selected} onSelect={select} />}

      <SnapshotDetail
        slot={slot}
        view={{
          snapshot: view.snapshot,
          slotState: view.slotState,
          slotError: view.slotError,
          reloadSlot: view.reloadSlot,
        }}
      />
    </div>
  )
}
