import { useMemo, useRef, useState } from 'react'
import { Database, FileUp, Lock, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react'

import { isApiError } from '../../api'
import {
  isRadarEndpointMissing,
  type PositionInput,
  type PositionItem,
  type PositionsPayload,
} from '../../api/radar'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  DataTable,
  Drawer,
  EmptyState,
  ErrorBlock,
  Field,
  HBarList,
  IconButton,
  Input,
  MetricCard,
  SectionCard,
  Textarea,
  type Column,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  formatChangePct,
  formatInt,
  formatMoney,
  formatOneDecimal,
  formatRatePct,
  MISSING,
} from '../../lib/format'
import { withParams } from '../industry/links'
import { useDemoEnabled, usePositionsView } from './useRadarView'

/**
 * P-R10-04 我的持仓（`/positions`）。
 *
 * 隐私边界（DEC-006 / DEC-020 / SPEC §6.3）：
 * - 持仓只存在于本机 SQLite 与本次请求的 React 内存里；页面不写 localStorage / IndexedDB，
 *   不把持仓放进 URL、日志或 AI 上下文，也没有同步 / 分享入口。
 * - 盈亏、集中度、行业分布全部来自接口载荷，页面不重算；`null` 显示 `—` + 原因。
 * - 接口缺失时的兜底：导入用户自己 `ldst position list --export` 的 JSON（只读），
 *   以及在写失败时给出等价 CLI 命令。
 */

interface EditorState {
  mode: 'add' | 'edit'
  item: PositionItem | null
}

interface MutationError {
  text: string
  code: string | null
}

function describeMutationError(error: unknown): MutationError {
  if (isApiError(error)) {
    return { text: error.detail ?? error.message, code: error.status ? `HTTP ${error.status}` : null }
  }
  return { text: error instanceof Error ? error.message : String(error), code: null }
}

function signedClass(value: number | null): string {
  if (value === null) return 'text-text'
  if (value > 0) return 'text-up'
  if (value < 0) return 'text-down'
  return 'text-text-secondary'
}

function pctText(value: number | null): string {
  return formatChangePct(value).text
}

function PrivacyNotice() {
  return (
    <div
      role="note"
      aria-label="隐私说明"
      className="flex items-start gap-3 rounded border border-accent bg-accent-soft px-3 py-2"
    >
      <Lock className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
      <div className="min-w-0 text-body leading-body">
        <p className="font-semibold text-text">数据只在本机，不上传、不进 AI</p>
        <p className="text-text-secondary">
          持仓保存在本机 SQLite（<code className="ldst-num">ldst position</code>），本页只在内存里展示本次请求结果；
          不写 localStorage / IndexedDB，不进 URL、日志或 AI 上下文，也没有同步 / 分享入口。导出只在你显式运行 CLI 时发生。
        </p>
      </div>
    </div>
  )
}

function ValuationCards({ payload }: { payload: PositionsPayload }) {
  const { valuation, concentration } = payload
  const unpriced = valuation.unpriced_cost > 0
  const coverage = valuation.coverage_pct === null ? MISSING : formatOneDecimal(valuation.coverage_pct)
  const basisLabel = concentration.basis === 'market_value' ? '市值' : '成本'

  return (
    <div className="grid grid-cols-2 gap-3 desk:grid-cols-5">
      <MetricCard
        label="持仓数"
        hero
        value={payload.position_count}
        unit="只"
        note={`数据时点 ${payload.as_of ?? MISSING}`}
      />
      <MetricCard
        label="总成本"
        value={formatMoney(valuation.total_cost)}
        note={`价格来源 ${payload.price_source || MISSING}`}
      />
      <MetricCard
        label="市值"
        value={valuation.market_value === null ? MISSING : formatMoney(valuation.market_value)}
        note={
          valuation.market_value === null
            ? `${formatMoney(valuation.unpriced_cost)} 成本未取到价格，退回成本口径`
            : `覆盖 ${coverage}% 成本`
        }
      />
      <MetricCard
        label="浮动盈亏"
        value={
          <span className={cn('ldst-num', signedClass(valuation.unrealized_pnl))}>
            {valuation.unrealized_pnl === null
              ? MISSING
              : `${valuation.unrealized_pnl > 0 ? '+' : ''}${formatMoney(valuation.unrealized_pnl)}`}
          </span>
        }
        note={
          unpriced
            ? `仅统计已取价部分 · 未取价 ${formatMoney(valuation.unpriced_cost)}`
            : `盈亏率 ${pctText(valuation.unrealized_pnl_pct)} · 覆盖 ${coverage}%`
        }
      />
      <MetricCard
        label="集中度 Top1"
        value={concentration.available && concentration.top1_pct !== null ? formatRatePct(concentration.top1_pct).text : MISSING}
        note={
          concentration.available
            ? `Top3 ${concentration.top3_pct === null ? MISSING : formatRatePct(concentration.top3_pct).text} · HHI ${
                concentration.hhi === null ? MISSING : formatOneDecimal(concentration.hhi)
              } · 口径 ${basisLabel}`
            : (concentration.reason ?? '无法计算集中度')
        }
      />
    </div>
  )
}

