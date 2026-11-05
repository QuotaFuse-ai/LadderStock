import { useMemo, useState } from 'react'
import { Eye, Plus, RefreshCw, Trash2 } from 'lucide-react'

import { isApiError } from '../../api'
import {
  addWatch,
  fetchWatchlist,
  removeWatch,
  type WatchlistItem,
} from '../../api/radar'
import { OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Banner,
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorBlock,
  IconButton,
  Input,
  PrivacyNotice,
  SectionCard,
  SignedNumber,
  SourceStatusBadge,
  type Column,
} from '../../components/ui'
import {
  MISSING,
  countOrMissing,
  formatChangePct,
  formatTime,
} from '../../lib/format'
import { useDensity } from '../../lib/prefs'
import { useAsync } from '../../lib/useAsync'

/**
 * P-QUOT-419 我的自选（`/watchlist`）。
 *
 * 与首页 C 块同一份 4 列（代码 / 名称 / 现价 / 涨跌），加一列行内删除；
 * 新增走 `POST /api/watchlist`（DEC-055），删除走 `POST /api/watchlist/{code}/delete`。
 * 保存 / 删除成功后重新拉 `GET /api/watchlist`，回首页时卡片随重新挂载再取一次，
 * 不需要手动刷新，更不需要跑 CLI。
 *
 * 隐私边界不变（DEC-052 / DEC-055）：读写只走 127.0.0.1，响应 `no-store`；
 * 清单只活在本次请求的 React 内存里，不写 localStorage / IndexedDB，不进 URL 与 AI 上下文。
 */

const CODE_PATTERN = /^(?:sh|sz|bj)?\d{6}$/i

const COLUMNS: Array<Column<WatchlistItem>> = [
  {
    key: 'code',
    header: '代码',
    width: '88px',
    sort: (a, b) => a.code.localeCompare(b.code),
    render: (row) => <span className="ldst-num text-text-secondary">{row.code}</span>,
  },
  {
    key: 'name',
    header: '名称',
    truncate: true,
    render: (row) => (
      <span className={row.name ? 'font-medium' : 'text-text-muted'}>{row.name ?? '名称未接入'}</span>
    ),
  },
  {
    key: 'price',
    header: '现价',
    width: '112px',
    numeric: true,
    sort: (a, b) =>
      (a.latest_price ?? Number.NEGATIVE_INFINITY) - (b.latest_price ?? Number.NEGATIVE_INFINITY),
    render: (row) => (
      <span className={row.latest_price === null ? 'text-text-muted' : undefined}>
        {row.latest_price === null ? MISSING : row.latest_price.toFixed(2)}
      </span>
    ),
  },
  {
    key: 'change',
    header: '涨跌',
    width: '112px',
    numeric: true,
    sort: (a, b) =>
      (a.change_pct ?? Number.NEGATIVE_INFINITY) - (b.change_pct ?? Number.NEGATIVE_INFINITY),
    render: (row) => {
      const change = formatChangePct(row.change_pct)
      return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
    },
  },
]

function describeError(error: unknown): string {
  if (isApiError(error)) return error.detail ?? error.message
  return error instanceof Error ? error.message : String(error)
}

