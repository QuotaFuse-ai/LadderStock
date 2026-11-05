import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Bot, Copy, RefreshCw } from 'lucide-react'

import {
  api,
  describeApiError,
  isApiError,
  parseAiFailures,
  type JobView,
  type ReviewAiFailure,
} from '../api'
import { AiErrorNotice } from '../components/kit'
import { resolveState, type PageState } from '../lib/pageState'
import { MISSING, formatInt, formatTime, localDateIso } from '../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  SectionCard,
  SkeletonText,
  SourceBadges,
  type BadgeTone,
} from '../components/ui'
import { LinkButton } from '../components/research/bits'
import { REVIEW_SECTIONS } from '../../stores/reviewDraft'
import { useAiReviewView, useReviewDemoEnabled } from './review/useReviewView'
import { DEMO_REVIEW_DATE } from './review/demo'
import {
  SECTION_PAYLOAD_NAME,
  SECTION_STATUS,
  classifyAiCase,
  isRetryableFailure,
  parseHeader,
  parseSections,
  summariseFailureKinds,
  tallySections,
  type AiReviewCase,
  type ReviewSectionView,
} from './review/parse'

/**
 * AI复盘（模型面，`/review/ai`，QUOT-344-S2 / DEC-049）。
 *
 * 只放模型产物：五段 `review.sections[].narrative` + 段级 `status` + `ai_failures[]` +
 * `ai.model` / `ai.degraded`，以及控制面动作 `POST /control/ai/generate` 与按段
 * `/control/ai/retry`（含 `control.job` 轮询）。
 *
 * 这一屏**没有程序指标卡**、没有逐段 `metrics`、「我的判断」也不可编辑——
 * 数据面与草稿在每日复盘 `/review`（DEC-049 §2/§3 裁定的边界，两屏读同一份 `/api/review`）。
 *
 * 三种「没有 AI 正文」必须分开显示，不允许把「模型未配」显示成「数据缺失」（DEC-049 §4）：
 * 1. `no-artifact`：当天复盘产物本身不存在 → 引导先跑 `ldst review`；
 * 2. `model-unconfigured`：产物在、程序指标都在，但没有一段跑过模型 → 引导「接入 AI」；
 * 3. `has-narrative` 里的段级失败：逐段标注 + 按段重试。
 *
 * 演示态（`?demo=1`）不触发任何控制面动作：控制面是真实后端，不能让演示数据冒充生成结果。
 */

const NO_FAILURES: ReviewAiFailure[] = []

/** 段状态徽标 + 竖条色（UI-SPEC §4.5）；口径与每日复盘屏同源，见 `review/parse.ts`。 */
function sectionBadge(status: string): { label: string; tone: BadgeTone } {
  const meta = SECTION_STATUS[status] ?? SECTION_STATUS.pending
  return { label: meta.label, tone: meta.badge }
}