function ConcentrationCards({ payload }: { payload: PositionsPayload }) {
  const { concentration, sector_distribution: sector } = payload
  const basisLabel = concentration.basis === 'market_value' ? '市值' : '成本'
  return (
    <div className="grid grid-cols-1 gap-3 desk:grid-cols-2">
      <SectionCard
        title="集中度"
        subtitle={concentration.available ? `按${basisLabel}口径 · Top1 / Top3 / HHI` : '无法计算'}
        tone={concentration.available ? 'neutral' : 'warn'}
      >
        {concentration.available ? (
          <HBarList
            items={concentration.holdings.map((holding) => ({
              label: holding.code,
              value: holding.weight_pct,
              hint: `成本 ${formatMoney(holding.cost)}`,
            }))}
            unit="%"
            format={(value) => value.toFixed(2)}
          />
        ) : (
          <EmptyState compact title="无法计算集中度" description={concentration.reason ?? '持仓明细不足。'} />
        )}
        {concentration.available && concentration.hhi !== null && (
          <p className="mt-3 text-caption text-text-muted">
            HHI {formatOneDecimal(concentration.hhi)}（1 为单票满仓）；权重为接口原值，页面不重算。
          </p>
        )}
      </SectionCard>

      <SectionCard
        title="行业分布"
        subtitle={sector.available ? `按${sector.basis === 'market_value' ? '市值' : '成本'}口径` : '无法计算'}
        tone={sector.available ? 'neutral' : 'warn'}
      >
        {sector.available && sector.industries.length > 0 ? (
          <>
            <HBarList
              items={sector.industries.map((row) => ({
                label: row.industry,
                value: row.weight_pct,
                hint: `成本 ${formatMoney(row.cost)}`,
              }))}
              unit="%"
              format={(value) => value.toFixed(2)}
            />
            <p className="mt-3 text-caption text-text-muted">
              已标注行业 {sector.labeled_count} 只
              {sector.unlabeled_count > 0
                ? ` · ${sector.unlabeled_count} 只未标注行业，不计入分布（仍计入集中度）`
                : ''}
              。
            </p>
          </>
        ) : (
          <EmptyState
            compact
            title="没有行业分布"
            description={sector.reason ?? '持仓没有行业标签，或明细不足。'}
          />
        )}
      </SectionCard>
    </div>
  )
}

function SignedMoney({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-text-muted" title="未取到价格，无法计算">—</span>
  }
  return (
    <span className={cn('ldst-num', signedClass(value))}>
      {value > 0 ? '+' : ''}
      {formatMoney(value)}
    </span>
  )
}

