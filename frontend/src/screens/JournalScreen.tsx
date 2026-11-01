import { useState } from 'react'
import { Copy, Lock, NotebookPen, RefreshCw } from 'lucide-react'

import { describeApiError, type JournalStatsPayload } from '../api'
import { useForcedState } from '../lib/pageState'
import {
  MISSING,
  formatInt,
  formatMoney,
  formatRatePct,
  formatTime,
  isFiniteNumber,
} from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  DataTable,
  EmptyState,
  ErrorBlock,
  FreshnessBadge,
  MetricCard,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../components/ui'
import { useJournalView, EMPTY_STATS } from './journal/useJournalView'

/**
 * 交易日志（P-05 / DESIGN-SPEC §4.6 / UI-SPEC §3.4）。
 *
 * 数据面只有一条：`GET /api/journal/stats`（DEC-015 Tier 1）——本机回环独占、
 * 扁平 JSON、只回聚合数字；`note` / 逐笔明细 / 标的清单永不出站。
 * 逐笔明细端点（DEC-015 Tier 2）决策为「不开」，所以本屏不展示单笔记录，
 * 也不编造示例行；明细只在终端 `ldst journal list` 可见。
 *
 * 取数与状态推导收在 `journal/useJournalView.ts`（演示源 / IndexedDB 离线回放都在那里），
 * 本文件只管 DOM。演示态的数据是**夹具样本**，与用户自己的成交无关：页头有「演示数据」徽标，
 * 正文再有一条「演示样本」提示条，且演示态不读写本机 IndexedDB、不提供任何导出入口。
 */

const EXAMPLE_COMMAND =
  'ldst journal add --code 600000 --side buy --price 10.00 --qty 100 --plan-price 10.00'

/** 纪律统计的最小样本：少于 5 个闭环不计算胜率 / 盈亏比（DESIGN-SPEC §4.6）。 */
const MIN_SAMPLE = 5

interface AggregateRow {
  key: string
  label: string
  value: string
  basis: string
}

