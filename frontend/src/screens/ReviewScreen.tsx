import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Copy, FileText, RefreshCw, Trash2 } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  parseAiFailures,
  type JobView,
  type ReviewAiFailure,
  type SentimentResult,
} from '../api'
import { AiErrorNotice } from '../components/kit'
import { useAsync } from '../lib/useAsync'
import { useForcedState, resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatInt, formatRatePct, formatTime, localDateIso } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  MetricCard,
  SectionCard,
  SkeletonText,
  SourceBadges,
  Textarea,
  type SectionTone,
} from '../components/ui'
import { KV_STORE, idbGet, idbSet } from '../../persist/indexeddb'
import {
  REVIEW_SECTIONS,
  draftCharCount,
  useReviewDraft,
  type ReviewSectionKey,
} from '../../stores/reviewDraft'

/**
 * 收盘复盘（P-04 / DESIGN-SPEC §4.5 / UI-SPEC §3.1）。
 *
 * 3 张指标卡（程序直出，不可编辑）+ 五段固定顺序（情绪→资金→题材→龙虎榜→龙头）。
 * 「AI 叙述」只读、来自 `/api/review`；「我的判断」写 `reviewDraft`（IndexedDB 自动保存）。
 * 「重新生成」走控制面 `POST /api/control/ai/generate`（后台任务 + 轮询 `/jobs/{id}`），
 * 单段失败的重试走 `/ai/retry`；失败原因统一用 QUOT-235 的 `AiErrorNotice` 展示。
 * 任何缺失值显示 `—` + 原因，不补 0、不编造。
 */

/** 段状态 → 左侧 3px 竖条 + 文字徽标双重编码（§4.5）。 */
const SECTION_STATUS: Record<string, { label: string; tone: SectionTone; badge: 'ok' | 'warn' | 'danger' | 'info' | 'neutral' }> = {
  pending: { label: '待生成', tone: 'neutral', badge: 'neutral' },
  running: { label: '生成中', tone: 'info', badge: 'info' },
  ok: { label: '已完成', tone: 'ok', badge: 'ok' },
  degraded: { label: '降级完成', tone: 'warn', badge: 'warn' },
  failed: { label: '失败', tone: 'danger', badge: 'danger' },
}

/** 页面级汇总用的短名（UI-SPEC §5.4「5 段中 2 段失败（网络 / 截断）」）。 */
const FAILURE_KIND_LABEL: Record<string, string> = {
  network: '网络',
  quota: '额度',
  format: '格式',
  truncated: '截断',
  internal: '未归类',
}

const NO_FAILURES: ReviewAiFailure[] = []

/** 离线回放的 IndexedDB 快照：只存复盘正文与程序指标，不含「我的判断」。 */
const CACHE_KEY = 'review_snapshot_cache_v1'
const CACHE_SCHEMA = 1

interface ReviewCache {
  schema: typeof CACHE_SCHEMA
  date: string
  fetchedAt: string
  review: Record<string, unknown>
  sentiment: SentimentResult | null
}

function isReviewCache(value: unknown): value is ReviewCache {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<ReviewCache>
  return (
    item.schema === CACHE_SCHEMA &&
    typeof item.date === 'string' &&
    typeof item.fetchedAt === 'string' &&
    !!item.review &&
    typeof item.review === 'object'
  )
}

interface SectionTally {
  failed: number
  degraded: number
}

function tallySections(sections: AiSection[]): SectionTally {
  return sections.reduce<SectionTally>(
    (tally, section) => {
      if (section.status === 'failed') tally.failed += 1
      if (section.status === 'degraded') tally.degraded += 1
      return tally
    },
    { failed: 0, degraded: 0 },
  )
}

function summariseFailureKinds(failures: ReviewAiFailure[]): string {
  return [...new Set(failures.map((failure) => FAILURE_KIND_LABEL[failure.kind] ?? failure.kind))].join(
    ' / ',
  )
}

/** 草稿分片键 → 复盘 payload 的段名（payload 用 `dragon_tiger`）。 */
const SECTION_PAYLOAD_NAME: Record<ReviewSectionKey, string> = {
  sentiment: 'sentiment',
  capital: 'capital',
  theme: 'theme',
  dragonTiger: 'dragon_tiger',
  leader: 'leader',
}