function buildColumns(
  editable: boolean,
  onEdit: (item: PositionItem) => void,
  onDelete: (item: PositionItem) => void,
): Array<Column<PositionItem>> {
  const columns: Array<Column<PositionItem>> = [
    {
      key: 'code',
      header: '代码',
      width: '88px',
      sort: (a, b) => a.code.localeCompare(b.code),
      render: (row) => <span className="ldst-num font-medium text-text">{row.code}</span>,
    },
    {
      key: 'industry',
      header: '行业',
      width: '88px',
      render: (row) =>
        row.industry ? (
          <span className="truncate text-text-secondary">{row.industry}</span>
        ) : (
          <span className="text-caption text-text-muted">未标注</span>
        ),
    },
    {
      key: 'cost',
      header: '成本价',
      align: 'right',
      width: '96px',
      sort: (a, b) => a.cost - b.cost,
      render: (row) => <span className="ldst-num text-text">{formatMoney(row.cost)}</span>,
    },
    {
      key: 'qty',
      header: '数量',
      align: 'right',
      width: '80px',
      sort: (a, b) => a.qty - b.qty,
      render: (row) => <span className="ldst-num text-text">{formatInt(row.qty)}</span>,
    },
    {
      key: 'price',
      header: '最新价',
      align: 'right',
      width: '88px',
      render: (row) =>
        row.latest_price === null ? (
          <span className="text-caption text-text-muted" title="未取到当日收盘价">—</span>
        ) : (
          <span className="ldst-num text-text">{formatMoney(row.latest_price)}</span>
        ),
    },
    {
      key: 'market_value',
      header: '市值',
      align: 'right',
      width: '104px',
      sort: (a, b) => (a.market_value ?? -1) - (b.market_value ?? -1),
      render: (row) =>
        row.market_value === null ? (
          <span className="text-caption text-text-muted" title="未取到价格">—</span>
        ) : (
          <span className="ldst-num text-text">{formatMoney(row.market_value)}</span>
        ),
    },
    {
      key: 'pnl',
      header: '浮动盈亏',
      align: 'right',
      width: '112px',
      sort: (a, b) => (a.unrealized_pnl ?? Number.NEGATIVE_INFINITY) - (b.unrealized_pnl ?? Number.NEGATIVE_INFINITY),
      render: (row) => <SignedMoney value={row.unrealized_pnl} />,
    },
    {
      key: 'pnl_pct',
      header: '盈亏率',
      align: 'right',
      width: '88px',
      sort: (a, b) =>
        (a.unrealized_pnl_pct ?? Number.NEGATIVE_INFINITY) - (b.unrealized_pnl_pct ?? Number.NEGATIVE_INFINITY),
      render: (row) => (
        <span className={cn('ldst-num', signedClass(row.unrealized_pnl_pct))}>
          {pctText(row.unrealized_pnl_pct)}
        </span>
      ),
    },
    {
      key: 'entry',
      header: '建仓日',
      align: 'right',
      width: '96px',
      render: (row) => <span className="ldst-num text-caption text-text-secondary">{row.entry_date ?? MISSING}</span>,
    },
    {
      key: 'note',
      header: '备注',
      width: '140px',
      render: (row) =>
        row.note ? (
          <span className="block max-w-[140px] truncate text-caption text-text-secondary" title={row.note}>
            {row.note}
          </span>
        ) : (
          <span className="text-caption text-text-muted">—</span>
        ),
    },
  ]

  if (editable) {
    columns.push({
      key: 'action',
      header: '操作',
      align: 'right',
      width: '88px',
      render: (row) => (
        <span className="inline-flex items-center gap-1">
          <IconButton
            label={`编辑 ${row.code}`}
            icon={<Pencil className="size-3.5" aria-hidden="true" />}
            onClick={() => onEdit(row)}
          />
          <IconButton
            label={`删除 ${row.code}`}
            icon={<Trash2 className="size-3.5" aria-hidden="true" />}
            onClick={() => onDelete(row)}
          />
        </span>
      ),
    })
  }

  return columns
}

function cliHint(mode: 'add' | 'edit' | 'delete', item: PositionItem | null, input?: PositionInput): string {
  if (mode === 'add' && input) {
    return `ldst position add --code ${input.code} --cost ${input.cost} --qty ${input.qty}`
  }
  if (mode === 'edit' && item && input) {
    return `ldst position update --code ${item.code} --cost ${input.cost} --qty ${input.qty}`
  }
  return `ldst position remove --code ${item?.code ?? '<code>'}`
}