/** 数量（股）可能带小数：最多 4 位，不补 0。 */
function formatQty(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

/** 盈亏金额：保留正负号，红涨绿跌只作用于数字。 */
function formatSignedMoney(value: number | null | undefined): {
  text: string
  tone: 'up' | 'down' | 'flat'
  arrow: string
} {
  if (!isFiniteNumber(value)) return { text: MISSING, tone: 'flat', arrow: '' }
  const rounded = Number(value.toFixed(2))
  const tone = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
  return {
    text: `${rounded > 0 ? '+' : ''}${formatMoney(rounded)}`,
    tone,
    arrow: tone === 'up' ? '↑' : tone === 'down' ? '↓' : '',
  }
}

function rangeLabel(stats: JournalStatsPayload | null): string {
  if (!stats) return MISSING
  const { from, to } = stats.range
  if (!from && !to) return '全部时间'
  return `${from ?? '最早'} ~ ${to ?? '今天'}`
}

function buildAggregateRows(stats: JournalStatsPayload): AggregateRow[] {
  return [
    {
      key: 'range',
      label: '查询区间',
      value: rangeLabel(stats),
      basis: '请求的 from / to 边界；不传即全部时间',
    },
    {
      key: 'total',
      label: '成交笔数',
      value: formatInt(stats.total_trades),
      basis: '买入 + 卖出之和：一行 = 一笔委托成交',
    },
    {
      key: 'buy',
      label: '买入笔数',
      value: formatInt(stats.buy_count),
      basis: 'side = buy 的成交笔数',
    },
    {
      key: 'sell',
      label: '卖出笔数',
      value: formatInt(stats.sell_count),
      basis: 'side = sell 的成交笔数',
    },
    {
      key: 'closed',
      label: '闭环笔数',
      value: formatInt(stats.closed_rounds),
      basis: 'FIFO 配对：卖出依次吃掉最早的买入批次，有配对数量即一个闭环',
    },
    {
      key: 'outcome',
      label: '盈 / 亏 / 平',
      value: `${formatInt(stats.wins)} / ${formatInt(stats.losses)} / ${formatInt(stats.flats)}`,
      basis: '按闭环的已实现盈亏划分，三项之和 = 闭环笔数',
    },
    {
      key: 'win-rate',
      label: '胜率',
      value: stats.win_rate === null ? MISSING : formatRatePct(stats.win_rate).text,
      basis: '盈利闭环 ÷ 闭环笔数（样本口径见上方指标卡）',
    },
    {
      key: 'avg-win',
      label: '平均盈利',
      value: stats.avg_win === null ? MISSING : formatMoney(stats.avg_win),
      basis: '盈利闭环的已实现盈亏均值',
    },
    {
      key: 'avg-loss',
      label: '平均亏损',
      value: stats.avg_loss === null ? MISSING : formatMoney(stats.avg_loss),
      basis: '亏损闭环的已实现亏损绝对值均值',
    },
    {
      key: 'ratio',
      label: '盈亏比',
      value: stats.profit_loss_ratio === null ? MISSING : stats.profit_loss_ratio.toFixed(2),
      basis: '平均盈利 ÷ 平均亏损；没有亏损样本时为空',
    },
    {
      key: 'pnl',
      label: '已实现盈亏',
      value: formatMoney(stats.realized_pnl),
      basis: '逐闭环累加：盈利合计 − 亏损合计（平录不计）',
    },
    {
      key: 'unmatched',
      label: '未配对卖出',
      value: `${formatQty(stats.unmatched_sell_qty)} 股`,
      basis: '没有对应买入批次的卖出数量，如实计数、不编造成本',
    },
    {
      key: 'deviation',
      label: '偏离计划笔数',
      value: `${formatInt(stats.deviation_review.count)} 笔`,
      basis: `阈值 ${stats.deviation_review.threshold_pct.toFixed(2)}%（|成交价 − 计划价| ÷ 计划价）`,
    },
  ]
}

const AGGREGATE_COLUMNS: Array<Column<AggregateRow>> = [
  {
    key: 'label',
    header: '指标',
    width: '144px',
    render: (row) => <span className="font-medium">{row.label}</span>,
  },
  {
    key: 'value',
    header: '数值',
    align: 'right',
    // 176px 会把「2026-10-01 ~ 2026-10-30」折成两行，一行多出的高度正好吃掉首屏的第 12 行。
    width: '208px',
    render: (row) => <span className="ldst-num whitespace-nowrap">{row.value}</span>,
  },
  {
    key: 'basis',
    header: '口径',
    render: (row) => <span className="text-caption leading-body text-text-muted">{row.basis}</span>,
  },
]

/** 加载骨架与真实表体同构（§11.0「骨架与真实内容形状同构」）：行数取聚合明细的行数。 */
const AGGREGATE_ROWS_COUNT = buildAggregateRows(EMPTY_STATS).length

export default function JournalScreen() {
  const view = useJournalView()
  const { demo, state, stats: viewStats, cached, error, endpointMissing, reload } = view
  const forced = useForcedState()

  const [copied, setCopied] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  /** `error !== null` 只在真实请求失败时为真（`useAsync` 成功 / 加载中都不带 error）。 */
  const requestFailed = error !== null

  const noDataNote =
    state === 'offline'
      ? cached
        ? '离线 · 缓存回放'
        : '本地服务未连接'
      : state === 'partial'
        ? '统计接口不可用'
        : state === 'loading'
          ? '读取中'
          : '暂无数据'

  const done = state === 'normal' || state === 'partial' || state === 'offline' || state === 'empty'

  const partialDetail = endpointMissing
    ? '统计接口未接入：当前后端没有 GET /api/journal/stats（旧版后端），本屏不编造任何数字。'
    : requestFailed
      ? `${describeApiError(error).detail}${
          cached ? `；以下为 IndexedDB 缓存（统计时间 ${formatTime(cached.fetchedAt)}）` : ''
        }`
      : demo
        ? // 演示夹具的唯一降级项：闭环样本少于 MIN_SAMPLE，纪律指标一律「样本不足」。
          `演示样本的闭环只有 ${formatInt(viewStats?.closed_rounds ?? 0)} 笔，少于 ${MIN_SAMPLE} 笔：胜率与盈亏比按「样本不足」显示，不是 0%。`
        : '逐笔明细通道未开放（DEC-015 Tier 2 决策为「不开」）：聚合统计可用，单笔记录不在本屏展示。'

  const errorInfo =
    error !== null && state === 'error'
      ? describeApiError(error)
      : {
          title: '本地日志统计无法读取',
          detail: '本机只读接口没有返回统计数据。恢复可用后点「重试取数」；本屏不编造任何数字。',
          code: null,
        }

  const summary =
    state === 'loading'
      ? '读取本机日志统计…'
      : state === 'error'
        ? '本地日志统计读取失败'
        : state === 'offline'
          ? cached
            ? `本机服务未连接 · 缓存回放（统计时间 ${formatTime(cached.fetchedAt)}）`
            : '本机服务未连接 · 没有可回放的缓存'
          : state === 'partial'
            ? viewStats && viewStats.total_trades > 0
              ? `本机私有 · 共 ${formatInt(viewStats.total_trades)} 笔 · 部分数据源不可用`
              : '本机私有 · 统计接口不可用'
            : state === 'empty'
              ? demo
                ? '演示样本 · 本机日志为空'
                : '本机私有 · 还没有交易记录'
              : `${demo ? '演示样本' : '本机私有'} · 共 ${formatInt(viewStats?.total_trades ?? 0)} 笔 · 买入 ${formatInt(
                  viewStats?.buy_count ?? 0,
                )} / 卖出 ${formatInt(viewStats?.sell_count ?? 0)} · ${rangeLabel(viewStats)}`

  const copyExample = () => {
    const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
    if (!clipboard?.writeText) {
      setNotice('当前浏览器不允许自动复制，请手动选中命令复制。')
      return
    }
    void clipboard
      .writeText(EXAMPLE_COMMAND)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setNotice('复制失败，请手动选中命令复制。'))
  }

  const header = (
    <PageHeader
      title="交易日志"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="primary"
            onClick={reload}
            loading={state === 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
          <Button
            variant="secondary"
            disabled
            disabledReason="仅命令行"
            leadingIcon={<NotebookPen className="size-3.5" aria-hidden="true" />}
          >
            记录一笔
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={4} rows={AGGREGATE_ROWS_COUNT} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock {...errorInfo} onRetry={reload} retryLabel="重试取数" />
      </div>
    )
  }

  const total = viewStats?.total_trades ?? null
  const hasTrades = viewStats !== null && viewStats.total_trades > 0
  const enoughSample = viewStats !== null && viewStats.closed_rounds >= MIN_SAMPLE
  const pnl = viewStats && hasTrades ? formatSignedMoney(viewStats.realized_pnl) : null
  const winRate =
    viewStats && hasTrades && enoughSample && viewStats.win_rate !== null
      ? formatRatePct(viewStats.win_rate).text
      : hasTrades
        ? '样本不足'
        : MISSING
  const ratio =
    viewStats && hasTrades && enoughSample && viewStats.profit_loss_ratio !== null
      ? viewStats.profit_loss_ratio.toFixed(2)
      : hasTrades
        ? '样本不足'
        : MISSING
  const aggregateRows = viewStats && hasTrades ? buildAggregateRows(viewStats) : []

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'offline' && <OfflineBanner onRetry={reload} />}
      {state === 'offline' && cached && (
        <Banner tone="warn" title="缓存回放（只读）">
          本机服务未连接，以下为本机 IndexedDB 缓存的聚合统计 · 统计时间 {formatTime(cached.fetchedAt)}。
          缓存只含数字，不含任何逐笔理由或备注。
        </Banner>
      )}
      {state === 'offline' && !cached && (
        <Banner tone="warn" title="没有可回放的缓存">
          本机服务未连接，IndexedDB 里也没有本机日志统计缓存。恢复连接后点「刷新」。
        </Banner>
      )}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={partialDetail}
          onRetry={reload}
          retryLabel="重试取数"
        />
      )}

      {/* §3.4 隐私信息条常驻；演示样本的说明并进同一条（两条并排会把 12 行表顶出首屏）。
          演示态用 warn 底色：这条屏上的数字不是你的成交。 */}
      <Banner tone={demo ? 'warn' : 'info'} title={demo ? '本机私有 · 当前是演示样本' : '本机私有'}>
        {demo
          ? '数字来自演示夹具，与这台机器的 data/journal.sqlite 无关：演示态不读写本机 IndexedDB，也没有导出真实数据的入口。关掉「设置 → 虚拟数据」即回到本机统计。'
          : '交易日志、持仓、自选只写入本机 SQLite，不上传、不进 AI 上下文、不被 MCP 读取。网页接口只回聚合数字，逐笔理由与备注永不出站。'}
      </Banner>

      {notice && (
        <p className="text-caption text-text-secondary" role="status">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 rail:grid-cols-2 desk:grid-cols-4">
        <MetricCard
          label="交易笔数"
          value={total === null ? MISSING : formatInt(total)}
          unit="笔"
          note={
            viewStats
              ? `买入 ${formatInt(viewStats.buy_count)} · 卖出 ${formatInt(viewStats.sell_count)}`
              : noDataNote
          }
          badge={<Badge tone="neutral">聚合</Badge>}
        />
        <MetricCard
          label="胜率"
          value={winRate}
          note={
            viewStats
              ? enoughSample
                ? `闭环 ${formatInt(viewStats.closed_rounds)} 笔 · 盈 ${formatInt(
                    viewStats.wins,
                  )} / 亏 ${formatInt(viewStats.losses)} / 平 ${formatInt(viewStats.flats)}`
                : `闭环 ${formatInt(viewStats.closed_rounds)} 笔 · 少于 ${MIN_SAMPLE} 笔不计算`
              : noDataNote
          }
          badge={
            <Badge tone="neutral" title={`闭环样本少于 ${MIN_SAMPLE} 笔显示「样本不足」`}>
              口径
            </Badge>
          }
        />
        <MetricCard
          label="盈亏比"
          value={ratio}
          note={
            viewStats
              ? hasTrades && enoughSample
                ? `平均盈利 ${viewStats.avg_win === null ? MISSING : formatMoney(viewStats.avg_win)} · 平均亏损 ${
                    viewStats.avg_loss === null ? MISSING : formatMoney(viewStats.avg_loss)
                  }`
                : hasTrades
                  ? `闭环 ${formatInt(viewStats.closed_rounds)} 笔 · 少于 ${MIN_SAMPLE} 笔不计算`
                  : '还没有闭环'
              : noDataNote
          }
          badge={<Badge tone="neutral">口径</Badge>}
        />
        <MetricCard
          label="已实现盈亏"
          value={pnl ? <SignedNumber text={pnl.text} tone={pnl.tone} arrow={pnl.arrow} /> : MISSING}
          note={
            viewStats
              ? hasTrades
                ? `未配对卖出 ${formatQty(viewStats.unmatched_sell_qty)} 股`
                : '还没有交易'
              : noDataNote
          }
          badge={<Badge tone="neutral">本机</Badge>}
        />
      </div>

      {state === 'empty' ? (
        <SectionCard
          title="从第一笔开始"
          subtitle="空态 · 日志只写本机，不显示任何示例数据"
          tone="neutral"
          bodyClassName="p-0"
          actions={
            <Badge tone="neutral" icon={<Lock className="size-3" aria-hidden="true" />}>
              本机
            </Badge>
          }
        >
          <EmptyState
            icon={<NotebookPen aria-hidden="true" />}
            title="还没有交易记录"
            description="在终端运行下面这条命令记一笔（代码 / 方向 / 价格 / 数量换成你的），再回来点「刷新」。日志写入只走本机命令行，网页不提供写入口。"
            action={
              <div className="flex w-full max-w-measure flex-col items-center gap-2">
                <code className="ldst-num w-full break-all rounded border border-border bg-surface-3 px-3 py-2 text-left text-caption text-text-secondary">
                  {EXAMPLE_COMMAND}
                </code>
                <Button
                  variant="secondary"
                  onClick={copyExample}
                  leadingIcon={<Copy className="size-3.5" aria-hidden="true" />}
                >
                  {copied ? '已复制' : '复制命令'}
                </Button>
              </div>
            }
          />
        </SectionCard>
      ) : (
        <SectionCard
          title="统计明细（聚合）"
          subtitle={
            viewStats && hasTrades
              ? `${rangeLabel(viewStats)} · 口径全部来自本机 SQLite`
              : '统计接口没有可用数字'
          }
          bodyClassName="p-0"
          actions={
            <>
              {/* §5.7：`/api/journal/stats` 是扁平静态 JSON，响应里没有 `freshness`，
                  所以新鲜度只能出「未知」；`data_source_status` 是这条契约自带的完整性字段。 */}
              <FreshnessBadge
                freshness={null}
                asOf={viewStats?.range.to ?? null}
                sourceDetail="GET /api/journal/stats 为扁平响应，未携带 freshness"
              />
              <SourceStatusBadge status={viewStats?.data_source_status ?? null} />
              <Badge tone="neutral">只读</Badge>
            </>
          }
        >
          <DataTable<AggregateRow>
            columns={AGGREGATE_COLUMNS}
            rows={aggregateRows}
            rowKey={(row) => row.key}
            caption="交易日志聚合统计（不含标的清单、逐笔明细与备注）"
            density="compact"
            // 首屏要同时看到 ≥12 行（§11.0）：13 行 × 36px + 表头 + 表说明 ≈ 580px，
            // 原来的 420px 会把最后四行藏进滚动区。§7.3 只允许一个具名 calc 表高，就用它。
            maxHeight="calc(100vh - 280px)"
            empty={
              <EmptyState
                compact
                title="没有可展示的聚合数字"
                description={
                  state === 'offline'
                    ? '本机服务未连接，且没有可回放的缓存；恢复连接后点「刷新」。'
                    : '统计接口当前不可用；本屏不补 0、不编造任何数值。'
                }
                action={
                  <Button variant="secondary" onClick={reload}>
                    刷新
                  </Button>
                }
              />
            }
          />
        </SectionCard>
      )}

      {done && (
        <SectionCard
          title="逐笔交易明细"
          subtitle="DEC-015：只开放聚合统计，逐笔明细不经网页接口出站"
          tone="neutral"
          bodyClassName="p-0"
          actions={
            <Badge tone="neutral" icon={<Lock className="size-3" aria-hidden="true" />}>
              未开放
            </Badge>
          }
        >
          <EmptyState
            compact
            icon={<Lock aria-hidden="true" />}
            title="逐笔明细不通过网页接口"
            description="逐笔明细端点（DEC-015 Tier 2）经决策为「不开」，因此这里不展示单笔日期 / 标的 / 价格，也不编造示例行。需要逐笔数据请在终端运行 ldst journal list。"
          />
        </SectionCard>
      )}

      <SectionCard title="日志写在哪、谁能读" subtitle="隐私边界（本屏不出现任何 AI 内容）" tone="neutral" bodyClassName="p-4">
        <ul className="flex flex-col gap-2 text-body leading-body text-text-secondary">
          <li>
            · 存储位置：本机 <code>data/journal.sqlite</code>，随数据目录一起备份或删除。
          </li>
          <li>
            · 读取方式：本机只读接口 <code>GET /api/journal/stats</code>（回环独占、扁平 JSON、只回聚合数字）
            与 <code>ldst journal list|stats</code> 命令；逐笔明细不经网页接口出站。
          </li>
          <li>· 边界：不上传、不进 AI 上下文、不被 MCP 读取；本屏没有同步、分享、云端入口。</li>
          <li>
            · 口径：闭环样本少于 {MIN_SAMPLE} 笔一律显示「样本不足」；无样本的胜率 / 盈亏比显示「
            {MISSING}」，不拿 0 冒充。
          </li>
        </ul>
      </SectionCard>

      <p className="text-caption text-text-muted">
        本屏不提供盈亏排行榜、收益预测或「AI 点评你的交易」。
      </p>
    </div>
  )
}
