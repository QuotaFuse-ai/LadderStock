import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Check, CircleSlash, ClipboardCheck, Plus, Save, Trash2, X } from 'lucide-react'

import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatInt, formatTime } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import {
  api,
  describeApiError,
  isApiError,
  type LadderEnvelope,
  type ValidationConditionWrite,
  type ValidationPayload,
} from '../api'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorBlock,
  Field,
  IconButton,
  Input,
  SectionCard,
  Segmented,
  Textarea,
  Toasts,
  useToasts,
} from '../components/ui'
import { useValidationDraft } from '../../stores/validationDraft'
import { useValidationSource, type ValidationSource } from '../../stores/validationMode'

/**
 * 明日验证（P-06 / DESIGN-SPEC §4.7 · UI-SPEC §3.3）。
 *
 * L0 用**分段控件**在「明日条件」与「收盘对账」之间切换——两种模式是互斥视图，
 * 不是并列的页签，因此不用 `Tabs`。
 *
 * - 明日条件：条件可以放两处，用户自己选（QUOT-186 追加需求）：
 *   · **本地**：读写本机 IndexedDB 草稿（`validation_draft`），默认给本机环境；
 *   · **后端**：读写 `GET /api/validation` 的 `payload.conditions`，保存走
 *     `PUT /api/control/validation/conditions`（QUOT-197），默认给服务器环境。
 *   默认值只看 `location.hostname`，**手动改过之后以用户为准**，不再被环境覆盖。
 * - 收盘对账：只读消费 `GET /api/validation`，逐条判定命中 / 未命中 / 未触发 / 数据缺失；
 *   命中率放标题栏摘要（「命中 4/5」），不做成大数字卡（§4.7 明文要求）。
 *
 * 两处都有数据且内容不同时**绝不自动合并**：只给两个按钮（本地覆盖后端 / 拉取后端覆盖本地），
 * 由人当场决定。自动合并要定「谁赢、按什么键去重、草稿脏了怎么办」，单机自用不值得。
 *
 * 仓位上限：`stores/validationDraft.ts` 的草稿结构里没有该字段（本轮不改 store），
 * 因此列上一律 `—` 并在表下说明「用备注承载」，不编造数字。
 *
 * 已知后端投影缺口（不改后端，只在这里说明）：`server.py:_validation_conditions`
 * 只投影 `code` / `trigger` 与对账结论，**不返回 `note`**——所以后端模式写入的备注
 * 会落盘但读回看不到，界面必须如实讲清，不能假装显示。
 */

type ViewMode = 'conditions' | 'reconcile'

const VIEW_OPTIONS = [
  { value: 'conditions' as const, label: '明日条件' },
  { value: 'reconcile' as const, label: '收盘对账' },
]

/** 屏上统一的行形状：本地草稿与后端条件在这里对齐，渲染与编辑只认它 */
interface ConditionRow {
  key: string
  code: string
  trigger: string
  note: string
  /** 后端条件不记录确认状态，`null` = 该来源没有这个概念 */
  confirmed: boolean | null
}

type Verdict = 'hit' | 'miss' | 'idle' | 'missing'

const VERDICT: Record<Verdict, { label: string; tone: 'ok' | 'danger' | 'flat' | 'warn' }> = {
  hit: { label: '命中', tone: 'ok' },
  miss: { label: '未命中', tone: 'danger' },
  idle: { label: '未触发', tone: 'flat' },
  missing: { label: '数据缺失', tone: 'warn' },
}

interface ReconcileResult {
  key: string
  text: string
  metric: string | null
  operator: string | null
  target: number | null
  actual: number | null
  verdict: Verdict
}

interface ReconcileView {
  status: string | null
  reason: string | null
  nextSession: string | null
  checked: number | null
  passed: number | null
  failed: number | null
  unknown: number | null
  results: ReconcileResult[]
}

function toNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function toText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function verdictOf(passed: unknown, actual: number | null): Verdict {
  if (passed === true) return 'hit'
  if (passed === false) return 'miss'
  // 没有明确通过/未通过：有实际值说明条件没被触发；没有实际值就是数据缺失。
  return actual === null ? 'missing' : 'idle'
}

function parseReconcile(raw: unknown): ReconcileView | null {
  if (!raw || typeof raw !== 'object') return null
  const node = raw as Record<string, unknown>
  const rawResults = Array.isArray(node.results) ? node.results : []
  const results: ReconcileResult[] = rawResults.flatMap((entry, index) => {
    if (!entry || typeof entry !== 'object') return []
    const item = entry as Record<string, unknown>
    const actual = toNumber(item.actual)
    const target = toNumber(item.target)
    const text = toText(item.text) ?? toText(item.metric) ?? `条件 ${index + 1}`
    return [
      {
        key: toText(item.id) ?? `r-${index}`,
        text,
        metric: toText(item.metric),
        operator: toText(item.operator),
        target,
        actual,
        verdict: verdictOf(item.passed, actual),
      },
    ]
  })

  return {
    status: toText(node.status),
    reason: toText(node.reason),
    nextSession: toText(node.next_session),
    checked: toNumber(node.checked),
    passed: toNumber(node.passed),
    failed: toNumber(node.failed),
    unknown: toNumber(node.unknown),
    results,
  }
}