function PositionForm({
  initial,
  submitting,
  error,
  onSubmit,
  onCancel,
}: {
  initial: PositionItem | null
  submitting: boolean
  error: MutationError | null
  onSubmit: (input: PositionInput) => void
  onCancel: () => void
}) {
  const [code, setCode] = useState(initial?.code ?? '')
  const [cost, setCost] = useState(initial ? String(initial.cost) : '')
  const [qty, setQty] = useState(initial ? String(initial.qty) : '')
  const [entryDate, setEntryDate] = useState(initial?.entry_date ?? '')
  const [industry, setIndustry] = useState(initial?.industry ?? '')
  const [note, setNote] = useState(initial?.note ?? '')
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = () => {
    const trimmed = code.trim()
    if (!initial && !trimmed) {
      setLocalError('代码不能为空')
      return
    }
    const costValue = Number(cost)
    const qtyValue = Number(qty)
    if (!Number.isFinite(costValue) || costValue <= 0) {
      setLocalError('成本价必须大于 0')
      return
    }
    if (!Number.isFinite(qtyValue) || qtyValue <= 0) {
      setLocalError('数量必须大于 0')
      return
    }
    setLocalError(null)
    onSubmit({
      code: initial ? initial.code : trimmed,
      cost: costValue,
      qty: qtyValue,
      ...(entryDate ? { entry_date: entryDate } : {}),
      ...(industry.trim() ? { industry: industry.trim() } : {}),
      ...(note.trim() ? { note: note.trim() } : {}),
    })
  }

  const shown = localError ?? error?.text ?? null

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <Field label="代码" htmlFor="position-code" required={!initial} hint={initial ? '代码不可修改，需要换代码请删除后新增' : '6 位 A 股代码'}>
        <Input
          id="position-code"
          value={code}
          disabled={Boolean(initial)}
          placeholder="600000"
          onChange={(event) => setCode(event.target.value)}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="成本价" htmlFor="position-cost" required>
          <Input
            id="position-cost"
            type="number"
            step="0.001"
            min="0"
            value={cost}
            onChange={(event) => setCost(event.target.value)}
          />
        </Field>
        <Field label="数量" htmlFor="position-qty" required>
          <Input
            id="position-qty"
            type="number"
            step="1"
            min="0"
            value={qty}
            onChange={(event) => setQty(event.target.value)}
          />
        </Field>
      </div>
      <Field label="建仓日" htmlFor="position-entry" hint="可选">
        <Input
          id="position-entry"
          type="date"
          value={entryDate}
          onChange={(event) => setEntryDate(event.target.value)}
        />
      </Field>
      <Field label="行业" htmlFor="position-industry" hint="可选；未标注时不计入行业分布">
        <Input
          id="position-industry"
          value={industry}
          placeholder="食品饮料"
          onChange={(event) => setIndustry(event.target.value)}
        />
      </Field>
      <Field label="备注" htmlFor="position-note" hint="可选；本地自用，纯文本保存">
        <Textarea
          id="position-note"
          value={note}
          maxLength={200}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>

      {shown && (
        <div role="alert" className="rounded border border-danger bg-danger-soft px-3 py-2 text-body text-danger">
          <p>{shown}</p>
          {error?.code && <p className="text-caption">{error.code}</p>}
          <p className="mt-1 text-caption text-text-secondary">
            写接口不可用时，可直接在本机终端执行：
            <code className="ldst-num mt-1 block break-all rounded bg-sunken px-2 py-1 text-text">
              {cliHint(initial ? 'edit' : 'add', initial, {
                code: initial?.code ?? code.trim(),
                cost: Number(cost) || 0,
                qty: Number(qty) || 0,
              })}
            </code>
          </p>
        </div>
      )}

      <div className="mt-1 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={submitting}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting}>
          {initial ? '保存修改' : '新增持仓'}
        </Button>
      </div>
    </form>
  )
}

function importedItems(value: unknown): PositionItem[] | null {
  const root = value as { items?: unknown } | unknown[] | null
  const rows = Array.isArray(root) ? root : Array.isArray(root?.items) ? root.items : null
  if (!rows) return null
  return rows
    .map((row): PositionItem | null => {
      if (typeof row !== 'object' || row === null) return null
      const record = row as Record<string, unknown>
      const code = typeof record.code === 'string' ? record.code.trim() : ''
      const cost = typeof record.cost === 'number' ? record.cost : Number(record.cost)
      const qty = typeof record.qty === 'number' ? record.qty : Number(record.qty)
      if (!code || !Number.isFinite(cost) || !Number.isFinite(qty)) return null
      return {
        code,
        cost,
        qty,
        entry_date: typeof record.entry_date === 'string' ? record.entry_date : null,
        industry: typeof record.industry === 'string' ? record.industry : '',
        note: typeof record.note === 'string' ? record.note : '',
        latest_price: null,
        market_value: null,
        unrealized_pnl: null,
        unrealized_pnl_pct: null,
      } satisfies PositionItem
    })
    .filter((row): row is PositionItem => row !== null)
}