interface AiSection {
  name: string
  title: string
  status: string
  model: string | null
  degraded: boolean
  narrative: string[]
  notes: string[]
}

interface ReviewHeader {
  generatedAt: string | null
  headline: string[]
  aiModel: string | null
  aiDegraded: boolean
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

/** `review` 是 `Record<string, unknown>`：逐字段防御性取值，结构不符就当没有。 */
function parseSections(review: Record<string, unknown> | null): AiSection[] {
  if (!review || !Array.isArray(review.sections)) return []
  return review.sections.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const name = typeof record.name === 'string' ? record.name : null
    if (!name) return []
    return [
      {
        name,
        title: typeof record.title === 'string' ? record.title : name,
        status: typeof record.status === 'string' ? record.status : 'pending',
        model: typeof record.model === 'string' ? record.model : null,
        degraded: record.degraded === true,
        narrative: asStringList(record.narrative),
        notes: asStringList(record.notes),
      },
    ]
  })
}

function parseHeader(review: Record<string, unknown> | null): ReviewHeader {
  if (!review) return { generatedAt: null, headline: [], aiModel: null, aiDegraded: false }
  const ai = review.ai && typeof review.ai === 'object' ? (review.ai as Record<string, unknown>) : null
  return {
    generatedAt: typeof review.generated_at === 'string' ? review.generated_at : null,
    headline: asStringList(review.headline),
    aiModel: ai && typeof ai.model === 'string' ? ai.model : null,
    aiDegraded: ai?.degraded === true,
  }
}

