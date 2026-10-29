import { useEffect, useMemo, useState } from 'react'
import { RefreshCw, Trash2 } from 'lucide-react'

import { api, describeApiError, isApiError } from '../api'
import {
  WATCH_SNAPSHOT_TTL_SEC,
  useWatchSnapshot,
  type SnapshotRow,
} from '../../stores/watchSnapshot'
import {
  describeFreshness,
  evaluateSnapshotFreshness,
  localDateIso,
} from '../../persist/freshness'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatChangePct, formatInt, formatTime } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  SectionCard,
  SignedNumber,
  StatusDot,
  Switch,
  type Column,
} from '../components/ui'

/**
 * 盯盘（P-03 / DESIGN-SPEC §4.4）。
 *
 * 只展示「可信」的快照：超过 TTL、交易日变更、本机时间异常时旧值一律不渲染，
 * 只说明原因并给重取入口 —— 宁可空着，也不让人按过期数据做决策。
 *
 * 阈值属于会话偏好（§3.2 遗留决策点）：本轮只存内存，卡上明示「本次会话有效」，
 * 不写 IndexedDB、不改 `config/state.yaml`。
 */

const DEFAULT_THRESHOLD_PCT = 3.0
const THRESHOLD_MIN = 1.0
const THRESHOLD_MAX = 10.0
const SCAN_WINDOW_LABEL = '3 分钟（固定）'

type LevelTone = 'danger' | 'warn' | 'flat'

const LEVEL_BAR: Record<LevelTone, string> = {
  danger: 'bg-danger',
  warn: 'bg-warn',
  flat: 'bg-border-strong',
}

/**
 * 级别只由「触发值 ÷ 阈值」的倍数决定（规则透明、可复核）：
 * ≥2 倍 → 强信号，≥1 倍 → 关注，否则 → 提示。阈值取自本页设置。
 */
function levelOf(row: SnapshotRow, thresholdPct: number): { label: string; tone: LevelTone } {
  if (row.changePct === null || thresholdPct <= 0) return { label: MISSING, tone: 'flat' }
  const ratio = Math.abs(row.changePct) / thresholdPct
  if (ratio >= 2) return { label: '强信号', tone: 'danger' }
  if (ratio >= 1) return { label: '关注', tone: 'warn' }
  return { label: '提示', tone: 'flat' }
}

function buildColumns(thresholdPct: number): Array<Column<SnapshotRow>> {
  return [
    {
      key: 'level',
      header: '级别',
      width: '96px',
      render: (row) => {
        const level = levelOf(row, thresholdPct)
        return (
          <span className="inline-flex items-center gap-2">
            <span aria-hidden="true" className={`inline-block h-4 w-[3px] rounded-pill ${LEVEL_BAR[level.tone]}`} />
            <span className="text-caption text-text-secondary">{level.label}</span>
          </span>
        )
      },
    },
    {
      key: 'code',
      header: '代码',
      width: '100px',
      render: (row) => <span className="ldst-num text-text-secondary">{row.code || MISSING}</span>,
    },
    {
      key: 'name',
      header: '名称',
      render: (row) => <span className="truncate font-medium">{row.name || MISSING}</span>,
    },
    {
      key: 'status',
      header: '信号类型',
      width: '120px',
      render: (row) => <span className="text-text-secondary">{row.status ?? MISSING}</span>,
    },
    {
      key: 'change',
      header: '触发值',
      align: 'right',
      width: '104px',
      sort: (a, b) => (a.changePct ?? 0) - (b.changePct ?? 0),
      render: (row) => {
        const change = formatChangePct(row.changePct)
        return <SignedNumber text={change.text} tone={change.tone} arrow={change.arrow} />
      },
    },
    {
      key: 'threshold',
      header: '阈值',
      align: 'right',
      width: '84px',
      render: () => <span className="ldst-num text-text-muted">{thresholdPct.toFixed(2)}%</span>,
    },
    {
      key: 'boards',
      header: '连板数',
      align: 'right',
      width: '84px',
      render: (row) => (
        <span className="ldst-num text-text-secondary">
          {row.limitUpCount === null ? MISSING : formatInt(row.limitUpCount)}
        </span>
      ),
    },
  ]
}