export default function PositionsScreen() {
  const demo = useDemoEnabled()
  const view = usePositionsView()
  const { forced, state, payload, error, reload } = view

  const [editor, setEditor] = useState<EditorState | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<MutationError | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PositionItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [imported, setImported] = useState<PositionItem[] | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const items = imported ?? payload?.items ?? []
  const editable = imported === null

  const onEdit = (item: PositionItem) => {
    setFormError(null)
    setEditor({ mode: 'edit', item })
  }
  const onDelete = (item: PositionItem) => {
    setDeleteTarget(item)
  }

  const columns = useMemo(() => buildColumns(editable, onEdit, onDelete), [editable])

  const submit = async (input: PositionInput) => {
    if (!editor) return
    setSubmitting(true)
    setFormError(null)
    try {
      if (editor.mode === 'add') {
        await view.addPosition(input)
      } else if (editor.item) {
        await view.updatePosition(editor.item.code, {
          cost: input.cost,
          qty: input.qty,
          entry_date: input.entry_date ?? '',
          industry: input.industry ?? '',
          note: input.note ?? '',
        })
      }
      setEditor(null)
      setMessage(`${editor.mode === 'add' ? '已新增' : '已更新'} ${input.code}（本机 SQLite）`)
    } catch (cause) {
      setFormError(describeMutationError(cause))
    } finally {
      setSubmitting(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await view.removePosition(deleteTarget.code)
      setMessage(`已删除 ${deleteTarget.code}（本机 SQLite）`)
      setDeleteTarget(null)
    } catch (cause) {
      setMessage(describeMutationError(cause).text)
      setDeleteTarget(null)
    } finally {
      setDeleting(false)
    }
  }

  const importFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const parsed: unknown = JSON.parse(String(reader.result))
        const rows = importedItems(parsed)
        if (!rows || rows.length === 0) {
          throw new Error('文件里没有可识别的持仓行（需要 items 数组）')
        }
        setImported(rows)
        setImportError(null)
        setMessage(`已导入 ${rows.length} 条持仓（只读视图，不计算估值）`)
      } catch (cause) {
        setImportError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    reader.onerror = () => setImportError('读取文件失败')
    reader.readAsText(file, 'utf-8')
  }

  const header = (
    <PageHeader
      title="我的持仓"
      subtitle={`${payload?.as_of ?? MISSING} · ${payload?.position_count ?? 0} 只 · 价格来源 ${
        payload?.price_source || MISSING
      }`}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="sr-only"
            aria-label="导入本地持仓 JSON"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) importFile(file)
              event.target.value = ''
            }}
          />
          <Button
            variant="secondary"
            onClick={() => fileRef.current?.click()}
            leadingIcon={<FileUp className="size-3.5" aria-hidden="true" />}
          >
            导入本地 JSON
          </Button>
          <Button
            variant="secondary"
            onClick={reload}
            disabled={state === 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              setFormError(null)
              setEditor({ mode: 'add', item: null })
            }}
            leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
          >
            新增持仓
          </Button>
        </div>
      }
    />
  )

  const notices = (
    <>
      <PrivacyNotice />
      {message && (
        <Banner
          tone="ok"
          title={message}
          action={
            <IconButton
              label="关闭提示"
              icon={<X className="size-4" aria-hidden="true" />}
              onClick={() => setMessage(null)}
            />
          }
        />
      )}
      {importError && (
        <Banner
          tone="danger"
          title="导入失败"
          action={
            <Button variant="ghost" onClick={() => setImportError(null)}>
              知道了
            </Button>
          }
        >
          {importError}
        </Banner>
      )}
      {imported && (
        <Banner
          tone="warn"
          title={`本地文件视图 · ${imported.length} 条（只读）`}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setImported(null)
                setMessage(null)
              }}
            >
              退出导入视图
            </Button>
          }
        >
          导入的持仓只用于核对；估值、集中度与行业分布需要接口提供，页面不重算，也不写入任何存储。
        </Banner>
      )}
    </>
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {notices}
        <ScreenSkeleton metrics={5} rows={6} />
      </div>
    )
  }

  if (state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {notices}
        <OfflineBanner onRetry={reload} />
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入 /api/positions；未连接时页面不缓存、不伪造任何持仓。"
            action={<Button variant="secondary" onClick={reload}>重新检查</Button>}
          />
        </Card>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {notices}
        <ErrorBlock
          variant="banner"
          title="持仓取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty' || !payload) {
    const endpointMissing = payload === null && isRadarEndpointMissing(error)
    return (
      <div className="flex flex-col gap-5">
        {header}
        {notices}
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title={endpointMissing ? '接口未接入' : '还没有持仓'}
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/positions</code> 契约实现。接口上线前，可以点右上角
                  「导入本地 JSON」查看自己 <code className="ldst-num">ldst position list --export</code> 出来的文件
                  （只读，不上传）。
                </>
              ) : (
                <>
                  持仓存在本机 SQLite 里。点「新增持仓」逐条录入，或在终端运行{' '}
                  <code className="ldst-num">ldst position add</code>。
                </>
              )
            }
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button
                  variant="primary"
                  onClick={() => {
                    setFormError(null)
                    setEditor({ mode: 'add', item: null })
                  }}
                >
                  新增持仓
                </Button>
                <Button variant="secondary" onClick={reload}>刷新</Button>
                {!demo && (
                  <a
                    href={withParams('/positions', { demo: '1', state: forced })}
                    className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
                  >
                    查看演示数据
                  </a>
                )}
              </div>
            }
          />
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}
      {notices}

      {state === 'partial' && imported === null && (
        <Banner tone="warn" title="部分持仓未取到价格">
          未取价成本 {formatMoney(payload.valuation.unpriced_cost)}，覆盖率{' '}
          {payload.valuation.coverage_pct === null ? MISSING : `${formatOneDecimal(payload.valuation.coverage_pct)}%`}
          。市值与盈亏退回成本口径显示，缺失行用 `—` 标注，不补 0。
        </Banner>
      )}

      {imported === null && <ValuationCards payload={payload} />}

      <Card>
        <CardHeader
          title="持仓明细"
          subtitle={`${items.length} 条 · ${imported ? '本地文件视图（只读）' : '本机 SQLite'}`}
        />
        <CardBody className="p-0">
          <DataTable<PositionItem>
            columns={columns}
            rows={items}
            rowKey={(row) => row.code}
            density="compact"
            caption={
              imported
                ? '导入视图只显示代码 / 成本 / 数量；估值需要接口，页面不重算。'
                : '成本价与数量为本地录入；最新价 / 市值 / 盈亏来自当日行情接口。'
            }
            empty={<EmptyState compact title="没有持仓" description="点「新增持仓」录入第一条。" />}
          />
        </CardBody>
      </Card>

      {imported === null && <ConcentrationCards payload={payload} />}

      <p className="text-caption leading-body text-text-muted">
        口径：盈亏、集中度、行业分布全部取自 <code className="ldst-num">GET /api/positions</code>，页面不重算；
        未取到价格的行显示 `—`。持仓不写浏览器存储、不进 URL、不进 AI 上下文——离开本页即从内存释放。
      </p>

      <Drawer
        open={editor !== null}
        onClose={() => {
          setEditor(null)
          setFormError(null)
        }}
        title={editor?.mode === 'edit' ? `编辑 ${editor.item?.code ?? ''}` : '新增持仓'}
        subtitle="数据只写入本机 SQLite，不上传"
      >
        {editor && (
          <PositionForm
            key={editor.mode === 'edit' ? (editor.item?.code ?? 'edit') : 'add'}
            initial={editor.item}
            submitting={submitting}
            error={formError}
            onSubmit={submit}
            onCancel={() => {
              setEditor(null)
              setFormError(null)
            }}
          />
        )}
      </Drawer>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`删除 ${deleteTarget?.code ?? ''}？`}
        description="只删本机 SQLite 里的这一条持仓，不影响交易日志；操作不可撤销。"
        confirmLabel="删除"
        tone="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
