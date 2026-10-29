import { useMemo, useState, type ReactNode } from 'react'
import { Check, CircleSlash, ClipboardCheck, Plus, Trash2, X } from 'lucide-react'

import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatInt, formatTime } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import { api, describeApiError, isApiError, type LadderEnvelope, type ValidationPayload } from '../api'
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

/**
 * 明日验证（P-06 / DESIGN-SPEC §4.7 · UI-SPEC §3.3）。
 *
 * L0 用**分段控件**在「明日条件」与「收盘对账」之间切换——两种模式是互斥视图，
 * 不是并列的页签，因此不用 `Tabs`。
 *
 * - 明日条件：草稿全部写本机 IndexedDB（`validation_draft`），不上传、不进 AI。
 * - 收盘对账：只读消费 `GET /api/validation`，逐条判定命中 / 未命中 / 未触发 / 数据缺失；
 *   命中率放标题栏摘要（「命中 4/5」），不做成大数字卡（§4.7 明文要求）。
 *
 * 仓位上限：`stores/validationDraft.ts` 的草稿结构里没有该字段（本轮不改 store），
 * 因此列上一律 `—` 并在表下说明「用备注承载」，不编造数字。
 */

type Mode = 'conditions' | 'reconcile'

const MODE_OPTIONS = [
  { value: 'conditions' as const, label: '明日条件' },
  { value: 'reconcile' as const, label: '收盘对账' },
]

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

