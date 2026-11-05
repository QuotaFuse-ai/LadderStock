import { useEffect, useMemo, useState } from 'react'

import { describeApiError, isApiError } from '../../api'
import {
  adoptLegacy,
  deletePortfolioTrade,
  dismissLegacy,
  fetchPortfolio,
  type PortfolioItem,
  type PortfolioLegacyRow,
  type PortfolioMethod,
  type PortfolioTrade,
} from '../../api/radar'
import { PageHeader } from '../../components/PageShell'
import {
  Banner,
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorBlock,
  PrivacyNotice,
  SectionCard,
  Segmented,
  SignedNumber,
  SkeletonRows,
  Tabs,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { MISSING, formatChangePct } from '../../lib/format'
import { usePrivacyMode } from '../../lib/privacy'
import { useAsync } from '../../lib/useAsync'
import { PortfolioEntry } from './PortfolioEntry'
import { MigrationCard, adoptLabel, dismissLabel, migrateActionRows } from './PortfolioMigration'
import { PortfolioPerfTab } from './PortfolioPerfTab'
import { PortfolioTradesTab } from './PortfolioTradesTab'
import { PrivacyText } from './PrivacyText'
import {
  TONE_CLASS,
  moneyText,
  pctSignedText,
  plain,
  priceText,
  signedMoney,
  toneOf,
} from './portfolioFormat'
import { readCostMethod, saveCostMethod, tradeTypeLabel } from './portfolioForm'

/**
 * 我的持仓（QUOT-434）：`/my-stocks?tab=hold`。
 *
 * 一条主线：**录的是流水，持仓由流水推算**。数量、成本价、市值、浮动盈亏、
 * 已实现、分红全部来自 `GET /api/portfolio`，页面不重算；成本法切换只换查询参数。
 *
 * 五条硬约束：
 * 1. 只做 A 股 / 人民币：不做多币种、不做汇率，不显示假的外币块；
 * 2. 缺失一律 `—`，不补 0；有问题的流水在表上方逐条说明「没参与计算」；
 * 3. 旧记录**只标记、不删除**：adopt / dismiss 都要二次确认，备份文件名照实展示；
 * 4. 隐私遮罩只走 `lib/privacy.ts` + 顶栏开关（本页不再放第二个开关）；
 * 5. 收益子页只有「收益 / 交易流水」两个标签，X 光与回放由后续任务加。
 */

const REFRESH_MS = 30 * 60 * 1000

const METHOD_OPTIONS: Array<{ value: PortfolioMethod; label: string }> = [
  { value: 'average', label: '移动加权平均' },
  { value: 'fifo', label: '先进先出' },
]

const IN_PAGE_TABS = [
  { value: 'perf', label: '收益' },
  { value: 'trades', label: '交易流水' },
] as const

type InPageTab = (typeof IN_PAGE_TABS)[number]['value']

type Confirm =
  | { kind: 'delete'; trade: PortfolioTrade }
  | { kind: 'adopt'; row: PortfolioLegacyRow }
  | { kind: 'dismiss'; row: PortfolioLegacyRow }

function describeError(error: unknown): string {
  if (isApiError(error)) return error.detail ?? error.message
  return error instanceof Error ? error.message : String(error)
}

/** `null` 当负无穷：缺失值不因为排序方向翻到最前面去抢眼。 */
function compareNumbers(a: number | null, b: number | null): number {
  return (a ?? Number.NEGATIVE_INFINITY) - (b ?? Number.NEGATIVE_INFINITY)
}

export function PortfolioPanel() {
  const [method, setMethod] = useState<PortfolioMethod>(readCostMethod)
  const view = useAsync((signal) => fetchPortfolio(method, signal), [method])
  const payload = view.data
  const { on: privacyOn } = usePrivacyMode()

  const [revision, setRevision] = useState(0)
  const [tab, setTab] = useState<InPageTab>('perf')
  const [message, setMessage] = useState<{ tone: 'ok' | 'warn'; text: string } | null>(null)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [busy, setBusy] = useState(false)

  const bump = () => setRevision((value) => value + 1)

  useEffect(() => {
    const timer = window.setInterval(() => view.refresh(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [view.refresh])

  const pickMethod = (next: PortfolioMethod) => {
    saveCostMethod(next)
    setMethod(next)
  }

  const afterWrite = (text: string) => {
    setMessage({ tone: 'ok', text })
    bump()
    view.refresh()
  }

  const runConfirm = async () => {
    if (!confirm) return
    const target = confirm
    setBusy(true)
    try {
      if (target.kind === 'delete') {
        await deletePortfolioTrade(target.trade.id)
        afterWrite(`已删除 ${target.trade.date} ${target.trade.symbol} 的流水（本机 SQLite）`)
      } else if (target.kind === 'adopt') {
        await adoptLegacy(target.row.id)
        afterWrite(`已把旧记录 ${target.row.symbol || target.row.id} 转成期初持仓（本机 SQLite）`)
      } else {
        await dismissLegacy(target.row.id)
        afterWrite(`已标记不要旧记录 ${target.row.symbol || target.row.id}；positions 旧行未删除`)
      }
      setConfirm(null)
    } catch (cause) {
      setMessage({ tone: 'warn', text: describeError(cause) })
    } finally {
      setBusy(false)
    }
  }

  const items = payload?.items ?? []
  const trades = payload?.ledger ?? []
  const issues = payload?.issues ?? []
  const totals = payload?.totals_cny ?? null
  const legacyRows = useMemo(() => migrateActionRows(payload?.legacy ?? []), [payload])

  const columns = useMemo<Array<Column<PortfolioItem>>>(() => {
    const sortable = (masked: boolean, compare: (a: PortfolioItem, b: PortfolioItem) => number) =>
      privacyOn && masked ? undefined : compare
    return [
      {
        key: 'name',
        header: '名称',
        truncate: true,
        sort: sortable(false, (a, b) => a.name.localeCompare(b.name)),
        render: (row) => (
          <span className={row.name ? 'truncate font-medium text-text' : 'text-text-muted'}>
            {row.name || MISSING}
          </span>
        ),
        cellTitle: (row) => row.name || '名称未填',
      },
      {
        key: 'symbol',
        header: '代码',
        width: '92px',
        render: (row) => <span className="ldst-num text-caption text-text-secondary">{row.symbol}</span>,
      },
      {
        key: 'price',
        header: '现价',
        width: '92px',
        numeric: true,
        sort: sortable(false, (a, b) => compareNumbers(a.latest_price, b.latest_price)),
        render: (row) => (
          <span
            className={cn('ldst-num', row.latest_price === null ? 'text-text-muted' : 'text-text')}
            title={row.price_reason ?? undefined}
          >
            {priceText(row.latest_price)}
          </span>
        ),
      },
      {
        key: 'shares',
        header: '数量',
        width: '96px',
        numeric: true,
        sort: sortable(true, (a, b) => compareNumbers(a.shares, b.shares)),
        render: (row) => <PrivacyText className="ldst-num text-text-secondary" text={plain(row.shares)} />,
      },
      {
        key: 'cost',
        header: '成本价',
        width: '96px',
        numeric: true,
        render: (row) => <PrivacyText className="ldst-num text-text-secondary" text={priceText(row.unit_cost)} />,
      },
      {
        key: 'market_value',
        header: '持仓市值',
        width: '112px',
        numeric: true,
        sort: sortable(true, (a, b) => compareNumbers(a.market_value, b.market_value)),
        render: (row) => <PrivacyText className="ldst-num text-text" text={moneyText(row.market_value)} />,
      },
      {
        key: 'pnl',
        header: '浮动盈亏',
        width: '112px',
        numeric: true,
        sort: sortable(true, (a, b) => compareNumbers(a.pnl, b.pnl)),
        render: (row) => (
          <PrivacyText className={cn('ldst-num', TONE_CLASS[toneOf(row.pnl)])} text={signedMoney(row.pnl)} />
        ),
      },
      {
        key: 'pnl_pct',
        header: '盈亏%',
        width: '104px',
        numeric: true,
        className: 'whitespace-nowrap',
        sort: sortable(false, (a, b) => compareNumbers(a.pnl_pct, b.pnl_pct)),
        render: (row) => {
          const pct = formatChangePct(row.pnl_pct)
          return <SignedNumber text={pct.text} tone={pct.tone} arrow={pct.arrow} />
        },
      },
      {
        key: 'realized_pnl',
        header: '已实现',
        width: '104px',
        numeric: true,
        sort: sortable(true, (a, b) => compareNumbers(a.realized_pnl, b.realized_pnl)),
        render: (row) => (
          <PrivacyText
            className={cn('ldst-num', TONE_CLASS[toneOf(row.realized_pnl)])}
            text={signedMoney(row.realized_pnl)}
          />
        ),
      },
      {
        key: 'dividends',
        header: '分红',
        width: '96px',
        numeric: true,
        sort: sortable(true, (a, b) => compareNumbers(a.dividends, b.dividends)),
        render: (row) => <PrivacyText className="ldst-num text-text-secondary" text={moneyText(row.dividends)} />,
      },
    ]
  }, [privacyOn])

  if (view.status === 'error' && !payload) {
    const error = describeApiError(view.error)
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title="我的持仓" subtitle="持仓由交易流水推算" refresh={{ onClick: view.reload }} />
        <ErrorBlock title={error.title} detail={error.detail} code={error.code} onRetry={view.reload} />
      </div>
    )
  }

  const migration = payload?.migration
  const autoMigrated = migration ? migration.migrated.length + migration.failed.length : 0

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="我的持仓"
        subtitle={
          payload
            ? `${items.length} 只 · ${payload.method_label || '移动加权平均'} · 记账日 ${payload.today || MISSING}`
            : '持仓由交易流水推算'
        }
        actions={
          <Segmented
            ariaLabel="成本法"
            size="sm"
            options={METHOD_OPTIONS}
            value={method}
            onChange={pickMethod}
          />
        }
        refresh={{ onClick: () => view.refresh({ manual: true }), loading: view.refreshing }}
      />

      <PrivacyNotice subject="持仓" storeCommand="ldst portfolio" />

      {view.refreshError !== null && (
        <Banner tone="warn" title="刷新失败，页面上还是上一次的数据">
          {describeError(view.refreshError)}
        </Banner>
      )}
      {message && (
        <Banner
          tone={message.tone}
          title={message.text}
          action={
            <Button variant="ghost" onClick={() => setMessage(null)}>
              知道了
            </Button>
          }
        />
      )}

      {autoMigrated > 0 && migration && (
        <Banner
          tone="info"
          title={
            migration.failed.length > 0
              ? `已自动转 ${migration.migrated.length} 条旧记录，${migration.failed.length} 条失败`
              : `已把 ${migration.migrated.length} 条旧持有记录转成「期初持仓」流水`
          }
        >
          <p>数量和成本照旧；没写建仓日的按今天暂记。旧记录只标记、不删除。</p>
          {migration.failed.map((row) => (
            <p key={row.position_id} className="text-caption text-danger">
              {row.symbol || row.position_id}：{row.why}
            </p>
          ))}
          {migration.backup && (
            <p className="text-caption">
              改动前的原文件备份：<code className="ldst-num">{migration.backup}</code>
            </p>
          )}
        </Banner>
      )}

      {legacyRows.length > 0 && (
        <MigrationCard
          rows={legacyRows}
          backup={migration?.backup ?? null}
          error={migration?.error ?? null}
          onPick={(target) =>
            setConfirm(
              target.action === 'adopt'
                ? { kind: 'adopt', row: target.row }
                : { kind: 'dismiss', row: target.row },
            )
          }
        />
      )}

      <SectionCard
        tone="neutral"
        title="人民币合计"
        subtitle="只汇总取到现价的持仓；缺价的不计入"
      >
        {!totals || !totals.available ? (
          <p className="text-body text-text-secondary">{totals?.reason ?? '本机还没有持仓流水'}</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Total label="市值" value={moneyText(totals.market_value)} />
            <Total label="成本" value={moneyText(totals.cost)} />
            <Total label="浮动盈亏" value={signedMoney(totals.pnl)} tone={toneOf(totals.pnl)} />
            <Total
              label="盈亏%"
              value={pctSignedText(totals.pnl_pct).text}
              tone={pctSignedText(totals.pnl_pct).tone}
              mask={false}
            />
          </div>
        )}
        {totals?.reason && totals.available && (
          <p className="mt-3 text-caption text-warn">{totals.reason}</p>
        )}
        {totals && totals.priced_count > 0 && (
          <p className="mt-3 text-caption text-text-muted">
            计入 {totals.priced_count} 只；缺价 {totals.unpriced_count} 只。本页只做人民币，不含港美股、不做汇率换算。
          </p>
        )}
      </SectionCard>

      <SectionCard title="记一笔" subtitle="只记已经发生的交易，不下单">
        <PortfolioEntry onSaved={(text) => afterWrite(text)} />
      </SectionCard>

      <SectionCard title="持仓明细" subtitle="数量、成本、盈亏都由流水按所选成本法算出">
        {issues.length > 0 && (
          <Banner tone="warn" title={`有 ${issues.length} 笔流水数据不对，没参与计算`} className="mb-3">
            <ul className="flex flex-col gap-0.5 text-caption">
              {issues.map((issue) => (
                <li key={issue.id}>
                  <span className="ldst-num">{issue.date || '无日期'}</span> ·{' '}
                  {tradeTypeLabel(issue.type)} · <span className="ldst-num">{issue.symbol || '无代码'}</span>：
                  {issue.why}
                </li>
              ))}
            </ul>
          </Banner>
        )}

        {view.status === 'loading' && !payload ? (
          <SkeletonRows rows={5} />
        ) : (
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(row) => row.symbol}
            density="default"
            empty={
              <EmptyState
                compact
                title="还没有持仓"
                description="开始记账前就有的仓位，用上面的「期初持仓」记一笔；之后按买入 / 卖出 / 分红逐笔记。"
              />
            }
          />
        )}

        <p className="mt-3 text-caption leading-body text-text-muted">
          {method === 'fifo'
            ? '先进先出：卖出时先扣最早买入的那批，剩下的成本是还没卖掉那几批的成本。'
            : '移动加权平均：每次买入后重算平均成本，卖出不改平均成本。'}
          两种方法持有数量一样，成本价和已实现盈亏不同。费用计入买入成本、从卖出所得里扣；分红单独算。
          {privacyOn && ' 隐私模式开着：金额与股数已遮罩，这几列暂不参与排序。'}
        </p>
      </SectionCard>

      <Tabs ariaLabel="持仓的更多内容" tabs={IN_PAGE_TABS} value={tab} onChange={setTab} />

      {tab === 'perf' ? (
        <PortfolioPerfTab refreshKey={revision} pendingLegacy={legacyRows.length} />
      ) : (
        <PortfolioTradesTab
          trades={trades}
          issues={issues}
          onDelete={(trade) => setConfirm({ kind: 'delete', trade })}
        />
      )}

      <ConfirmDialog
        open={confirm !== null}
        title={confirmTitle(confirm)}
        description={confirmDescription(confirm)}
        confirmLabel={confirmLabel(confirm)}
        tone={confirm?.kind === 'adopt' ? 'primary' : 'danger'}
        loading={busy}
        onConfirm={() => void runConfirm()}
        onCancel={() => setConfirm(null)}
      />
    </div>
  )
}

interface TotalProps {
  label: string
  value: string
  tone?: 'up' | 'down' | 'flat'
  /** 百分比这类推不出本金的读数不遮（默认遮）。 */
  mask?: boolean
}

function Total({ label, value, tone, mask = true }: TotalProps) {
  const className = cn('ldst-num text-subtitle font-semibold', tone ? TONE_CLASS[tone] : 'text-text')
  return (
    <div className="flex flex-col gap-1">
      <span className="text-caption text-text-secondary">{label}</span>
      {mask ? <PrivacyText className={className} text={value} /> : <span className={className}>{value}</span>}
    </div>
  )
}

function confirmTitle(confirm: Confirm | null): string {
  if (!confirm) return ''
  if (confirm.kind === 'delete') return '删除这笔流水？'
  if (confirm.kind === 'adopt') return '确认转成期初持仓？'
  return '确认不要这条旧记录？'
}

function confirmDescription(confirm: Confirm | null): string {
  if (!confirm) return ''
  if (confirm.kind === 'delete') {
    const trade = confirm.trade
    return `${trade.date} ${tradeTypeLabel(trade.type)} ${trade.symbol}。删除后持仓与盈亏会按剩下的流水重算；删前后端会先备份。`
  }
  const row = confirm.row
  const shares = row.shares === null ? MISSING : plain(row.shares)
  const cost = row.cost === null ? MISSING : priceText(row.cost)
  if (confirm.kind === 'adopt') {
    return `${row.symbol || '无代码'} · ${shares} 股 · 成本 ${cost}。${
      row.migrated_trade !== null ? '已按它转过一笔，这次会原地替换那笔。' : '会补一笔「期初持仓」流水。'
    }处理前先备份；旧记录只标记、不删除。`
  }
  return `${row.symbol || '无代码'} · ${shares} 股 · 成本 ${cost}。只会写一个「不要这条」的标记，positions 旧行原样保留，之后仍可按备份对照。`
}

function confirmLabel(confirm: Confirm | null): string {
  if (!confirm) return '确认'
  if (confirm.kind === 'delete') return '删除'
  return confirm.kind === 'adopt' ? adoptLabel(confirm.row) : dismissLabel(confirm.row)
}

export default PortfolioPanel
