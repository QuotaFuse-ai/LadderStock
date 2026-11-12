import { useEffect, useMemo, useRef, useState } from 'react'
import { Eye, Plus, Trash2 } from 'lucide-react'

import { isApiError } from '../../api'
import {
  addWatch,
  fetchPortfolioSymbols,
  fetchWatchlist,
  removeWatch,
  type WatchlistItem,
} from '../../api/radar'
import { KlineCard } from '../../components/KlineCard'
import { OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Banner,
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorBlock,
  IconButton,
  PrivacyNotice,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  Textarea,
  type Column,
} from '../../components/ui'
import {
  MISSING,
  countOrMissing,
  formatChangePct,
  formatTime,
  isFiniteNumber,
} from '../../lib/format'
import { useDensity } from '../../lib/prefs'
import { useAsync } from '../../lib/useAsync'
import { planWatchBatch, type BatchPlan } from '../../lib/watchBatch'
import { cn } from '../../lib/cn'

/**
 * 我的自选（QUOT-431）：`/my-stocks?tab=watch`。
 *
 * 七列与「是否持有 / 点行看 K 线 / 批量添加」按参考库 `Watchlist.tsx` 复刻，
 * 数据面换成本仓的 `GET /api/watchlist`（QUOT-427 起已带 `pe_ttm` / `pb` /
 * `turnover_pct` 与逐条 `metrics_reason`）。
 *
 * 三条口径：
 * 1. 只收 6 位 A 股。批量输入按空格 / 逗号 / 换行拆分，逐条给原因（港股 / 美股 /
 *    B 股 / 位数不对各自一句），不猜市场、不静默丢弃（`lib/watchBatch.ts`）。
 * 2. 缺失值一律 `—`，不补 0、不画骨架行；指标缺列的原因放在单元格 tooltip 里。
 * 3. `是否持有` 只是标记：`GET /api/portfolio` 取不到就不标，不影响表格。
 */

const CODE_COLUMN_WIDTH = '96px'
const NUM_COLUMN_WIDTH = '92px'
const NUM = 'text-right'

function numberText(value: number | null, digits = 2): string {
  return isFiniteNumber(value) ? value.toFixed(digits) : MISSING
}

/** `null` 当负无穷：缺失值不因为排序方向翻到最前面去抢眼。 */
function compareNumbers(a: number | null, b: number | null): number {
  return (a ?? Number.NEGATIVE_INFINITY) - (b ?? Number.NEGATIVE_INFINITY)
}

function describeError(error: unknown): string {
  if (isApiError(error)) return error.detail ?? error.message
  return error instanceof Error ? error.message : String(error)
}

interface Message {
  tone: 'ok' | 'warn'
  text: string
}

/** `600519.SH` / `SH600519` / `sh600519` → 裸 6 位（持仓账本存的就是裸 6 位）。 */
function bareCode(value: string): string {
  return value
    .trim()
    .toUpperCase()
    .replace(/^(SH|SZ|BJ)[.:]?/, '')
    .replace(/\.(SH|SZ|BJ)$/, '')
}