export default function ValidateScreen() {
  const forced = useForcedState()
  const toasts = useToasts()

  const tradeDate = useValidationDraft((state) => state.tradeDate)
  const conditions = useValidationDraft((state) => state.conditions)
  const updatedAt = useValidationDraft((state) => state.updatedAt)
  const addCondition = useValidationDraft((state) => state.addCondition)
  const updateCondition = useValidationDraft((state) => state.updateCondition)
  const removeCondition = useValidationDraft((state) => state.removeCondition)
  const toggleConfirmed = useValidationDraft((state) => state.toggleConfirmed)
  const reset = useValidationDraft((state) => state.reset)

  const [mode, setMode] = useState<Mode>('conditions')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)

  const pending = useMemo(
    () => conditions.filter((item) => !item.confirmed).length,
    [conditions],
  )

  // 草稿里没有交易日时，对账按「今天」取——和 `ldst validate` 的默认口径一致。
  const reconcileDate = tradeDate ?? new Date().toISOString().slice(0, 10)
  const reconcile = useAsync<LadderEnvelope<ValidationPayload>>(
    (signal) => api.validation(reconcileDate, signal),
    [reconcileDate],
    { enabled: mode === 'reconcile' },
  )
  const reconcileView = useMemo(
    () => (reconcile.data ? parseReconcile(reconcile.data.payload.reconcile) : null),
    [reconcile.data],
  )

  const derived = useMemo<PageState>(() => {
    if (mode === 'conditions') return conditions.length === 0 ? 'empty' : 'normal'
    if (reconcile.status === 'loading') return 'loading'
    if (reconcile.status === 'error') {
      return isApiError(reconcile.error) && reconcile.error.kind === 'not-connected' ? 'offline' : 'error'
    }
    // 对账历史与草稿无关：即便本机没有草稿，也要把已有的对账结果摆在台面上。
    return reconcileView ? 'normal' : 'empty'
  }, [mode, conditions.length, reconcile.status, reconcile.error, reconcileView])

  const state = resolveState(forced, derived)

  const handleAdd = () => {
    const id = addCondition()
    setExpanded(id)
    setMode('conditions')
    toasts.push('已新增一条条件，写入本机草稿', 'ok')
  }

  const handleReset = () => {
    reset()
    setConfirmReset(false)
    setExpanded(null)
    toasts.push('本机草稿已清空', 'ok')
  }

  const beforeGate = new Date().getHours() < 15

  const header = (
    <PageHeader
      title="明日验证"
      subtitle={
        state === 'offline'
          ? '本地服务未连接（草稿不依赖行情服务，仍可编辑）'
          : state === 'error'
            ? '对账结果读取失败'
            : `交易日 ${tradeDate ?? reconcileDate} · 共 ${formatInt(conditions.length)} 条条件 · 待确认 ${formatInt(pending)} 条${
                updatedAt ? ` · 最后写入 ${formatTime(updatedAt)}` : ''
              }`
      }
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="primary"
            onClick={handleAdd}
            leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
          >
            新增条件
          </Button>
          <Button
            variant="secondary"
            onClick={() => setConfirmReset(true)}
            disabled={conditions.length === 0}
            disabledReason={conditions.length === 0 ? '还没有条件可清空' : undefined}
            leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
          >
            清空草稿
          </Button>
        </>
      }
    />
  )

  const toolbar = (
    <div className="flex flex-wrap items-center gap-3">
      <Segmented
        options={MODE_OPTIONS}
        value={mode}
        onChange={setMode}
        ariaLabel="验证模式"
      />
      <span className="h-5 w-px bg-border" aria-hidden="true" />
      <span className="text-caption text-text-secondary">
        交易日 <span className="ldst-num text-text">{tradeDate ?? reconcileDate}</span>
      </span>
      <Badge tone={pending > 0 ? 'warn' : 'ok'}>待确认 {formatInt(pending)}</Badge>
      {updatedAt && (
        <span className="text-caption text-text-muted">最后写入 {formatTime(updatedAt)}</span>
      )}
    </div>
  )

  const renderConditions = () => {
    if (state === 'empty') {
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
      <SectionCard
        title="条件草稿"
        subtitle="每条都是一次可复核的承诺：代码、触发、失效、仓位写清楚，次日收盘才能对账"
        actions={<Badge tone="neutral">{formatInt(conditions.length)} 条</Badge>}
      >
        <div className="overflow-x-auto">
          <div className="min-w-[960px]">
            <div className="grid h-control grid-cols-[2.5rem_minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,1.5fr)_5.5rem_5.5rem_6.5rem] items-center gap-3 border-b border-border px-3 text-caption text-text-secondary">
              <span>序号</span>
              <span>代码 · 名称</span>
              <span>触发条件</span>
              <span>失效条件</span>
              <span>仓位上限</span>
              <span>确认状态</span>
              <span className="text-right">操作</span>
            </div>

            {conditions.map((condition, index) => {
              const fieldId = `condition-${condition.id}`
              const isOpen = expanded === condition.id
              return (
                <div key={condition.id} className="border-b border-border last:border-b-0">
                  <div
                    className="relative grid min-h-row grid-cols-[2.5rem_minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,1.5fr)_5.5rem_5.5rem_6.5rem] items-center gap-3 px-3 py-2"
                    aria-describedby={!condition.confirmed ? `${fieldId}-pending` : undefined}
                  >
                    <span
                      aria-hidden="true"
                      className={`absolute inset-y-0 left-0 w-[3px] ${condition.confirmed ? 'bg-ok' : 'bg-warn'}`}
                    />
                    <span className="ldst-num text-caption text-text-muted">{index + 1}</span>
                    <span className="truncate text-body text-text">
                      {condition.code ? (
                        <span className="ldst-num">{condition.code}</span>
                      ) : (
                        <span className="text-text-muted">未填代码</span>
                      )}
                    </span>
                    <span className="truncate text-body text-text-secondary" title={condition.trigger || undefined}>
                      {condition.trigger || MISSING}
                    </span>
                    <span className="truncate text-body text-text-secondary" title={condition.note || undefined}>
                      {condition.note || MISSING}
                    </span>
                    <span
                      className="text-body text-text-muted"
                      title="草稿结构（stores/validationDraft.ts）不记录仓位上限，用备注承载"
                    >
                      {MISSING}
                    </span>
                    <span>
                      {condition.confirmed ? (
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
                        onClick={() => setExpanded(isOpen ? null : condition.id)}
                        icon={<ClipboardCheck className="size-4" aria-hidden="true" />}
                      />
                      <IconButton
                        label={condition.confirmed ? '取消确认' : '标记确认'}
                        aria-pressed={condition.confirmed}
                        onClick={() => toggleConfirmed(condition.id)}
                        icon={
                          condition.confirmed ? (
                            <X className="size-4" aria-hidden="true" />
                          ) : (
                            <Check className="size-4" aria-hidden="true" />
                          )
                        }
                      />
                      <IconButton
                        label="删除该条条件"
                        onClick={() => {
                          removeCondition(condition.id)
                          if (expanded === condition.id) setExpanded(null)
                          toasts.push('已删除该条条件', 'info')
                        }}
                        icon={<Trash2 className="size-4" aria-hidden="true" />}
                      />
                    </span>
                  </div>

                  {isOpen && (
                    <div id={`${fieldId}-editor`} className="grid gap-4 border-t border-border bg-sunken p-4 min-[1100px]:grid-cols-[1fr_1fr_2fr]">
                      <Field label="代码 / 标的" htmlFor={`${fieldId}-code`} hint="六位代码或简称。">
                        <Input
                          id={`${fieldId}-code`}
                          type="text"
                          value={condition.code}
                          placeholder="如 000001"
                          onChange={(event) => updateCondition(condition.id, { code: event.target.value })}
                        />
                      </Field>
                      <Field label="触发条件" htmlFor={`${fieldId}-trigger`} hint="价格 / 量比 / 时间窗口。">
                        <Input
                          id={`${fieldId}-trigger`}
                          type="text"
                          value={condition.trigger}
                          placeholder="如 竞价高开 3% 以上且量比 > 2"
                          onChange={(event) => updateCondition(condition.id, { trigger: event.target.value })}
                        />
                      </Field>
                      <Field
                        label="失效条件 · 备注"
                        htmlFor={`${fieldId}-note`}
                        hint="失效条件、仓位上限、备选标的…草稿结构只有这一个自由文本字段。"
                      >
                        <Textarea
                          id={`${fieldId}-note`}
                          value={condition.note}
                          placeholder="如 跌破昨收 3% 即失效；仓位不超过 2 成"
                          onChange={(event) => updateCondition(condition.id, { note: event.target.value })}
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
          「仓位上限」列显示 {MISSING}：草稿结构不记录该字段（本轮不改 store），需要时写进「失效条件 · 备注」。
          确认只代表你的判断已定，不代表行情验证通过。
        </p>
      </SectionCard>
    )
  }

  const renderReconcile = () => {
    if (reconcile.status === 'loading') {
      return <ScreenSkeleton metrics={3} rows={5} />
    }

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
            <Button variant="secondary" onClick={() => setMode('conditions')}>
              去写明日条件
            </Button>
          }
        />
      )
    }

    const total = reconcileView.checked ?? reconcileView.results.length
    const hit = reconcileView.passed ?? reconcileView.results.filter((item) => item.verdict === 'hit').length

    const rows = reconcileView.results

    return (
      <div className="flex flex-col gap-4">
        {state === 'partial' && (
          <Banner tone="warn" title="部分数据缺失">
            部分数据缺失（涨停池等），结论仅供参考；缺失的条目判为「数据缺失」而不是「未命中」。
          </Banner>
        )}

        {rows.length === 0 && reconcileView.reason && (
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
            rows={rows}
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
                width: '6rem',
                render: (row) => (
                  <Badge tone={VERDICT[row.verdict].tone}>{VERDICT[row.verdict].label}</Badge>
                ),
              },
              {
                key: 'text',
                header: '条件',
                render: (row) => (
                  <span className="block truncate text-body text-text" title={row.text}>
                    {row.text}
                  </span>
                ),
              },
              {
                key: 'metric',
                header: '指标',
                width: '7rem',
                render: (row) => <span className="text-caption text-text-secondary">{row.metric ?? MISSING}</span>,
              },
              {
                key: 'target',
                header: '目标',
                width: '6rem',
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
                width: '6rem',
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

  let body: ReactNode
  if (state === 'loading') {
    body = <ScreenSkeleton metrics={3} rows={4} />
  } else if (state === 'error') {
    const described = reconcile.status === 'error' ? describeApiError(reconcile.error) : null
    body = (
      <ErrorBlock
        title={mode === 'reconcile' ? (described?.title ?? '对账结果读取失败') : '本机草稿库无法读取'}
        detail={
          mode === 'reconcile'
            ? `${described?.detail ?? '本机只读 API 没有返回对账结果'} 对账不会自行推断结论。`
            : 'IndexedDB 可能被浏览器策略禁用或配额已满；条件草稿不会上传到任何地方，重新打开页面即可重试。'
        }
        code={described?.code ?? null}
        onRetry={mode === 'reconcile' ? reconcile.reload : () => toasts.push('仍无法读取：请检查浏览器是否禁用了站点数据存储。', 'danger')}
        retryLabel={mode === 'reconcile' ? '重试' : '重新检查'}
      />
    )
  } else if (mode === 'conditions') {
    body = renderConditions()
  } else {
    body = renderReconcile()
  }

  return (
    <div className="flex flex-col gap-5">
      {header}
      {toolbar}

      {state === 'offline' && mode === 'reconcile' && <OfflineBanner onRetry={reconcile.reload} />}
      {state === 'offline' && mode === 'conditions' && (
        <OfflineBanner
          onRetry={() => toasts.push('草稿只写本机，离线不影响编辑；行情字段需连上本地服务后才有。', 'info')}
        />
      )}
      {state === 'partial' && mode === 'conditions' && (
        <Banner tone="warn" title="部分数据缺失">
          行情源未连接：条件草稿不依赖行情，仍可编辑；但「代码 / 触发条件」对不上行情时，次日无法自动对账。
        </Banner>
      )}

      {mode === 'conditions' && state !== 'loading' && state !== 'error' && (
        <Banner tone="info" title="只写本机">
          条件草稿写入本机 IndexedDB，不上传、不进 AI、不被 MCP 读取；刷新、崩溃、升级后原样回填。
          提交对账由 <code>ldst validate</code> 负责。
        </Banner>
      )}

      {body}

      <ConfirmDialog
        open={confirmReset}
        title="清空本机验证草稿？"
        description="所有条件、失效条件与备注会一并清除，该操作不可撤销；只影响本机 IndexedDB，不动任何后端数据。"
        confirmLabel="清空草稿"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={handleReset}
      />

      <Toasts items={toasts.items} onDismiss={toasts.dismiss} />
    </div>
  )
}