function formatMetricValue(value: number | null): string {
  if (value === null) return MISSING
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

/** 冲突判定只看「代码 + 触发条件」：备注不参与，因为后端 GET 本来就不回备注。 */
function signatureOf(rows: Array<{ code: string; trigger: string }>): string {
  return rows
    .map((row) => `${row.code.trim()}|${row.trigger.trim()}`)
    .sort()
    .join('\n')
}

/** 列宽走 §7.3 的 {56, 88} 档；长文本列吃剩余宽度。 */
const ROW_GRID =
  'grid-cols-[3.5rem_minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,1.5fr)_5.5rem_5.5rem_5.5rem]'

export default function ValidateScreen() {
  const forced = useForcedState()
  const toasts = useToasts()

  // ---- 本地草稿（IndexedDB，store 只读引用，不改定义） ----
  const tradeDate = useValidationDraft((state) => state.tradeDate)
  const draftConditions = useValidationDraft((state) => state.conditions)
  const updatedAt = useValidationDraft((state) => state.updatedAt)
  const addCondition = useValidationDraft((state) => state.addCondition)
  const updateCondition = useValidationDraft((state) => state.updateCondition)
  const removeCondition = useValidationDraft((state) => state.removeCondition)
  const toggleConfirmed = useValidationDraft((state) => state.toggleConfirmed)
  const reset = useValidationDraft((state) => state.reset)

  // ---- 条件存放位置（localStorage 偏好，默认跟环境走） ----
  const { source, environment, explicit, setSource } = useValidationSource()

  const [viewMode, setViewMode] = useState<ViewMode>('conditions')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [confirmPull, setConfirmPull] = useState(false)
  const [saving, setSaving] = useState(false)
  /** 后端条件的编辑缓冲：`null` = 还没有改动，直接跟随服务端返回值 */
  const [backendDraft, setBackendDraft] = useState<ConditionRow[] | null>(null)
  const [backendDirty, setBackendDirty] = useState(false)

  const pending = useMemo(
    () => draftConditions.filter((item) => !item.confirmed).length,
    [draftConditions],
  )

  const localRows = useMemo<ConditionRow[]>(
    () =>
      draftConditions.map((item) => ({
        key: item.id,
        code: item.code,
        trigger: item.trigger,
        note: item.note,
        confirmed: item.confirmed,
      })),
    [draftConditions],
  )

  // 草稿里没有交易日时按「今天」取——和 `ldst validate` 的默认口径一致。
  const conditionsDate = tradeDate ?? new Date().toISOString().slice(0, 10)

  // 读后端条件：两种来源都取（冲突检测要求「进入页面时同时探测两边」）
  const backend = useAsync<LadderEnvelope<ValidationPayload>>(
    (signal) => api.validation(conditionsDate, signal),
    [conditionsDate],
    { enabled: viewMode === 'conditions' },
  )

  // 写通道是否就绪（QUOT-197）：GET 探针，404 = 未合入/控制面关闭，405 = 路由在
  const probe = useAsync((signal) => api.control.validationWriteProbe(signal), [], {
    enabled: viewMode === 'conditions',
  })
  const writeChannelMissing = probe.status === 'ready' && probe.data?.ready === false

  // 环境徽标的 hover 里才用 `data_dir`（B 条：不占主视觉）
  const health = useAsync((signal) => api.health(signal), [], {
    enabled: viewMode === 'conditions',
  })
  const dataDir = health.data?.data_dir ?? null

  // 后端明确答「这一天没有缓存」：空态，不是错误
  const backendMissing = backend.data?.payload.data_source_status === 'missing'

  const backendRows = useMemo<ConditionRow[]>(
    () =>
      (backend.data?.payload.conditions ?? []).map((item, index) => ({
        key: `backend-${index}-${item.code ?? ''}`,
        code: item.code ?? '',
        trigger: item.trigger ?? '',
        note: item.note ?? '',
        confirmed: null,
      })),
    [backend.data],
  )

  // 服务端结果变化且没有未保存改动时才覆盖缓冲，避免把用户正在编辑的内容冲掉
  useEffect(() => {
    if (backendDirty) return
    setBackendDraft(backend.status === 'ready' ? backendRows : null)
  }, [backendRows, backend.status, backendDirty])

  // 后端写通道没就绪时不假装能存：退回本地，并说明原因
  const effectiveSource: ValidationSource =
    source === 'backend' && writeChannelMissing ? 'local' : source
  const rows = effectiveSource === 'local' ? localRows : (backendDraft ?? backendRows)
  const readOnly = effectiveSource === 'backend' && (backend.status === 'error' || probe.status === 'error')

  // ---- 收盘对账 ----
  const reconcile = useAsync<LadderEnvelope<ValidationPayload>>(
    (signal) => api.validation(conditionsDate, signal),
    [conditionsDate],
    { enabled: viewMode === 'reconcile' },
  )
  const reconcileView = useMemo(
    () => (reconcile.data ? parseReconcile(reconcile.data.payload.reconcile) : null),
    [reconcile.data],
  )

  const derived = useMemo<PageState>(() => {
    if (viewMode === 'reconcile') {
      if (reconcile.status === 'loading') return 'loading'
      if (reconcile.status === 'error') {
        return isApiError(reconcile.error) && reconcile.error.kind === 'not-connected'
          ? 'offline'
          : 'error'
      }
      if (!reconcileView) return 'empty'
      return reconcileView.status && reconcileView.status !== 'ok' ? 'partial' : 'normal'
    }

    if (effectiveSource === 'local') {
      // 本地草稿不依赖服务：有内容就是 normal，没有就是 empty
      return localRows.length === 0 ? 'empty' : 'normal'
    }

    if (backend.status === 'loading') return 'loading'
    if (backend.status === 'error') {
      return isApiError(backend.error) && backend.error.kind === 'not-connected' ? 'offline' : 'error'
    }
    if (!backend.data) return 'error'
    if (backendMissing) return 'empty'
    if (backend.data.source_status && backend.data.source_status !== 'ok') return 'partial'
    return backendRows.length === 0 ? 'empty' : 'normal'
  }, [
    viewMode,
    reconcile.status,
    reconcile.error,
    reconcileView,
    effectiveSource,
    localRows.length,
    backend.status,
    backend.error,
    backend.data,
    backendMissing,
    backendRows.length,
  ])

  const state = resolveState(forced, derived)

  // ---- 冲突（两边都有数据且不同）：只提示，不自动合并 ----
  const conflict =
    viewMode === 'conditions' &&
    backend.status === 'ready' &&
    !backendMissing &&
    localRows.length > 0 &&
    backendRows.length > 0 &&
    signatureOf(localRows) !== signatureOf(backendRows)

  // ---- 动作 ----
  const handleAdd = () => {
    if (effectiveSource === 'local') {
      const id = addCondition()
      setExpanded(id)
      toasts.push('已新增一条条件，写入本机草稿', 'ok')
      return
    }
    const key = `new-${Date.now()}`
    setBackendDraft((draft) => [
      ...(draft ?? backendRows),
      { key, code: '', trigger: '', note: '', confirmed: null },
    ])
    setBackendDirty(true)
    setExpanded(key)
    toasts.push('已新增一条条件（还没保存到后端）', 'info')
  }

  const updateRow = (row: ConditionRow, patch: Partial<Pick<ConditionRow, 'code' | 'trigger' | 'note'>>) => {
    if (effectiveSource === 'local') {
      updateCondition(row.key, patch)
      return
    }
    setBackendDraft((draft) => (draft ?? backendRows).map((item) => (item.key === row.key ? { ...item, ...patch } : item)))
    setBackendDirty(true)
  }

  const deleteRow = (row: ConditionRow) => {
    if (effectiveSource === 'local') {
      removeCondition(row.key)
      toasts.push('已删除该条条件', 'info')
    } else {
      setBackendDraft((draft) => (draft ?? backendRows).filter((item) => item.key !== row.key))
      setBackendDirty(true)
      toasts.push('已删除该条条件（还没保存到后端）', 'info')
    }
    if (expanded === row.key) setExpanded(null)
  }

  const handleReset = () => {
    reset()
    setConfirmReset(false)
    setExpanded(null)
    toasts.push('本机草稿已清空', 'ok')
  }

  /** 空触发条件服务端会 400：先在本地拦下，避免把用户丢进一个红色错误里 */
  const invalidRows = (items: ConditionRow[]) =>
    items.filter((item) => !item.trigger.trim()).length

  const toWriteCondition = (row: ConditionRow): ValidationConditionWrite => ({
    code: row.code.trim() || null,
    trigger: row.trigger.trim(),
    note: row.note.trim() || null,
  })

  const pushToBackend = async (items: ConditionRow[], mode: 'merge' | 'replace', successText: string) => {
    setSaving(true)
    try {
      const saved = await api.control.writeValidationConditions({
        trade_date: conditionsDate,
        conditions: items.map(toWriteCondition),
        mode,
      })
      setBackendDirty(false)
      backend.reload()
      toasts.push(`${successText} ${formatInt(saved.saved)} 条 · 原文件已备份为 .bak`, 'ok')
    } catch (error) {
      const described = describeApiError(error)
      toasts.push(`${described.title}：${described.detail}`, 'danger')
      if (isApiError(error) && error.kind === 'http' && error.status === 404) probe.reload()
    } finally {
      setSaving(false)
    }
  }

  const handleSaveBackend = () => {
    const bad = invalidRows(rows)
    if (bad > 0) {
      toasts.push(`有 ${bad} 条缺少触发条件，后端不接受空条件`, 'danger')
      return
    }
    void pushToBackend(rows, 'replace', '已保存到后端 validation 缓存')
  }

  const handlePushLocal = () => {
    const bad = invalidRows(localRows)
    if (bad > 0) {
      toasts.push(`本地有 ${bad} 条缺少触发条件，无法推到后端`, 'danger')
      return
    }
    void pushToBackend(localRows, 'replace', '已用本地草稿覆盖后端')
  }

  const handlePullBackend = () => {
    reset()
    for (const row of backendRows) {
      addCondition({ code: row.code, trigger: row.trigger, note: row.note })
    }
    setConfirmPull(false)
    toasts.push(`已把后端 ${formatInt(backendRows.length)} 条条件覆盖到本地草稿`, 'ok')
  }

  const beforeGate = new Date().getHours() < 15

  // ---- 标题 ----
  const subtitle =
    viewMode === 'reconcile'
      ? state === 'offline'
        ? '本地服务未连接（草稿不依赖行情服务，仍可编辑）'
        : state === 'error'
          ? '对账结果读取失败'
          : `交易日 ${conditionsDate} · 命中率见下方对账卡`
      : effectiveSource === 'local'
        ? `交易日 ${conditionsDate} · 本地草稿 ${formatInt(localRows.length)} 条 · 待确认 ${formatInt(pending)} 条${
            updatedAt ? ` · 最后写入 ${formatTime(updatedAt)}` : ''
          }`
        : state === 'offline'
          ? `本地服务未连接 · 交易日 ${conditionsDate}`
          : state === 'error'
            ? `后端条件读取失败 · 交易日 ${conditionsDate}`
            : `交易日 ${conditionsDate} · 后端 ${formatInt(backendRows.length)} 条${
                backend.data ? ` · 读取于 ${formatTime(backend.data.fetched_at)}` : ''
              }`

  const header = (
    <PageHeader
      title="明日验证"
      subtitle={subtitle}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="primary"
            onClick={handleAdd}
            disabled={readOnly}
            disabledReason={readOnly ? '离线时后端条件只读' : undefined}
            leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
          >
            新增条件
          </Button>
          {effectiveSource === 'local' ? (
            <Button
              variant="secondary"
              onClick={() => setConfirmReset(true)}
              disabled={localRows.length === 0}
              disabledReason={localRows.length === 0 ? '还没有条件可清空' : undefined}
              leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
            >
              清空草稿
            </Button>
          ) : (
            <Button
              variant="secondary"
              onClick={handleSaveBackend}
              loading={saving}
              disabled={!backendDirty || readOnly}
              disabledReason={
                readOnly ? '离线时无法写入后端' : !backendDirty ? '没有未保存的改动' : undefined
              }
              leadingIcon={<Save className="size-3.5" aria-hidden="true" />}
            >
              保存到后端
            </Button>
          )}
        </>
      }
    />
  )

  // ---- 环境徽标（B 条） ----
  const hostname = typeof window === 'undefined' ? '' : window.location.hostname
  const envBadge = (
    <Badge
      tone={environment === 'local' ? 'neutral' : 'info'}
      title={
        environment === 'local'
          ? `当前通过 ${hostname} 访问：条件草稿只存在这台机器的浏览器里。后端数据目录：${
              dataDir ?? '未取到（GET /api/health）'
            }。默认模式：本地。`
          : `当前通过 ${hostname} 访问：条件写进服务器的 validation 缓存，同一服务器的浏览器共享同一份。后端数据目录：${
              dataDir ?? '未取到（GET /api/health）'
            }。默认模式：后端。`
      }
    >
      {environment === 'local' ? '本机 · 数据只在你这台机器上' : '服务器 · 条件是共享的'}
    </Badge>
  )

  const toolbar = (
    <div className="flex flex-wrap items-center gap-3">
      <Segmented options={VIEW_OPTIONS} value={viewMode} onChange={setViewMode} ariaLabel="验证模式" />
      <span className="h-5 w-px bg-border" aria-hidden="true" />
      <span className="text-caption text-text-secondary">
        交易日 <span className="ldst-num text-text">{conditionsDate}</span>
      </span>
      {viewMode === 'conditions' && (
        <>
          <span className="h-5 w-px bg-border" aria-hidden="true" />
          <span className="text-caption text-text-secondary">条件存放</span>
          <Segmented
            options={[
              { value: 'local' as const, label: '本地' },
              { value: 'backend' as const, label: '后端', disabled: writeChannelMissing },
            ]}
            value={effectiveSource}
            onChange={(next) => {
              setSource(next)
              setExpanded(null)
            }}
            ariaLabel="条件存放位置"
            size="sm"
          />
          {writeChannelMissing && (
            <Badge tone="warn" title={probe.data?.detail}>
              后端写通道未就绪
            </Badge>
          )}
          {envBadge}
        </>
      )}
      {viewMode === 'conditions' && effectiveSource === 'local' ? (
        <>
          <Badge tone={pending > 0 ? 'warn' : 'ok'}>待确认 {formatInt(pending)}</Badge>
          {updatedAt && <span className="text-caption text-text-muted">最后写入 {formatTime(updatedAt)}</span>}
        </>
      ) : null}
      {viewMode === 'conditions' && effectiveSource === 'backend' && (
        <>
          <Badge tone={backendDirty ? 'warn' : 'ok'}>
            {backendDirty ? '有未保存改动' : `后端 ${formatInt(backendRows.length)} 条`}
          </Badge>
          {backend.data && (
            <span className="text-caption text-text-muted">读取于 {formatTime(backend.data.fetched_at)}</span>
          )}
        </>
      )}
      {viewMode === 'conditions' && !explicit && (
        <span className="text-caption text-text-muted">
          默认按{environment === 'local' ? '本机' : '服务器'}环境选「{source === 'local' ? '本地' : '后端'}」
        </span>
      )}
    </div>
  )

  // ---- 条件表 ----
  const renderTable = (items: ConditionRow[], sourceOfRows: ValidationSource, locked: boolean) => {
    const isLocal = sourceOfRows === 'local'
    return (
      <SectionCard
        title={isLocal ? '条件草稿' : '后端条件'}
        subtitle={
          isLocal
            ? '每条都是一次可复核的承诺：代码、触发、失效、仓位写清楚，次日收盘才能对账'
            : '这一份存在后端 validation 缓存里；改动先落在本页缓冲，点「保存到后端」才落盘'
        }
        actions={<Badge tone="neutral">{formatInt(items.length)} 条</Badge>}
      >
        <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 280px)' }}>
          <div className="min-w-[960px]">
            <div
              className={`sticky top-0 z-sticky grid h-control ${ROW_GRID} items-center gap-3 border-b border-border bg-surface-2 px-3 text-caption font-medium text-text-secondary`}
            >
              <span>序号</span>
              <span>代码 · 名称</span>
              <span>触发条件</span>
              <span>失效条件 · 备注</span>
              <span>仓位上限</span>
              <span>确认状态</span>
              <span className="text-right">操作</span>
            </div>

            {items.map((row, index) => {
              const fieldId = `condition-${row.key}`
              const isOpen = expanded === row.key
              return (
                <div key={row.key} className="border-b border-border last:border-b-0">
                  <div
                    className={`relative grid min-h-row ${ROW_GRID} items-center gap-3 px-3 py-2 transition-colors duration-fast ease-ldst hover:bg-surface-3`}
                    aria-describedby={row.confirmed === false ? `${fieldId}-pending` : undefined}
                  >
                    <span
                      aria-hidden="true"
                      className={`absolute inset-y-0 left-0 w-status-bar ${
                        row.confirmed === null ? 'bg-border-strong' : row.confirmed ? 'bg-ok' : 'bg-warn'
                      }`}
                    />
                    <span className="ldst-num text-caption text-text-muted">{index + 1}</span>
                    <span className="min-w-0 truncate text-body text-text" title={row.code || '未填代码'}>
                      {row.code ? (
                        <span className="ldst-num">{row.code}</span>
                      ) : (
                        <span className="text-text-muted">未填代码</span>
                      )}
                    </span>
                    <span className="min-w-0 truncate text-body text-text-secondary" title={row.trigger || undefined}>
                      {row.trigger || MISSING}
                    </span>
                    <span className="min-w-0 truncate text-body text-text-secondary" title={row.note || undefined}>
                      {row.note || MISSING}
                    </span>
                    <span
                      className="text-body text-text-muted"
                      title="草稿结构（stores/validationDraft.ts）不记录仓位上限，用备注承载"
                    >
                      {MISSING}
                    </span>
                    <span>
                      {row.confirmed === null ? (
                        <span className="text-caption text-text-muted" title="后端条件不记录确认状态">
                          {MISSING}
                        </span>
                      ) : row.confirmed ? (
                        <Badge tone="ok">已确认</Badge>
                      ) : (
                        <Badge tone="warn">
                          <span id={`${fieldId}-pending`}>待确认</span>
                        </Badge>
                      )}
                    </span>
                    <span className="flex items-center justify-end gap-1">
                      <IconButton
                        label={isOpen ? '收起编辑' : '编辑该条'}
                        aria-expanded={isOpen}
                        aria-controls={`${fieldId}-editor`}
                        disabled={locked}
                        onClick={() => setExpanded(isOpen ? null : row.key)}
                        icon={<ClipboardCheck className="size-4" aria-hidden="true" />}
                      />
                      {isLocal && (
                        <IconButton
                          label={row.confirmed ? '取消确认' : '标记确认'}
                          aria-pressed={row.confirmed === true}
                          disabled={locked}
                          onClick={() => toggleConfirmed(row.key)}
                          icon={
                            row.confirmed ? (
                              <X className="size-4" aria-hidden="true" />
                            ) : (
                              <Check className="size-4" aria-hidden="true" />
                            )
                          }
                        />
                      )}
                      <IconButton
                        label="删除该条条件"
                        disabled={locked}
                        onClick={() => deleteRow(row)}
                        icon={<Trash2 className="size-4" aria-hidden="true" />}
                      />
                    </span>
                  </div>

                  {isOpen && (
                    <div
                      id={`${fieldId}-editor`}
                      className="grid gap-4 border-t border-border bg-sunken p-4 desk:grid-cols-12"
                    >
                      <Field label="代码 / 标的" htmlFor={`${fieldId}-code`} className="desk:col-span-3" hint="六位代码或简称，可留空（纯情绪条件）。">
                        <Input
                          id={`${fieldId}-code`}
                          type="text"
                          value={row.code}
                          placeholder="如 000001"
                          disabled={locked}
                          onChange={(event) => updateRow(row, { code: event.target.value })}
                        />
                      </Field>
                      <Field label="触发条件" htmlFor={`${fieldId}-trigger`} className="desk:col-span-3" hint="价格 / 量比 / 时间窗口；后端不接受空条件。">
                        <Input
                          id={`${fieldId}-trigger`}
                          type="text"
                          value={row.trigger}
                          placeholder="如 竞价高开 3% 以上且量比 > 2"
                          disabled={locked}
                          invalid={!row.trigger.trim()}
                          onChange={(event) => updateRow(row, { trigger: event.target.value })}
                        />
                      </Field>
                      <Field
                        label="失效条件 · 备注"
                        htmlFor={`${fieldId}-note`}
                        className="desk:col-span-6"
                        hint={
                          isLocal
                            ? '失效条件、仓位上限、备选标的…草稿结构只有这一个自由文本字段。'
                            : '后端 GET 不回备注（server.py:_validation_conditions 只投影 code / trigger），保存后读回看不到，但会写进文件。'
                        }
                      >
                        <Textarea
                          id={`${fieldId}-note`}
                          value={row.note}
                          placeholder="如 跌破昨收 3% 即失效；仓位不超过 2 成"
                          disabled={locked}
                          onChange={(event) => updateRow(row, { note: event.target.value })}
                        />
                      </Field>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <p className="mt-3 text-caption text-text-muted">
          「仓位上限」列显示 {MISSING}：两种来源的结构都不记录该字段（本轮不改 store），需要时写进「失效条件 · 备注」。
          {isLocal
            ? '确认只代表你的判断已定，不代表行情验证通过。'
            : '后端条件没有确认概念，那一列留空；备注写入后由后端读取接口省略，这是后端投影的现状，不是页面漏显示。'}
        </p>
      </SectionCard>
    )
  }

  const conflictBanner = conflict ? (
    <Banner
      tone="warn"
      title={`两边都有条件且不一样：后端 ${formatInt(backendRows.length)} 条，本地 ${formatInt(localRows.length)} 条${
        updatedAt ? `（本地改动于 ${formatTime(updatedAt)}）` : ''
      }`}
      action={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            variant="secondary"
            size="md"
            onClick={handlePushLocal}
            loading={saving}
            disabled={invalidRows(localRows) > 0}
            disabledReason={
              invalidRows(localRows) > 0 ? `本地有 ${invalidRows(localRows)} 条缺少触发条件` : undefined
            }
          >
            用本地覆盖后端
          </Button>
          <Button variant="danger" size="md" onClick={() => setConfirmPull(true)} disabled={saving}>
            拉取后端覆盖本地
          </Button>
        </div>
      }
    >
      本屏不做自动合并：两边各自都是你写下的东西，谁赢必须你当场定。覆盖前请确认哪一边更新。
    </Banner>
  ) : null

  // ---- 条件区主体 ----
  const renderConditions = (): ReactNode => {
    if (state === 'loading') return <ScreenSkeleton metrics={3} rows={5} />

    if (state === 'error') {
      const described = describeApiError(backend.error)
      return (
        <ErrorBlock
          title={described.title}
          detail={`${described.detail} 条件来自本机只读 API，页面不会自己编一份出来。`}
          code={described.code}
          onRetry={backend.reload}
          retryLabel="重试读取"
        />
      )
    }

    if (state === 'offline') {
      return (
        <div className="flex flex-col gap-4">
          <OfflineBanner onRetry={backend.reload} />
          <Banner
            tone="info"
            title={`离线 · 展示本机草稿（只读）${updatedAt ? ` · 最后改动 ${formatTime(updatedAt)}` : ''}`}
          >
            {localRows.length > 0
              ? `本机 IndexedDB 里还有 ${formatInt(localRows.length)} 条条件，连上本地服务后可以切回「后端」继续读写。`
              : '本机 IndexedDB 里也没有条件；连上本地服务后点「重试」再取后端条件。'}
          </Banner>
          {localRows.length > 0 && renderTable(localRows, 'local', true)}
        </div>
      )
    }

    if (state === 'empty') {
      if (effectiveSource === 'backend') {
        return (
          <EmptyState
            icon={<CircleSlash className="size-8" aria-hidden="true" />}
            title={backendMissing ? '后端还没有这一天的条件' : '后端条件为空'}
            description={
              backendMissing
                ? `${backend.data?.source_detail ?? '后端 validation 缓存不存在'}。可以先在这里写下条件并「保存到后端」，或在「本地」模式里写草稿。`
                : '这一天有缓存文件，但里面没有条件。写一条并保存，次日收盘才能对账。'
            }
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button
                  variant="primary"
                  onClick={handleAdd}
                  leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
                >
                  新增第一条
                </Button>
                {localRows.length > 0 && (
                  <Button variant="secondary" onClick={handlePushLocal} loading={saving}>
                    把本地 {formatInt(localRows.length)} 条推到后端
                  </Button>
                )}
              </div>
            }
          />
        )
      }
      return (
        <EmptyState
          icon={<CircleSlash className="size-8" aria-hidden="true" />}
          title="还没有验证条件"
          description="写下明天要盯的代码与触发条件，写法随意，先记下来最要紧。没有条件时，本屏不会显示任何示例。"
          action={
            <Button
              variant="primary"
              onClick={handleAdd}
              leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
            >
              新增第一条
            </Button>
          }
        />
      )
    }

    return (
      <div className="flex flex-col gap-4">
        {state === 'partial' && effectiveSource === 'backend' && (
          <Banner tone="warn" title="部分数据缺失 · 后端缓存不完整">
            {backend.data?.source_detail ?? '后端这一天的 validation 缓存状态是 partial：条件可能不全。'}
            缓存里的条件照常展示；缺的部分不会补造。
          </Banner>
        )}
        {conflictBanner}
        {renderTable(rows, effectiveSource, readOnly)}
      </div>
    )
  }

  // ---- 对账区（保持原有口径） ----
  const renderReconcile = (): ReactNode => {
    if (reconcile.status === 'loading') return <ScreenSkeleton metrics={3} rows={5} />

    if (reconcile.status === 'error') {
      const described = describeApiError(reconcile.error)
      return (
        <ErrorBlock
          title={described.title}
          detail={`${described.detail} 对账结果来自本机只读 API，不会自行推断结论。`}
          code={described.code}
          onRetry={reconcile.reload}
        />
      )
    }

    if (!reconcileView) {
      return (
        <EmptyState
          icon={<CircleSlash className="size-8" aria-hidden="true" />}
          title="这个交易日还没有对账结果"
          description={
            beforeGate
              ? '15:00 后可对账；现在可以先写明日条件，收盘任务跑完会自动生成逐条判定。'
              : '收盘任务跑完后才会有结论；本屏不自行判定，也不补算历史。'
          }
          action={
            <Button variant="secondary" onClick={() => setViewMode('conditions')}>
              去写明日条件
            </Button>
          }
        />
      )
    }

    const total = reconcileView.checked ?? reconcileView.results.length
    const hit = reconcileView.passed ?? reconcileView.results.filter((item) => item.verdict === 'hit').length
    const rowsOfReconcile = reconcileView.results

    return (
      <div className="flex flex-col gap-4">
        {state === 'partial' && (
          <Banner tone="warn" title="部分数据缺失">
            对账结论里有无法判定的条目（行情缺数据），结论仅供参考；缺失的条目判为「数据缺失」而不是「未命中」。
          </Banner>
        )}

        {rowsOfReconcile.length === 0 && reconcileView.reason && (
          <Banner tone="warn" title="本次没有生成逐条判定">
            {reconcileView.reason}
          </Banner>
        )}

        <SectionCard
          title="收盘对账"
          subtitle={
            <>
              命中 <span className="ldst-num text-text">{formatInt(hit)}</span>/
              <span className="ldst-num text-text">{formatInt(total)}</span>
              {reconcileView.nextSession ? ` · 下一交易日 ${reconcileView.nextSession}` : ''}
            </>
          }
          actions={
            <>
              {reconcileView.status && (
                <Badge tone={reconcileView.status === 'ok' ? 'ok' : 'warn'}>
                  {reconcileView.status === 'ok' ? '对账完成' : '部分缺失'}
                </Badge>
              )}
              <Button
                variant="secondary"
                disabled
                disabledReason={
                  beforeGate
                    ? '15:00 后可对账'
                    : '对账由收盘任务写入，本轮只读；人工复核待 ldst validate 接入'
                }
              >
                人工复核
              </Button>
            </>
          }
        >
          <DataTable<ReconcileResult>
            rows={rowsOfReconcile}
            rowKey={(row) => row.key}
            density="compact"
            caption="逐条判定只读；口径：通过 = 实际值满足条件，未触发 = 条件没被触发，数据缺失 = 当日指标不可用。"
            empty={
              <span className="text-body text-text-secondary">
                没有逐条判定——{reconcileView.reason ?? '当日没有可对账的条件'}。
              </span>
            }
            columns={[
              {
                key: 'verdict',
                header: '判定',
                width: '88px',
                render: (row) => (
                  <Badge tone={VERDICT[row.verdict].tone}>{VERDICT[row.verdict].label}</Badge>
                ),
              },
              {
                key: 'text',
                header: '条件',
                render: (row) => (
                  <span className="block min-w-0 truncate text-body text-text" title={row.text}>
                    {row.text}
                  </span>
                ),
              },
              {
                key: 'metric',
                header: '指标',
                width: '112px',
                render: (row) => <span className="text-caption text-text-secondary">{row.metric ?? MISSING}</span>,
              },
              {
                key: 'target',
                header: '目标',
                width: '88px',
                align: 'right',
                render: (row) => (
                  <span className="ldst-num text-body text-text-secondary">
                    {row.operator ? `${row.operator} ` : ''}
                    {formatMetricValue(row.target)}
                  </span>
                ),
              },
              {
                key: 'actual',
                header: '实际值',
                width: '88px',
                align: 'right',
                render: (row) => (
                  <span className="ldst-num text-body text-text">{formatMetricValue(row.actual)}</span>
                ),
              },
            ]}
          />
        </SectionCard>

        <p className="text-caption text-text-muted">
          对账 {formatInt(reconcileView.checked)} 条 · 通过 {formatInt(reconcileView.passed)} · 未通过{' '}
          {formatInt(reconcileView.failed)} · 无法判定 {formatInt(reconcileView.unknown)}。本屏不做收益预测，
          也不提供自动交易。
        </p>
      </div>
    )
  }

  const privacyBanner =
    viewMode === 'conditions' && state !== 'loading' && state !== 'error' && state !== 'offline' ? (
      effectiveSource === 'local' ? (
        <Banner tone="info" title="存在这台机器的浏览器里">
          条件草稿写进这台机器的浏览器（IndexedDB），换台电脑、清掉站点数据就没了；不上传、不进 AI、不被 MCP
          读取。提交对账由 <code>ldst validate</code> 负责。
        </Banner>
      ) : (
        <Banner tone="info" title="存在本机数据目录的 validation 缓存里">
          条件通过本机控制面接口写进数据目录下的 <code>state/validation/{conditionsDate}.json</code>
          （写前备份 .bak，只改 conditions 字段，对账结果 reconcile 原样保留）；同一台机器上的浏览器读到的是同一份。
        </Banner>
      )
    ) : null

  const fallbackBanner =
    source === 'backend' && writeChannelMissing ? (
      <Banner tone="warn" title="后端写通道未就绪 · 已临时按「本地」展示">
        {probe.data?.detail ?? '控制面没有暴露保存条件的端点'}。前端不会拿一个只能看不能存的开关糊弄你；等
        QUOT-197 上线后这个开关会自动恢复。
      </Banner>
    ) : null

  return (
    <div className="flex flex-col gap-5">
      {header}
      {toolbar}
      {fallbackBanner}
      {privacyBanner}
      {viewMode === 'conditions' ? renderConditions() : renderReconcile()}

      <ConfirmDialog
        open={confirmReset}
        title="清空本机验证草稿？"
        description="所有条件、失效条件与备注会一并清除，该操作不可撤销；只影响本机 IndexedDB，不动后端 validation 缓存。"
        confirmLabel="清空草稿"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={handleReset}
      />

      <ConfirmDialog
        open={confirmPull}
        title="用后端条件覆盖本地草稿？"
        description={`本地这 ${formatInt(
          localRows.length,
        )} 条会被后端这 ${formatInt(backendRows.length)} 条整段替换，你还没保存到后端的本地改动会丢失。该操作不可撤销。`}
        confirmLabel="覆盖本地"
        tone="danger"
        onCancel={() => setConfirmPull(false)}
        onConfirm={handlePullBackend}
      />

      <Toasts items={toasts.items} onDismiss={toasts.dismiss} />
    </div>
  )
}