export function WatchlistPanel() {
  const density = useDensity()
  const view = useAsync((signal) => fetchWatchlist(signal), [])
  const payload = view.data
  const items = useMemo(() => (payload?.items ?? []).filter((item) => Boolean(item.code)), [payload])
  const count = countOrMissing(payload?.watch_count ?? (payload ? items.length : null))

  const [held, setHeld] = useState<ReadonlySet<string> | null>(null)
  const [heldMissing, setHeldMissing] = useState(false)
  const [heldNonce, setHeldNonce] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    fetchPortfolioSymbols(controller.signal).then(
      (symbols) => {
        if (!active) return
        setHeld(new Set([...symbols].map(bareCode)))
        setHeldMissing(false)
      },
      () => {
        if (!active) return
        // 持仓读不到只是少一个标记：表格照常展示，并在表下说明缺了什么
        setHeld(null)
        setHeldMissing(true)
      },
    )
    return () => {
      active = false
      controller.abort()
    }
  }, [heldNonce])

  const [input, setInput] = useState('')
  const [pending, setPending] = useState<BatchPlan | null>(null)
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<WatchlistItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const refresh = () => {
    view.reload()
    setHeldNonce((value) => value + 1)
  }

  const preview = () => {
    const plan = planWatchBatch(input, items.map((item) => item.code))
    setPending(plan)
    setMessage(null)
  }

  const cancelPending = () => {
    setPending(null)
    setMessage({ tone: 'warn', text: '已取消，本次没有写入任何自选。' })
  }

  const confirmAdd = async () => {
    if (!pending || pending.accepted.length === 0) return
    const codes = pending.accepted
    setAdding(true)
    const failed: string[] = []
    for (const code of codes) {
      try {
        await addWatch(code)
      } catch (cause) {
        failed.push(`${code}（${describeError(cause)}）`)
      }
    }
    setAdding(false)
    setPending(null)
    // 失败的代码留在输入框里，修好（或重试）不用重新敲一遍
    setInput(failed.map((entry) => entry.replace(/（.*$/, '')).join(' '))
    setMessage(
      failed.length
        ? { tone: 'warn', text: `有 ${failed.length} 只没加上：${failed.join('；')}` }
        : { tone: 'ok', text: `已加入 ${codes.length} 只自选（本机 SQLite）` },
    )
    refresh()
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await removeWatch(deleteTarget.code)
      setMessage({ tone: 'ok', text: `已移除 ${deleteTarget.code}（本机 SQLite）` })
      if (selected === deleteTarget.code) setSelected(null)
      refresh()
    } catch (cause) {
      setMessage({ tone: 'warn', text: describeError(cause) })
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  const columns = useMemo<Array<Column<WatchlistItem>>>(
    () => [
      {
        key: 'name',
        header: '名称',
        truncate: true,
        sort: (a, b) => (a.name ?? '\uffff').localeCompare(b.name ?? '\uffff'),
        cellTitle: (row) => row.name ?? '名称未取到',
        render: (row) => (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className={row.name ? 'truncate font-medium' : 'text-text-muted'}>
              {row.name ?? MISSING}
            </span>
            {held?.has(bareCode(row.code)) && (
              <span className="shrink-0 rounded-xs bg-accent-soft px-1 py-0.5 text-caption text-accent">
                持有
              </span>
            )}
          </span>
        ),
      },
      {
        key: 'code',
        header: '代码',
        width: CODE_COLUMN_WIDTH,
        sort: (a, b) => a.code.localeCompare(b.code),
        render: (row) => <span className="ldst-num text-text-secondary">{row.code}</span>,
      },
      {
        key: 'price',
        header: '现价',
        width: NUM_COLUMN_WIDTH,
        numeric: true,
        sort: (a, b) => compareNumbers(a.latest_price, b.latest_price),
        render: (row) => (
          <span className={cn(row.latest_price === null ? 'text-text-muted' : 'text-text')}>
            {numberText(row.latest_price)}
          </span>
        ),
      },
      {
        key: 'change_pct',
        header: '涨跌%',
        width: NUM_COLUMN_WIDTH,
        numeric: true,
        sort: (a, b) => compareNumbers(a.change_pct, b.change_pct),
        render: (row) => {
          const change = formatChangePct(row.change_pct)
          return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
        },
      },
      {
        key: 'pe_ttm',
        header: 'PE(TTM)',
        width: NUM_COLUMN_WIDTH,
        numeric: true,
        sort: (a, b) => compareNumbers(a.pe_ttm, b.pe_ttm),
        render: (row) => (
          <span
            className={cn(NUM, row.pe_ttm === null ? 'text-text-muted' : 'text-text')}
            title={row.pe_ttm === null ? (row.metrics_reason ?? undefined) : undefined}
          >
            {numberText(row.pe_ttm)}
          </span>
        ),
      },
      {
        key: 'pb',
        header: 'PB',
        width: NUM_COLUMN_WIDTH,
        numeric: true,
        sort: (a, b) => compareNumbers(a.pb, b.pb),
        render: (row) => (
          <span
            className={cn(NUM, row.pb === null ? 'text-text-muted' : 'text-text')}
            title={row.pb === null ? (row.metrics_reason ?? undefined) : undefined}
          >
            {numberText(row.pb)}
          </span>
        ),
      },
      {
        key: 'turnover_pct',
        header: '换手%',
        width: NUM_COLUMN_WIDTH,
        numeric: true,
        sort: (a, b) => compareNumbers(a.turnover_pct, b.turnover_pct),
        render: (row) => (
          <span
            className={cn(NUM, row.turnover_pct === null ? 'text-text-muted' : 'text-text')}
            title={row.turnover_pct === null ? (row.metrics_reason ?? undefined) : undefined}
          >
            {numberText(row.turnover_pct)}
          </span>
        ),
      },
      {
        key: 'action',
        header: '操作',
        align: 'right',
        width: '64px',
        render: (row) => (
          // 行本身可点（看 K 线），删除按钮必须截断冒泡，否则点删除会顺手切图
          <span onClick={(event) => event.stopPropagation()}>
            <IconButton
              label={`移除 ${row.code}`}
              icon={<Trash2 className="size-3.5" aria-hidden="true" />}
              onClick={() => setDeleteTarget(row)}
            />
          </span>
        ),
      },
    ],
    [held],
  )

  const header = (
    <PageHeader
      title="我的自选"
      subtitle={
        payload ? `${count} 只 · 数据时点 ${payload.price_as_of ?? MISSING}` : '自选只存本机'
      }
      badge={
        payload ? (
          <SourceStatusBadge
            status={payload.data_source_status}
            detail={payload.reason ?? payload.source_detail}
          />
        ) : undefined
      }
      refresh={{ onClick: refresh, disabled: view.status === 'loading' }}
    />
  )

  const notices = (
    <>
      <PrivacyNotice subject="自选" />
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
    </>
  )

  const addCard = (
    <SectionCard
      title="批量添加"
      subtitle="6 位 A 股代码，空格 / 逗号 / 换行分隔；确认后才写入本机 SQLite"
    >
      <label className="flex flex-col gap-1">
        <span className="text-caption text-text-secondary">证券代码</span>
        <Textarea
          ref={inputRef}
          value={input}
          rows={2}
          aria-label="批量代码"
          placeholder="600519 000858, 300750"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault()
              if (input.trim()) preview()
            }
          }}
        />
      </label>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!input.trim()}
          leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
          onClick={preview}
        >
          添加
        </Button>
        <span className="text-caption leading-body text-text-muted">
          只收 6 位 A 股；港股 / 美股 / B 股会逐条说明原因，不猜市场、不静默丢弃。
        </span>
      </div>

      {pending && (
        <div className="mt-3 flex flex-col gap-2 rounded border border-border bg-sunken p-3" data-testid="batch-pending">
          <p className="text-body font-medium text-text">
            {pending.accepted.length > 0
              ? `待确认：将加入 ${pending.accepted.length} 只`
              : '没有可加入的新代码'}
          </p>
          {pending.accepted.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label="待加入代码">
              {pending.accepted.map((code) => (
                <li
                  key={code}
                  className="ldst-num rounded-xs border border-border-strong bg-surface px-2 py-0.5 text-caption text-text"
                >
                  {code}
                </li>
              ))}
            </ul>
          )}
          {pending.duplicates.length > 0 && (
            <p className="text-caption leading-body text-text-secondary">
              已在自选里：{pending.duplicates.join('、')}
            </p>
          )}
          {pending.repeated.length > 0 && (
            <p className="text-caption leading-body text-text-secondary">
              输入里重复：{pending.repeated.join('、')}
            </p>
          )}
          {pending.rejected.length > 0 && (
            <ul className="flex flex-col gap-1" aria-label="不能加入的代码">
              {pending.rejected.map((entry) => (
                <li key={`${entry.token}-${entry.reason}`} className="text-caption leading-body text-danger">
                  <span className="ldst-num">{entry.token}</span>：{entry.reason}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {pending.accepted.length > 0 && (
              <Button variant="primary" loading={adding} onClick={() => void confirmAdd()}>
                确认加入 {pending.accepted.length} 只
              </Button>
            )}
            <Button variant="ghost" onClick={cancelPending}>
              不加了
            </Button>
          </div>
        </div>
      )}
    </SectionCard>
  )

  const selectedName = items.find((item) => item.code === selected)?.name ?? undefined

  return (
    <div className="flex flex-col gap-5">
      {header}
      {notices}
      {addCard}

      <SectionCard
        title={payload ? `自选清单 · (${count})` : '自选清单'}
        subtitle={
          payload?.price_as_of ? (
            <span className="ldst-num text-text-muted">{formatTime(payload.price_as_of)}</span>
          ) : (
            '现价与指标来自本机行情快照'
          )
        }
        tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
        bodyClassName="p-0"
      >
        {view.status === 'loading' && (
          <p className="p-4 text-body text-text-muted" aria-busy="true">
            正在取自选…
          </p>
        )}

        {view.status === 'error' &&
          (isApiError(view.error) && view.error.kind === 'not-connected' ? (
            <div className="p-4">
              <OfflineBanner onRetry={refresh} />
            </div>
          ) : (
            <div className="p-4">
              <ErrorBlock
                variant="banner"
                title="自选取数失败"
                detail={describeError(view.error)}
                onRetry={refresh}
                retryLabel="重试"
              />
            </div>
          ))}

        {view.status === 'ready' && (
          <DataTable<WatchlistItem>
            columns={columns}
            rows={items}
            rowKey={(row) => row.code}
            caption={`我的自选（${count} 只）；点一行看它的 K 线，再点一次收起`}
            density={density}
            maxHeight="520px"
            onRowClick={(row) => setSelected((current) => (current === row.code ? null : row.code))}
            rowClassName={(row) => (row.code === selected ? 'bg-accent-soft' : undefined)}
            empty={
              <EmptyState
                icon={<Eye aria-hidden="true" />}
                title="还没有自选"
                description="本机还没有自选。在上面的批量输入框粘贴 6 位 A 股代码（空格 / 逗号 / 换行分隔），确认后加入。"
                action={
                  <Button variant="secondary" onClick={() => inputRef.current?.focus()}>
                    去批量添加
                  </Button>
                }
              />
            }
          />
        )}

        {view.status === 'ready' && heldMissing && items.length > 0 && (
          <p className="border-t border-border px-3 py-2 text-caption leading-body text-text-muted">
            没取到持仓，「持有」标记暂缺；自选本身不受影响。
          </p>
        )}
      </SectionCard>

      {selected && (
        <KlineCard symbol={selected} title={selectedName} />
      )}

      <p className="text-caption leading-body text-text-muted">
        自选只走 127.0.0.1 回环读写，响应不进缓存；不上传、不进 AI 上下文，也不写浏览器存储。
        现价与指标是行情快照，不是逐笔实时；没有值的列显示 {MISSING}。
      </p>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`移除 ${deleteTarget?.code ?? ''} 是否继续？`}
        description="只删本机 SQLite 里的这一条自选，不影响持仓与交易日志；随时可以再加回来。"
        confirmLabel="移除"
        tone="danger"
        loading={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