export default function WatchScreen() {
  const forced = useForcedState()

  const storeTradeDate = useWatchSnapshot((state) => state.tradeDate)
  const storeFetchedAt = useWatchSnapshot((state) => state.fetchedAt)
  const storeSourceDetail = useWatchSnapshot((state) => state.sourceDetail)
  const storeRows = useWatchSnapshot((state) => state.rows)
  const setSnapshot = useWatchSnapshot((state) => state.setSnapshot)
  const clearSnapshot = useWatchSnapshot((state) => state.clear)

  // 时间流逝会让快照过期：每 15s 重新评估一次（§7.6 不打断输入）
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000)
    return () => window.clearInterval(timer)
  }, [])

  const request = useAsync((signal) => api.watchSnapshot(signal), [])
  const envelope = request.data

  // 只把「可用」的快照写回本机；不可用时保留旧缓存，由 TTL 判定是否可展示
  useEffect(() => {
    if (!envelope) return
    if (envelope.payload.rows.length === 0) return
    setSnapshot({
      tradeDate: envelope.payload.trade_date,
      fetchedAt: envelope.fetched_at,
      sourceDetail: envelope.source_detail,
      rows: envelope.payload.rows.map((row) => ({
        code: row.code,
        name: row.name,
        limitUpCount: row.limit_up_count,
        changePct: row.change_pct,
        status: row.status,
      })),
    })
  }, [envelope, setSnapshot])

  const freshness = evaluateSnapshotFreshness(
    { fetchedAt: storeFetchedAt, tradeDate: storeTradeDate },
    WATCH_SNAPSHOT_TTL_SEC,
    now,
  )
  const trustedRows = freshness.needsRefetch ? [] : storeRows

  // ---- 阈值（本次会话有效） ----
  const [pctText, setPctText] = useState(DEFAULT_THRESHOLD_PCT.toFixed(1))
  const [cooldownText, setCooldownText] = useState('60')
  const [detectOpen, setDetectOpen] = useState(true)
  const parsedPct = Number(pctText)
  const pctValid = Number.isFinite(parsedPct) && parsedPct >= THRESHOLD_MIN && parsedPct <= THRESHOLD_MAX
  const thresholdPct = pctValid ? parsedPct : DEFAULT_THRESHOLD_PCT
  const parsedCooldown = Number(cooldownText)
  const cooldownValid = Number.isFinite(parsedCooldown) && parsedCooldown >= 0 && parsedCooldown <= 600
  const columns = useMemo(() => buildColumns(thresholdPct), [thresholdPct])

  const [confirmClear, setConfirmClear] = useState(false)

  const sourceStatus = envelope?.source_status ?? null
  const unavailableDetail =
    envelope && envelope.payload.rows.length === 0 ? envelope.source_detail : null
  /** 后端明确回答「没有状态文件 / 不可用」：`available=false` 会被后端折成 `missing`（server.py:_envelope） */
  const backendMissing = envelope?.payload.data_source_status === 'missing'

  const derived = useMemo<PageState>(() => {
    if (request.status === 'loading' && storeRows.length === 0) return 'loading'
    if (request.status === 'error') {
      return isApiError(request.error) && request.error.kind === 'not-connected' ? 'offline' : 'error'
    }
    if (request.status === 'ready' && sourceStatus !== null && sourceStatus !== 'ok' && trustedRows.length === 0) {
      return 'partial'
    }
    if (trustedRows.length === 0) return 'empty'
    if (sourceStatus !== null && sourceStatus !== 'ok') return 'partial'
    return 'normal'
  }, [request.status, request.error, sourceStatus, storeRows.length, trustedRows.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)

  const remainingSec =
    freshness.ageSec === null ? null : Math.max(0, WATCH_SNAPSHOT_TTL_SEC - freshness.ageSec)

  // 真实原因优先用后端结论：本地没有任何快照时也说不出「为什么没有」，后端能
  const subtitle = backendMissing
    ? '快照不可用 · 后端没有可用的盯盘状态文件'
    : freshness.needsRefetch
      ? `快照不可用 · ${describeFreshness(freshness)}`
      : `快照有效（剩余 ${formatInt(remainingSec)}s）· 交易日 ${storeTradeDate ?? MISSING}`

  // 表体空态的文案必须与「后端到底有没有数据」一致，不能假设盯盘守护进程在跑。
  // offline 优先：没连上后端时无从谈「后端有没有状态文件」，只能讲本机回放。
  const emptyHint =
    state === 'offline'
      ? {
          title: '离线 · 本机没有可回放的信号',
          description: '本地服务未连接，本机 IndexedDB 里也没有有效期内的盯盘快照。恢复连接后点「立即取数」。',
        }
      : backendMissing
        ? {
            title: '盯盘还没有取过数',
            description:
              '后端没有盯盘状态文件：先运行 ldst watch start 产出状态文件，再点「立即取数」。本页不编造任何数值。',
          }
        : state === 'partial'
          ? {
              title: '暂无可用信号',
              description: '盯盘快照不可用，本次没有取到任何信号行；原因见上方提示条。',
            }
          : {
              title: '暂无信号',
              description: '最近一次扫描没有触发阈值；盯盘仍在运行，信号出现后这里会出现新行。',
            }

  const header = (
    <PageHeader
      title="盘中盯盘"
      subtitle={subtitle}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="primary"
            onClick={request.reload}
            loading={request.status === 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            立即取数
          </Button>
          <Button
            variant="secondary"
            onClick={() => setConfirmClear(true)}
            disabled={storeRows.length === 0}
            leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
          >
            清除快照
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={4} rows={8} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock {...describeApiError(request.error)} onRetry={request.reload} retryLabel="重试取数" />
      </div>
    )
  }

  const showRows = state === 'normal' || state === 'partial' || state === 'offline'

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'offline' && <OfflineBanner onRetry={request.reload} />}
      {(state === 'offline' || backendMissing) && trustedRows.length > 0 && (
        <Badge tone="warn" title="本机缓存快照，可能不是实时数据">
          缓存回放（只读）· 数据时间 {formatTime(storeFetchedAt)}
        </Badge>
      )}
      {state === 'partial' && (
        <ErrorBlock
          variant="banner"
          title="盘中快照不可用"
          detail={
            unavailableDetail ??
            '当前没有可用快照（可能未运行 ldst watch start 或已超过 TTL）。旧值不展示，避免按过期数据决策。'
          }
          onRetry={request.reload}
          retryLabel="重新取数"
        />
      )}

      <Card>
        <dl className="grid grid-cols-2 divide-border desk:grid-cols-4 desk:divide-x">
          <div className="flex flex-col gap-1 p-4">
            <dt className="text-caption text-text-secondary">快照状态</dt>
            <dd>
              <StatusDot tone={freshness.needsRefetch ? 'warn' : 'ok'}>
                {freshness.needsRefetch ? '需重新取数' : '有效期以内'}
              </StatusDot>
            </dd>
          </div>
          <div className="flex flex-col gap-1 p-4">
            <dt className="text-caption text-text-secondary">交易日</dt>
            <dd className="ldst-num text-body text-text">{storeTradeDate ?? MISSING}</dd>
          </div>
          <div className="flex flex-col gap-1 p-4">
            <dt className="text-caption text-text-secondary">取数时间</dt>
            <dd className="ldst-num text-body text-text">{formatTime(storeFetchedAt)}</dd>
          </div>
          <div className="flex flex-col gap-1 p-4">
            <dt className="text-caption text-text-secondary">剩余 TTL</dt>
            <dd className="ldst-num text-body text-text">
              {formatInt(remainingSec)}s
              <span className="ml-1 text-caption text-text-muted">/ {WATCH_SNAPSHOT_TTL_SEC}s</span>
            </dd>
          </div>
        </dl>
      </Card>

      {freshness.needsRefetch && storeRows.length > 0 && (
        <ErrorBlock
          variant="inline"
          title="旧值已隐藏"
          detail={`${describeFreshness(freshness)}。本页不展示超过有效期的行情数值。`}
          onRetry={request.reload}
          retryLabel="重新取数"
        />
      )}

      <div className="grid gap-4 desk:grid-cols-12">
        <SectionCard
          className="desk:col-span-8"
          title="信号流"
          subtitle={
            showRows && trustedRows.length > 0
              ? `${tradeDateLabel(storeTradeDate, now)} · ${formatInt(trustedRows.length)} 条 · 最新在上`
              : '暂无可用信号'
          }
          bodyClassName="p-0"
        >
          {state === 'empty' ? (
            <div className="p-4">
              <EmptyState
                icon={<RefreshCw aria-hidden="true" />}
                title="当前没有可信快照"
                description="先运行 ldst watch start 让盯盘守护进程产出状态文件，再点「立即取数」。本页不编造任何数值。"
                action={
                  <Button variant="secondary" onClick={request.reload}>
                    立即取数
                  </Button>
                }
              />
            </div>
          ) : (
            <DataTable<SnapshotRow>
              columns={columns}
              rows={showRows ? trustedRows : []}
              rowKey={(row) => row.code}
              caption={`盘中信号流（${tradeDateLabel(storeTradeDate, now)}），按最近一次事件合并，每股一行`}
              density="compact"
              maxHeight="520px"
              empty={
                <EmptyState
                  compact
                  title={emptyHint.title}
                  description={emptyHint.description}
                  action={
                    <Button variant="secondary" onClick={request.reload}>
                      立即取数
                    </Button>
                  }
                />
              }
            />
          )}
        </SectionCard>

        <SectionCard
          className="desk:col-span-4"
          title="阈值设置"
          subtitle="改动即时用于本次会话"
          actions={<Badge tone="info">本次会话有效</Badge>}
        >
          <div className="flex flex-col gap-3">
            <Field
              label="涨幅阈值（%）"
              htmlFor="watch-threshold"
              hint={`${THRESHOLD_MIN.toFixed(1)} – ${THRESHOLD_MAX.toFixed(1)}，步进 0.5`}
              error={pctValid ? null : `阈值需在 ${THRESHOLD_MIN.toFixed(1)}–${THRESHOLD_MAX.toFixed(1)} 之间`}
            >
              <Input
                id="watch-threshold"
                type="number"
                inputMode="decimal"
                step="0.5"
                min={THRESHOLD_MIN}
                max={THRESHOLD_MAX}
                value={pctText}
                invalid={!pctValid}
                onChange={(event) => setPctText(event.target.value)}
              />
            </Field>

            <div className="flex items-center justify-between border-t border-border pt-3">
              <span className="text-caption text-text-secondary">统计窗口</span>
              <span className="text-body text-text">{SCAN_WINDOW_LABEL}</span>
            </div>

            <div className="border-t border-border pt-3">
              <Switch
                checked={detectOpen}
                onChange={setDetectOpen}
                label="封板 / 开板检测"
                description="打开后同时监测涨停封板与炸板信号"
              />
            </div>

            <Field
              label="同一标的冷却时间（秒）"
              htmlFor="watch-cooldown"
              hint="同一标的同类型信号在冷却期内合并为一条"
              error={cooldownValid ? null : '冷却时间需在 0–600 秒之间'}
            >
              <Input
                id="watch-cooldown"
                type="number"
                inputMode="numeric"
                step="30"
                min={0}
                max={600}
                value={cooldownText}
                invalid={!cooldownValid}
                onChange={(event) => setCooldownText(event.target.value)}
              />
            </Field>

            <p className="border-t border-border pt-3 text-caption text-text-muted">
              阈值不落盘：刷新后回到默认 3.0%。要跨刷新保留需新增 watch_settings 分片（另行决策，本轮不加）。
            </p>
          </div>
        </SectionCard>
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2">
          <p className="text-caption text-text-muted">
            快照来源：{envelope?.source_detail ?? storeSourceDetail ?? MISSING} · 扫描间隔由 ldst watch
            守护进程决定 · 最近扫描 {formatTime(storeFetchedAt)} · 本机 {localDateIso(now)}
          </p>
          <Button
            variant="ghost"
            disabled
            disabledReason="通知链路自检由 ldst notify test 提供（本轮不接入）"
          >
            通知链路自检
          </Button>
        </div>
      </Card>

      <ConfirmDialog
        open={confirmClear}
        title="清除本机盯盘快照？"
        description="清除后本机不再保留任何信号行与取数时间，下次进入需重新取数。只影响本机 IndexedDB 缓存，不影响后端数据、阈值设置与草稿。该操作不可撤销。"
        confirmLabel="清除快照"
        tone="danger"
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => {
          clearSnapshot()
          setConfirmClear(false)
        }}
      />
    </div>
  )
}

/** 交易日标签：当日显示「今日」，否则显示日期原值（不猜相对日期）。 */
function tradeDateLabel(tradeDate: string | null, now: Date): string {
  if (!tradeDate) return `交易日 ${MISSING}`
  return tradeDate === localDateIso(now) ? `今日 ${tradeDate}` : `交易日 ${tradeDate}`
}