export default function AiReviewScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const demo = useReviewDemoEnabled()
  // 日期只跟 URL：本屏不写「我的判断」的草稿存储（那份按段对齐数据面，归 `/review`），
  // 两处各存一份当前交易日必然会漂移。
  const tradeDate = params.get('date') ?? (demo ? DEMO_REVIEW_DATE : localDateIso())

  const changeDate = (value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set('date', value)
    else next.delete('date')
    setParams(next, { replace: true })
  }

  const view = useAiReviewView({ date: tradeDate })
  const forced = view.forced
  const review = view.review
  const reloadAll = view.reloadAll
  const busy = review.status === 'loading'

  const [notice, setNotice] = useState<string | null>(null)

  // AI 生成 / 重试任务：POST 建任务 → 轮询 `/control/jobs/{id}` 直到 ok / failed
  const [aiJob, setAiJob] = useState<JobView | null>(null)
  const [aiJobError, setAiJobError] = useState<string | null>(null)
  const aiBusy = aiJob !== null

  const startAi = async (mode: 'generate' | 'retry', section?: string) => {
    // 演示态不触发任何写 / 任务入口：控制面是真实后端，不能让演示数据冒充生成结果
    if (demo) {
      setAiJobError('演示态不触发 AI 生成：本页数据来自 demo.ts，控制面不在演示链路上。')
      return
    }
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
              next.action === 'retry' ? '失败段已重试完成，段落已刷新' : 'AI 生成完成，段落已刷新',
            )
            review.reload()
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

  const reviewData = review.data?.payload.review ?? null
  const aiSections = useMemo(() => parseSections(reviewData), [reviewData])
  const header = useMemo(() => parseHeader(reviewData), [reviewData])
  const failures = useMemo(() => parseAiFailures(reviewData?.ai_failures), [reviewData])

  const hasArtifact = reviewData !== null
  const derived = useMemo<PageState>(() => {
    if (review.status === 'loading') return 'loading'
    if (review.status === 'error') {
      return isApiError(review.error) && review.error.kind === 'not-connected' ? 'offline' : 'error'
    }
    if (review.status === 'ready') {
      if (!hasArtifact) return 'empty'
      const tally = tallySections(parseSections(reviewData))
      return tally.failed + tally.degraded > 0 ? 'partial' : 'normal'
    }
    return 'loading'
  }, [review.status, review.error, reviewData, hasArtifact]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'
  // 生成 / 重试需要本机控制面可达；offline 只读，不假装能跑；演示态根本不接控制面
  const canRunAi = !demo && (usable || state === 'empty')
  const runAiDisabledReason = demo
    ? '演示态不触发 AI 生成：本页数据来自 demo.ts，不写后端也不建任务'
    : state === 'offline'
      ? '本地服务未连接，无法触发生成'
      : '取数未完成，暂不能触发生成'

  const viewSections = usable ? aiSections : []
  const viewHeader = usable ? header : null
  const viewFailures = useMemo(
    () => (usable ? failures : NO_FAILURES),
    [usable, failures],
  )
  const viewTally = useMemo(() => tallySections(viewSections), [viewSections])
  const firstFailedSection = viewSections.find((section) => section.status === 'failed')?.name ?? null
  const reviewCommand = `ldst review --date ${tradeDate}`

  /** AI复盘屏的三-case（`review/parse.ts: classifyAiCase`，判定口径只有一处）。 */
  const aiCase: AiReviewCase = classifyAiCase({
    hasArtifact: usable || state === 'empty',
    header: viewHeader ?? header,
    sections: viewSections,
    failures: viewFailures,
  })
  /** 未接入时后端把提示写进每段 `narrative`，取第一段就够——同一句话贴五遍不是信息。 */
  const unconfiguredHint = viewSections.find((section) => section.narrative.length > 0)?.narrative[0] ?? null

  const sectionMap = useMemo(() => {
    const map = new Map<string, ReviewSectionView>()
    viewSections.forEach((section) => map.set(section.name, section))
    return map
  }, [viewSections])

  const failureMap = useMemo(() => {
    const map = new Map<string, ReviewAiFailure>()
    viewFailures.forEach((failure) => map.set(failure.section, failure))
    return map
  }, [viewFailures])

  const summary = useMemo(() => {
    if (state === 'offline') return '本地服务未连接 · AI 段落只读，无法触发生成'
    if (state === 'error') return '复盘正文取数失败'
    if (state === 'empty') return `${tradeDate} 还没有复盘产物 · 运行 ${reviewCommand}`
    if (state === 'partial') {
      return `${tradeDate} · 五段中 ${formatInt(viewTally.failed)} 段失败 / ${formatInt(viewTally.degraded)} 段降级`
    }
    if (demo) return `${tradeDate} · 演示数据（非真实行情）· 不触发控制面`
    if (aiCase === 'model-unconfigured') return `${tradeDate} · 产物在，但五段都没跑过模型`
    return `${tradeDate} · 模型 ${viewHeader?.aiModel ?? MISSING} · 生成于 ${formatTime(viewHeader?.generatedAt ?? null)}`
  }, [state, tradeDate, viewTally.failed, viewTally.degraded, viewHeader, reviewCommand, demo, aiCase])

  const copyCommand = () => {
    const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
    if (!clipboard?.writeText) {
      setNotice('当前浏览器不允许自动复制，请手动选中命令复制。')
      return
    }
    void clipboard
      .writeText(reviewCommand)
      .then(() => setNotice('命令已复制，在终端执行后回到本页点「重新检查」。'))
      .catch(() => setNotice('复制失败，请手动选中命令复制。'))
  }

  const focusSection = (name: string) => {
    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    document
      .getElementById(`review-ai-section-${name}`)
      ?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' })
  }

  const pageHeader = (
    <PageHeader
      title="AI复盘"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      refresh={{
        onClick: reloadAll,
        loading: busy && state !== 'loading',
        disabled: state === 'loading',
      }}
      actions={
        <>
          <Button
            variant="primary"
            loading={aiBusy}
            disabled={!canRunAi}
            disabledReason={canRunAi ? undefined : runAiDisabledReason}
            onClick={() => void startAi('generate')}
          >
            {aiBusy
              ? 'AI 生成中…'
              : viewFailures.some((failure) => isRetryableFailure(failure.kind))
                ? '重试失败段'
                : '重新生成'}
          </Button>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {pageHeader}
        <p className="text-caption text-text-muted" role="status">
          五段 AI 叙述取数中。程序指标与「我的判断」在每日复盘屏，本屏不重复取。
        </p>
        <ScreenSkeleton metrics={0} rows={5} />
      </div>
    )
  }

  if (state === 'error') {
    const errorInfo = review.error
      ? describeApiError(review.error)
      : {
          title: 'AI 复盘取数失败',
          detail: '复盘接口没有返回可用数据。恢复后点「重试取数」；本屏不编造任何段落。',
          code: 'DEMO · ?state=error',
        }
    return (
      <div className="flex flex-col gap-5">
        {pageHeader}
        <ErrorBlock {...errorInfo} onRetry={reloadAll} retryLabel="重试取数" />
      </div>
    )
  }

  const generateDisabled = !canRunAi
  const generateDisabledReason = runAiDisabledReason

  return (
    <div className="flex flex-col gap-5">
      {pageHeader}

      {state === 'offline' && <OfflineBanner onRetry={reloadAll} />}

      {/* 未接入由下面专门的 case 2 横幅一次讲清，不再叠一条「几段降级」的泛化统计 */}
      {state === 'partial' && aiCase !== 'model-unconfigured' && viewTally.failed + viewTally.degraded > 0 && (
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
          每段已单独标注：失败段可就地重跑，降级段保留降级说明；其余段落照常渲染。
          这只影响本屏——五段的程序指标与「我的判断」草稿都不受 AI 成败影响。
        </Banner>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Field label="交易日" htmlFor="review-ai-date" className="w-44">
          <Input
            id="review-ai-date"
            type="date"
            value={tradeDate}
            onChange={(event) => changeDate(event.target.value)}
          />
        </Field>
        <Badge tone={viewHeader?.aiDegraded ? 'warn' : 'neutral'}>
          模型 {viewHeader?.aiModel ?? (aiCase === 'model-unconfigured' ? '未接入' : MISSING)}
        </Badge>
        {/* 读的是同一份产物的外壳字段（§5.1/§5.5）：这条 AI 正文取自实时取数还是缓存产物 */}
        <SourceBadges envelope={review.data} />
        {viewHeader?.generatedAt && (
          <Badge tone="neutral">产物生成于 {formatTime(viewHeader.generatedAt)}</Badge>
        )}
        <div className="grow" />
        <LinkButton to="/review">每日复盘（数据与我的判断）</LinkButton>
      </div>

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

      {/* case 1：当天复盘产物本身不存在——是数据没生成，不是「AI 功能缺失」 */}
      {state === 'empty' && (
        <SectionCard
          title="这一天还没有复盘产物"
          subtitle={`AI复盘与每日复盘同源：都读 review/${tradeDate}.json · 只读`}
          bodyClassName="p-0"
          actions={<Badge tone="neutral">待生成</Badge>}
        >
          <EmptyState
            icon={<Bot aria-hidden="true" />}
            title={`${tradeDate} 还没有复盘产物`}
            description="AI 段落写在复盘产物里，产物还没有就没有可生成的正文。先在终端运行下面这条命令生成五段复盘，落盘后回到本页点「重新检查」；本屏的「重新生成」只负责让模型写叙述，不代替产物生成。"
            action={
              <div className="flex w-full max-w-measure flex-col items-center gap-2">
                <code className="ldst-num w-full break-all rounded border border-border bg-surface-3 px-3 py-2 text-left text-caption text-text-secondary">
                  {reviewCommand}
                </code>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={copyCommand}
                    leadingIcon={<Copy className="size-3.5" aria-hidden="true" />}
                  >
                    复制命令
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

      {/* case 2：产物在、模型未接入——明确说「数据没缺」，不允许显示成数据缺失 */}
      {usable && aiCase === 'model-unconfigured' && (
        <Banner
          tone="warn"
          title="模型未接入：五段都没有跑过模型"
          action={
            <>
              <Button variant="secondary" onClick={() => navigate('/settings?tab=ai')}>
                接入 AI
              </Button>
              <Button
                variant="primary"
                loading={aiBusy}
                disabled={generateDisabled}
                disabledReason={generateDisabled ? generateDisabledReason : undefined}
                onClick={() => void startAi('generate')}
              >
                {aiBusy ? 'AI 生成中…' : '接入后重新生成'}
              </Button>
            </>
          }
        >
          这里缺的是模型，不是数据：当天的五段程序指标都在每日复盘{' '}
          <code className="ldst-num">/review</code>
          ，与模型是否接入无关。后端在未配置凭据时把每段落成「降级完成」，提示为：
          {unconfiguredHint ? ` ${unconfiguredHint}` : ' 未配置模型环境变量，不发起任何请求。'}
          凭据写入本机 <code className="ldst-num">config/ai.env</code>，只写不读回。
        </Banner>
      )}

      <div className="flex flex-col gap-4">
        {REVIEW_SECTIONS.map((section, index) => {
          const payloadName = SECTION_PAYLOAD_NAME[section.key]
          const payload = sectionMap.get(payloadName)
          const failure = failureMap.get(payloadName)
          const status = payload?.status ?? 'pending'
          const badge = sectionBadge(status)
          const narrated = (payload?.narrative.length ?? 0) > 0
          const showNoticeOnly = aiCase === 'model-unconfigured'
          return (
            <div key={section.key} id={`review-ai-section-${payloadName}`} className="scroll-mt-6">
              <SectionCard
                tone={SECTION_STATUS[status]?.tone ?? 'neutral'}
                title={
                  <span>
                    <span className="ldst-num mr-2 text-caption text-text-muted">{index + 1}</span>
                    {section.label}
                  </span>
                }
                subtitle={payload?.title ?? section.hint}
                actions={
                  <>
                    <Badge tone={badge.tone}>{badge.label}</Badge>
                    {payload?.degraded && <Badge tone="warn">降级</Badge>}
                    {status === 'failed' && (!failure || isRetryableFailure(failure.kind)) && (
                      <Button
                        variant="secondary"
                        onClick={() => void startAi('retry', payloadName)}
                        loading={aiBusy}
                        disabled={generateDisabled}
                        disabledReason={
                          generateDisabled
                            ? generateDisabledReason
                            : undefined
                        }
                      >
                        {aiBusy ? '重跑中…' : '重跑该段'}
                      </Button>
                    )}
                  </>
                }
              >
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-caption font-medium text-text-secondary">AI 叙述</span>
                    {/* 模型名只在本屏出现：数据面不显示任何模型信息（DEC-049 §2） */}
                    <span
                      className="min-w-0 truncate text-caption text-text-muted"
                      title={`模型 ${payload?.model ?? '未启用'}`}
                    >
                      模型 {payload?.model ?? '未启用'}
                    </span>
                  </div>

                  {state === 'empty' ? (
                    <p className="text-body leading-body text-text-muted">
                      该段还没有复盘产物（待生成）。运行 <code className="ldst-num">{reviewCommand}</code> 生成五段复盘后，本段才有 AI 正文可写。
                    </p>
                  ) : status === 'running' ? (
                    <SkeletonText lines={3} />
                  ) : failure ? (
                    <div className="flex flex-col gap-2">
                      <AiErrorNotice
                        kind={failure.kind}
                        section={section.label}
                        message={failure.message}
                        action={failure.action}
                        keptPrevious={narrated}
                        retryLabel={aiBusy ? 'AI 生成中…' : '重试该段'}
                        onRetry={canRunAi ? () => void startAi('retry', failure.section) : undefined}
                        onOpenStatus={() => navigate('/settings?tab=ai')}
                      />
                      {payload && narrated && (
                        <div className="flex flex-col gap-2">
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
                          'AI 未返回内容；程序指标与「我的判断」不受影响，在每日复盘屏照常可读。'
                        }
                        onRetry={canRunAi ? () => void startAi('retry', payloadName) : undefined}
                        retryLabel={aiBusy ? 'AI 重跑中…' : '重跑该段'}
                      />
                      <p className="text-caption text-text-muted">
                        可重跑：只重跑本段，其余失败段与已完成段落保留；也可运行{' '}
                        <code className="ldst-num">{reviewCommand}</code> 整份重生成。
                      </p>
                    </div>
                  ) : showNoticeOnly ? (
                    <div className="flex flex-col gap-2">
                      <AiErrorNotice
                        kind="degraded"
                        section={section.label}
                        message={(payload?.notes ?? []).join(' ') || '未配置模型凭据，本段没有 AI 正文。'}
                        action="接入模型后点右上「重新生成」，或只重跑本段。"
                        onOpenStatus={() => navigate('/settings?tab=ai')}
                      />
                      {payload && narrated && (
                        <p className="text-caption leading-body text-text-muted">
                          产物里这段存的是后端的未配置提示：<span className="ldst-num">{payload.narrative[0]}</span>
                        </p>
                      )}
                    </div>
                  ) : payload && narrated ? (
                    <div className="flex flex-col gap-2">
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
                      生成五段复盘，或点右上「重新生成」。程序指标与「我的判断」在每日复盘屏，不受本段影响。
                    </p>
                  )}
                </div>
              </SectionCard>
            </div>
          )
        })}
      </div>

      <p className="text-caption text-text-muted">
        本屏只呈现模型产物与它的成败：段落状态、降级原因、失败类别都只信后端字段，缺失以 {MISSING} 标注，
        不补 0、不编造。当日五段程序指标与「我的判断」草稿在 <LinkButton to="/review" className="inline">每日复盘</LinkButton>。
      </p>
    </div>
  )
}