export default function ReviewScreen() {
  const forced = useForcedState()
  const [params] = useSearchParams()
  const navigate = useNavigate()

  const draftTradeDate = useReviewDraft((state) => state.tradeDate)
  const updatedAt = useReviewDraft((state) => state.updatedAt)
  const sections = useReviewDraft((state) => state.sections)
  const setTradeDate = useReviewDraft((state) => state.setTradeDate)
  const setSection = useReviewDraft((state) => state.setSection)
  const setActiveSection = useReviewDraft((state) => state.setActiveSection)
  const reset = useReviewDraft((state) => state.reset)

  const tradeDate = draftTradeDate ?? params.get('date') ?? localDateIso()

  const review = useAsync((signal) => api.review(tradeDate, signal), [tradeDate])
  const sentiment = useAsync((signal) => api.sentiment(tradeDate, signal), [tradeDate])
  const requests = [review, sentiment]
  const reloadAll = useCallback(() => {
    review.reload()
    sentiment.reload()
  }, [review.reload, sentiment.reload]) // eslint-disable-line react-hooks/exhaustive-deps
  const busy = requests.some((request) => request.status === 'loading')

  // AI 生成 / 重试任务：POST 建任务 → 轮询 `/control/jobs/{id}` 直到 ok / failed
  const [aiJob, setAiJob] = useState<JobView | null>(null)
  const [aiJobError, setAiJobError] = useState<string | null>(null)
  const aiBusy = aiJob !== null

  const startAi = async (mode: 'generate' | 'retry', section?: string) => {
    setAiJobError(null)
    setNotice(null)
    try {
      const handle =
        mode === 'generate'
          ? await api.control.generateAi({ date: tradeDate })
          : await api.control.retryAi({ date: tradeDate, section })
      setAiJob({
        id: handle.job_id,
        peer: 'ldst',
        action: mode,
        status: 'queued',
        step: '',
        progress: 0,
        started_at: null,
        ended_at: null,
        log_tail: [],
        error: null,
      })
    } catch (error) {
      const described = describeApiError(error)
      setAiJobError(`${described.title}：${described.detail}`)
    }
  }

  useEffect(() => {
    if (!aiJob) return
    let cancelled = false
    const poll = async () => {
      try {
        const next = await api.control.job(aiJob.id)
        if (cancelled) return
        if (next.status === 'ok' || next.status === 'failed') {
          setAiJob(null)
          if (next.status === 'ok') {
            setNotice(
              next.action === 'retry' ? '失败段已重试完成，报告已刷新' : 'AI 生成完成，报告已刷新',
            )
            review.reload()
            sentiment.reload()
          } else {
            setAiJobError(next.error ?? 'AI 生成失败')
          }
          return
        }
        setAiJob(next)
      } catch (error) {
        if (cancelled) return
        setAiJob(null)
        setAiJobError(describeApiError(error).detail)
      }
    }
    const timer = window.setInterval(() => {
      void poll()
    }, 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [aiJob]) // eslint-disable-line react-hooks/exhaustive-deps

  // 首帧快照：区分「本机恢复的草稿」与「本次新写的笔记」
  const [restored] = useState(() => {
    const state = useReviewDraft.getState()
    return { chars: draftCharCount(state), updatedAt: state.updatedAt }
  })

  const total = useMemo(
    () => Object.values(sections).reduce((sum, text) => sum + text.trim().length, 0),
    [sections],
  )

  const [confirmReset, setConfirmReset] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [cached, setCached] = useState<ReviewCache | null>(null)
  const [copied, setCopied] = useState(false)

  // 离线回放：只读取本机 IndexedDB 里的复盘快照（正文 + 程序指标）
  useEffect(() => {
    let alive = true
    idbGet<unknown>(KV_STORE, CACHE_KEY)
      .then((value) => {
        if (alive && isReviewCache(value)) setCached(value)
      })
      .catch(() => {
        /* 无缓存 / IndexedDB 不可用：离线态如实显示「没有可回放的缓存」 */
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (review.status !== 'ready') return
    const report = review.data?.payload.review ?? null
    if (!report) return
    const entry: ReviewCache = {
      schema: CACHE_SCHEMA,
      date: tradeDate,
      fetchedAt: new Date().toISOString(),
      review: report,
      sentiment: sentiment.data?.payload.sentiment ?? null,
    }
    setCached(entry)
    void idbSet(KV_STORE, CACHE_KEY, entry).catch(() => {
      /* 写缓存失败不影响当前展示，下次成功取数再写 */
    })
  }, [review.status, review.data, sentiment.status, sentiment.data, tradeDate])

  const reviewData = review.data?.payload.review ?? null
  const aiSections = useMemo(() => parseSections(reviewData), [reviewData])
  const aiHeader = useMemo(() => parseHeader(reviewData), [reviewData])
  const aiFailures = useMemo(() => parseAiFailures(reviewData?.ai_failures), [reviewData])
  const sent: SentimentResult | null = sentiment.data?.payload.sentiment ?? null

  const derived = useMemo<PageState>(() => {
    const statuses = requests.map((request) => request.status)
    if (statuses.every((status) => status === 'loading')) return 'loading'
    const errors = requests.map((request) => request.error).filter(Boolean)
    if (errors.length === statuses.length) {
      return errors.every((error) => isApiError(error) && error.kind === 'not-connected') ? 'offline' : 'error'
    }
    if (errors.length > 0) return 'partial'
    if (statuses.every((status) => status === 'ready')) {
      if (reviewData === null) return 'empty'
      // 部分失败的主场景：五段里任一段 failed / degraded 即进入 partial，逐段标注
      const tally = tallySections(parseSections(reviewData))
      return tally.failed + tally.degraded > 0 ? 'partial' : 'normal'
    }
    return 'loading'
  }, [review.status, sentiment.status, reviewData]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  // 生成 / 重试需要本机控制面可达；offline 只读回放，不假装能跑
  const canRunAi = usable || state === 'empty'

  // 离线回放：只用本机 IndexedDB 快照，日期不符就不冒充当天数据
  const replay = state === 'offline' && cached && cached.date === tradeDate ? cached : null
  const viewSections = useMemo(
    () => (usable ? aiSections : replay ? parseSections(replay.review) : []),
    [usable, aiSections, replay],
  )
  const viewHeader = usable ? aiHeader : replay ? parseHeader(replay.review) : null
  const viewFailures = useMemo(
    () => (usable ? aiFailures : replay ? parseAiFailures(replay.review.ai_failures) : NO_FAILURES),
    [usable, aiFailures, replay],
  )
  const viewSent = usable ? sent : replay?.sentiment ?? null
  const viewTally = useMemo(() => tallySections(viewSections), [viewSections])
  const firstFailedSection = viewSections.find((section) => section.status === 'failed')?.name ?? null
  const reviewCommand = `ldst review --date ${tradeDate}`

  const sectionMap = useMemo(() => {
    const map = new Map<string, AiSection>()
    viewSections.forEach((section) => map.set(section.name, section))
    return map
  }, [viewSections])

  const failureMap = useMemo(() => {
    const map = new Map<string, ReviewAiFailure>()
    viewFailures.forEach((failure) => map.set(failure.section, failure))
    return map
  }, [viewFailures])

  const failedLabels = useMemo(() => {
    const labels: string[] = []
    if (review.status === 'error') labels.push('复盘正文')
    if (sentiment.status === 'error') labels.push('程序指标')
    return labels
  }, [review.status, sentiment.status])

  // 程序指标不可用时整组按缺失处理：空数组的 `gap_levels` 不是「0 档断层」
  const sentimentReady = viewSent?.available === true
  const temperature = sentimentReady ? viewSent?.temperature ?? null : null
  const promotionRate = sentimentReady ? viewSent?.promotion_rate ?? null : null
  const gapLevels = sentimentReady ? viewSent?.gap_levels ?? null : null

  const summary = useMemo(() => {
    if (state === 'offline') {
      return replay
        ? `离线 · 缓存回放（取数于 ${formatTime(replay.fetchedAt)}）`
        : '本地服务未连接 · 没有可回放的缓存'
    }
    if (state === 'error') return '取数失败'
    if (state === 'empty') return `${tradeDate} 尚未生成复盘 · 运行 ${reviewCommand}`
    if (state === 'partial') {
      if (failedLabels.length > 0) return `${tradeDate} · 部分数据源不可用`
      return `${tradeDate} · 五段中 ${formatInt(viewTally.failed)} 段失败 / ${formatInt(viewTally.degraded)} 段降级`
    }
    return `${tradeDate} · 草稿 ${formatInt(total)} 字 · 最后写入 ${formatTime(updatedAt)}`
  }, [state, tradeDate, total, updatedAt, failedLabels.length, viewTally.failed, viewTally.degraded, replay, reviewCommand])

  const handleReset = () => {
    reset()
    setConfirmReset(false)
    setNotice('本机复盘草稿已清空')
  }

  const copyCommand = () => {
    const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
    if (!clipboard?.writeText) {
      setNotice('当前浏览器不允许自动复制，请手动选中命令复制。')
      return
    }
    void clipboard
      .writeText(reviewCommand)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setNotice('复制失败，请手动选中命令复制。'))
  }

  const focusSection = (name: string) => {
    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    document
      .getElementById(`review-section-${name}`)
      ?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' })
  }

  const header = (
    <PageHeader
      title="收盘复盘"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <>
          <Button
            variant="secondary"
            loading={busy && state !== 'loading'}
            disabled={state === 'loading'}
            onClick={reloadAll}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
          <Button
            variant="primary"
            loading={aiBusy}
            disabled={!canRunAi}
            disabledReason={
              canRunAi
                ? undefined
                : state === 'offline'
                  ? '本地服务未连接，无法触发生成'
                  : '取数未完成，暂不能触发生成'
            }
            onClick={() => void startAi('generate')}
          >
            {aiBusy ? 'AI 生成中…' : viewFailures.length > 0 ? '重试失败段' : '重新生成'}
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <p className="text-caption text-text-muted" role="status">
          程序指标计算中，AI 叙述随后到达。
        </p>
        <ScreenSkeleton metrics={3} rows={5} />
      </div>
    )
  }

  if (state === 'error') {
    const firstError = requests.map((request) => request.error).find(Boolean)
    const errorInfo = firstError
      ? describeApiError(firstError)
      : {
          title: '复盘取数失败',
          detail: '复盘接口没有返回可用数据。恢复后点「重试取数」；本屏不编造任何数字。',
          code: 'DEMO · ?state=error',
        }
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock {...errorInfo} onRetry={reloadAll} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'offline' && <OfflineBanner onRetry={reloadAll} />}
      {state === 'offline' && replay && (
        <Banner tone="warn" title="缓存回放（只读）">
          本机服务未连接，以下为本机 IndexedDB 缓存的复盘快照 · 生成于{' '}
          {formatTime(viewHeader?.generatedAt ?? null)} · 取数于 {formatTime(replay.fetchedAt)}。
          快照只含复盘正文与程序指标，不含「我的判断」；恢复连接后点「刷新」取最新。
        </Banner>
      )}
      {state === 'offline' && !replay && (
        <Banner tone="warn" title="没有可回放的缓存">
          本机服务未连接，IndexedDB 里也没有 {tradeDate} 的复盘快照。先在终端确认{' '}
          <code className="ldst-num">ldst run</code> 是否在运行，恢复后点「刷新」。
        </Banner>
      )}
      {state === 'partial' && failedLabels.length > 0 && (
        <ErrorBlock
          variant="banner"
          title="部分数据源不可用"
          detail={`${failedLabels.join('、')} 取数失败；其余内容照常展示，缺失处以 ${MISSING} 标注。草稿仍可继续写。`}
          onRetry={reloadAll}
          retryLabel="重试取数"
        />
      )}
      {state === 'partial' && failedLabels.length === 0 && viewTally.failed + viewTally.degraded > 0 && (
        <Banner
          tone={viewTally.failed > 0 ? 'danger' : 'warn'}
          title={`五段中 ${formatInt(viewTally.failed)} 段失败、${formatInt(viewTally.degraded)} 段降级${
            viewFailures.length > 0 ? `（${summariseFailureKinds(viewFailures)}）` : ''
          }`}
          action={
            firstFailedSection ? (
              <Button variant="secondary" onClick={() => focusSection(firstFailedSection)}>
                定位首个失败段
              </Button>
            ) : undefined
          }
        >
          每段已单独标注：失败段保留程序指标并可就地重跑，降级段保留降级说明；其余段落照常渲染。
          「我的判断」草稿不受影响。
        </Banner>
      )}
      {state === 'partial' && failedLabels.length === 0 && viewTally.failed + viewTally.degraded === 0 && (
        <Banner tone="warn" title="部分失败 · 五段各自独立">
          某一数据源或某一段不可用时进入本态：该段单独标注失败 / 降级，其余段落照常渲染，不整屏报错。
        </Banner>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Field label="交易日" htmlFor="review-date" className="w-44">
          <Input
            id="review-date"
            type="date"
            value={tradeDate}
            onChange={(event) => setTradeDate(event.target.value || null)}
          />
        </Field>
        <Badge tone="neutral">草稿 {formatInt(total)} 字</Badge>
        <Badge tone="neutral">最后写入 {formatTime(updatedAt)}</Badge>
        {viewHeader?.generatedAt && <Badge tone="neutral">生成于 {formatTime(viewHeader.generatedAt)}</Badge>}
        <div className="grow" />
        <Button
          variant="secondary"
          onClick={() => setConfirmReset(true)}
          disabled={total === 0}
          leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
        >
          清空草稿
        </Button>
      </div>

      {restored.chars > 0 && (
        <Banner tone="info" title="已从本机恢复草稿">
          {formatInt(restored.chars)} 字正文（最后写入 {formatTime(restored.updatedAt)}）。刷新、崩溃、升级后都会原样回填。
        </Banner>
      )}
      {notice && (
        <p className="text-caption text-text-secondary" role="status">
          {notice}
        </p>
      )}
      {aiBusy && (
        <Banner tone="info" title="AI 正在生成复盘">
          五段依次生成，已完成段落会保留；完成后本页自动刷新。
          {aiJob?.step ? `当前步骤：${aiJob.step}` : ''}
        </Banner>
      )}
      {aiJobError && (
        <AiErrorNotice
          kind="internal"
          message={aiJobError}
          onOpenStatus={() => navigate('/settings?tab=ai')}
        />
      )}

      {state === 'empty' && (
        <SectionCard
          title="这一天还没有复盘"
          subtitle={`数据面：ldst review 产物（只读）· ${tradeDate}`}
          bodyClassName="p-0"
          actions={<Badge tone="neutral">待生成</Badge>}
        >
          <EmptyState
            icon={<FileText aria-hidden="true" />}
            title={`${tradeDate} 尚未生成复盘`}
            description="在终端运行下面这条命令生成五段复盘（情绪 / 资金 / 题材 / 龙虎榜 / 龙头），落盘后回到本页点「重新检查」。未配置模型凭据时，五段会以「降级完成」落盘，程序指标照常保留。"
            action={
              <div className="flex w-full max-w-[720px] flex-col items-center gap-2">
                <code className="ldst-num w-full break-all rounded border border-border bg-surface-3 px-3 py-2 text-left text-caption text-text-secondary">
                  {reviewCommand}
                </code>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={copyCommand}
                    leadingIcon={<Copy className="size-3.5" aria-hidden="true" />}
                  >
                    {copied ? '已复制' : '复制命令'}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={reloadAll}
                    leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
                  >
                    重新检查
                  </Button>
                </div>
              </div>
            }
          />
        </SectionCard>
      )}

      <div className="grid grid-cols-1 gap-4 desk:grid-cols-3">
        <MetricCard
          label="情绪温度"
          hero
          badge={<SourceBadges envelope={sentiment.data} />}
          value={temperature === null ? MISSING : formatInt(temperature)}
          note={
            temperature === null
              ? viewSent?.reason ?? (state === 'offline' ? '本地服务未连接' : '未生成')
              : `50 为中性 · 涨停 ${formatInt(viewSent?.limit_up_count)} 家`
          }
        />
        <MetricCard
          label="晋级率"
          badge={<SourceBadges envelope={sentiment.data} />}
          value={formatRatePct(promotionRate).text}
          note={
            promotionRate === null
              ? viewSent?.reason ?? (state === 'offline' ? '本地服务未连接' : '未生成')
              : `昨日涨停 ${formatInt(viewSent?.yesterday_limit_up_count)} 家`
          }
        />
        <MetricCard
          label="梯队断层"
          badge={<SourceBadges envelope={sentiment.data} />}
          value={gapLevels === null ? MISSING : formatInt(gapLevels.length)}
          unit={gapLevels === null ? undefined : '档'}
          note={
            gapLevels === null
              ? viewSent?.reason ?? (state === 'offline' ? '本地服务未连接' : '未生成')
              : `最高 ${formatInt(viewSent?.max_board)} 板 · ${viewSent?.gap ? '存在断层' : '无断层'}`
          }
        />
      </div>

      {viewHeader && viewHeader.headline.length > 0 && (
        <section className="rounded border border-border bg-surface-2 px-4 py-3" aria-label="AI 摘要">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone={viewHeader.aiDegraded ? 'warn' : 'info'}>
              {viewHeader.aiDegraded ? '摘要降级' : 'AI 摘要'}
            </Badge>
            <span className="text-caption text-text-muted">模型 {viewHeader.aiModel ?? '未启用'}</span>
          </div>
          <ul className="flex flex-col gap-1">
            {viewHeader.headline.map((line) => (
              <li key={line} className="text-body leading-body text-text-secondary">
                {line}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-col gap-4">
        {REVIEW_SECTIONS.map((section, index) => {
          const payloadName = SECTION_PAYLOAD_NAME[section.key]
          const payload = sectionMap.get(payloadName)
          const failure = failureMap.get(payloadName)
          const status = payload?.status ?? 'pending'
          const meta = SECTION_STATUS[status] ?? SECTION_STATUS.pending
          const draftText = sections[section.key]
          const textareaId = `review-draft-${section.key}`
          return (
            <div key={section.key} id={`review-section-${payloadName}`} className="scroll-mt-6">
            <SectionCard
              tone={meta.tone}
              title={
                <span>
                  <span className="ldst-num mr-2 text-caption text-text-muted">{index + 1}</span>
                  {section.label}
                </span>
              }
              subtitle={payload?.title ?? section.hint}
              actions={
                <>
                  <Badge tone={meta.badge}>{meta.label}</Badge>
                  {payload?.degraded && <Badge tone="warn">降级</Badge>}
                  {status === 'failed' && (
                    <Button
                      variant="secondary"
                      onClick={() => void startAi('retry', payloadName)}
                      loading={aiBusy}
                      disabled={!canRunAi}
                      disabledReason={
                        canRunAi
                          ? undefined
                          : state === 'offline'
                            ? '本地服务未连接，无法重跑'
                            : '取数未完成，暂不能重跑'
                      }
                    >
                      {aiBusy ? '重跑中…' : '重跑该段'}
                    </Button>
                  )}
                </>
              }
            >
              <div className="grid gap-4 desk:grid-cols-[2fr_3fr]">
                <div className="rounded border border-border bg-surface-2 p-3">
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="text-caption font-medium text-text-secondary">AI 叙述</span>
                    <span className="truncate text-caption text-text-muted">
                      模型 {payload?.model ?? '未启用'}
                    </span>
                  </div>
                  {payload?.degraded && status !== 'failed' && (
                    <AiErrorNotice
                      kind="degraded"
                      className="mb-2"
                      onOpenStatus={() => navigate('/settings?tab=ai')}
                    />
                  )}
                  {status === 'running' ? (
                    <SkeletonText lines={3} />
                  ) : failure ? (
                    <div className="flex flex-col gap-2">
                      <AiErrorNotice
                        kind={failure.kind}
                        section={section.label}
                        message={failure.message}
                        action={failure.action}
                        keptPrevious={(payload?.narrative.length ?? 0) > 0}
                        retryLabel={aiBusy ? 'AI 生成中…' : '重试该段'}
                        onRetry={() => void startAi('retry', failure.section)}
                        onOpenStatus={() => navigate('/settings?tab=ai')}
                      />
                      {payload && payload.narrative.length > 0 && (
                        <div className="flex flex-col gap-1.5">
                          {payload.narrative.map((line) => (
                            <p key={line} className="text-body leading-body text-text-secondary">
                              {line}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : status === 'failed' ? (
                    <div className="flex flex-col gap-2">
                      <ErrorBlock
                        variant="inline"
                        title="该段生成失败"
                        detail={
                          (payload?.notes ?? []).join(' ') ||
                          'AI 未返回内容；程序指标与我的判断不受影响。'
                        }
                        onRetry={canRunAi ? () => void startAi('retry', payloadName) : undefined}
                        retryLabel={aiBusy ? 'AI 重跑中…' : '重跑该段'}
                      />
                      <p className="text-caption text-text-muted">
                        可重跑：只重跑本段，其余失败段与已完成段落保留；也可运行{' '}
                        <code className="ldst-num">{reviewCommand}</code> 整份重生成。
                      </p>
                    </div>
                  ) : payload && payload.narrative.length > 0 ? (
                    <div className="flex flex-col gap-1.5">
                      {payload.narrative.map((line) => (
                        <p key={line} className="text-body leading-body text-text-secondary">
                          {line}
                        </p>
                      ))}
                      {payload.notes.length > 0 && (
                        <p className="mt-1 border-t border-border pt-2 text-caption text-text-muted">
                          {payload.notes.join(' ')}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-body leading-body text-text-muted">
                      该段还没有 AI 叙述（待生成）。运行 <code className="ldst-num">{reviewCommand}</code>{' '}
                      生成五段复盘，或点右上「重新生成」。{section.hint}。
                    </p>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <Field
                    label={`我的判断 · ${section.label}`}
                    htmlFor={textareaId}
                    hint="自动存本机（IndexedDB），刷新、崩溃、升级后原样回填；不联网、不上传。"
                  >
                    <Textarea
                      id={textareaId}
                      value={draftText}
                      placeholder={`写今天的${section.label}判断…`}
                      onChange={(event) => setSection(section.key, event.target.value)}
                      onFocus={() => setActiveSection(section.key)}
                    />
                  </Field>
                  <p className="self-end text-caption text-text-muted">{formatInt(draftText.trim().length)} 字</p>
                </div>
              </div>
            </SectionCard>
            </div>
          )
        })}
      </div>

      <p className="text-caption text-text-muted">
        指标口径：情绪温度 0–100（50 中性）；晋级率为昨日涨停今日晋级比例；梯队断层统计断档档数。
        指标由程序直出，不可编辑；缺失显示 {MISSING} 并注明原因。
      </p>

      <ConfirmDialog
        open={confirmReset}
        title="清空本机复盘草稿？"
        description="五段正文与交易日会一并清除，该操作不可撤销；只影响本机 IndexedDB，不动后端已生成的复盘。"
        confirmLabel="清空草稿"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={handleReset}
      />
    </div>
  )
}