export default function WatchlistScreen() {
  const density = useDensity()
  const view = useAsync((signal) => fetchWatchlist(signal), [])
  const payload = view.data
  const items = useMemo(() => (payload?.items ?? []).filter((item) => Boolean(item.code)), [payload])
  const count = countOrMissing(payload?.watch_count ?? (payload ? items.length : null))

  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<WatchlistItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  const submit = async () => {
    const trimmed = code.trim()
    if (!CODE_PATTERN.test(trimmed)) {
      setFormError('请输入 6 位代码（可带 sh/sz/bj 前缀）')
      return
    }
    setSubmitting(true)
    setFormError(null)
    try {
      const result = await addWatch(trimmed)
      setMessage(`已加入自选 ${result.watch?.code ?? trimmed}（本机 SQLite）`)
      setCode('')
      view.reload()
    } catch (cause) {
      setFormError(describeError(cause))
    } finally {
      setSubmitting(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const result = await removeWatch(deleteTarget.code)
      setMessage(`已移除 ${result.removed?.code ?? deleteTarget.code}（本机 SQLite）`)
      view.reload()
    } catch (cause) {
      setMessage(describeError(cause))
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  const columns = useMemo<Array<Column<WatchlistItem>>>(
    () => [
      ...COLUMNS,
      {
        key: 'action',
        header: '操作',
        align: 'right',
        width: '72px',
        render: (row) => (
          <IconButton
            label={`移除 ${row.code}`}
            icon={<Trash2 className="size-3.5" aria-hidden="true" />}
            onClick={() => setDeleteTarget(row)}
          />
        ),
      },
    ],
    [],
  )

  const header = (
    <PageHeader
      title="我的自选"
      subtitle={
        payload
          ? `${count} 只 · 数据时点 ${payload.price_as_of ?? MISSING}`
          : '自选只存本机'
      }
      badge={
        payload ? (
          <SourceStatusBadge
            status={payload.data_source_status}
            detail={payload.reason ?? payload.source_detail}
          />
        ) : undefined
      }
      actions={
        <Button
          variant="secondary"
          onClick={view.reload}
          disabled={view.status === 'loading'}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新
        </Button>
      }
    />
  )

  const notices = (
    <>
      <PrivacyNotice subject="自选" storeCommand="ldst watch" />
      {message && (
        <Banner
          tone="ok"
          title={message}
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
      title="新增自选"
      subtitle="6 位代码（可带 sh/sz/bj 前缀）；只写入本机 SQLite"
    >
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <label className="flex min-w-48 flex-1 flex-col gap-1">
          <span className="text-caption text-text-secondary">证券代码</span>
          <Input
            value={code}
            inputMode="numeric"
            placeholder="600000"
            aria-label="证券代码"
            onChange={(event) => setCode(event.target.value)}
          />
        </label>
        <Button
          type="submit"
          variant="primary"
          loading={submitting}
          leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
        >
          加入自选
        </Button>
      </form>
      {formError && (
        <p role="alert" className="mt-3 rounded border border-danger bg-danger-soft px-3 py-2 text-body text-danger">
          {formError}
        </p>
      )}
      <p className="mt-3 text-caption leading-body text-text-muted">
        已存在的代码不会重复添加，也不会刷新加入时间；移除只删本机记录，可随时再加回来。
      </p>
    </SectionCard>
  )

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
            '现价与涨跌来自本机行情快照'
          )
        }
        tone={payload && payload.data_source_status !== 'ok' ? 'warn' : 'neutral'}
        bodyClassName="p-0"
      >
        {view.status === 'loading' && (
          <div className="flex flex-col gap-2 p-4" aria-hidden="true">
            {Array.from({ length: 5 }, (_, index) => (
              <div key={index} className="h-row rounded bg-surface-2" />
            ))}
          </div>
        )}

        {view.status === 'error' &&
          (isApiError(view.error) && view.error.kind === 'not-connected' ? (
            <div className="p-4">
              <OfflineBanner onRetry={view.reload} />
            </div>
          ) : (
            <div className="p-4">
              <ErrorBlock
                variant="banner"
                title="自选取数失败"
                detail={describeError(view.error)}
                onRetry={view.reload}
                retryLabel="重试"
              />
            </div>
          ))}

        {view.status === 'ready' && (
          <DataTable<WatchlistItem>
            columns={columns}
            rows={items}
            rowKey={(row) => row.code}
            caption={`我的自选（${count} 只），现价与涨跌来自本机行情快照`}
            density={density}
            maxHeight="520px"
            empty={
              <EmptyState
                icon={<Eye aria-hidden="true" />}
                title="还没有自选"
                description="在上面的输入框加一只 6 位代码，保存后首页「我的自选」卡会立即出现。"
              />
            }
          />
        )}
      </SectionCard>

      <p className="text-caption leading-body text-text-muted">
        自选只走 127.0.0.1 回环读写，响应不进缓存；不上传、不进 AI 上下文，也不写浏览器存储。
        现价与涨跌是行情快照，不是逐笔实时。
      </p>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`移除 ${deleteTarget?.code ?? ''}？`}
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
