import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FileText, RefreshCw, Trash2 } from 'lucide-react'

import { describeApiError, isApiError, type SentimentResult } from '../api'
import { resolveState, type PageState } from '../lib/pageState'
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
  SourceBadges,
  Textarea,
  type BadgeTone,
} from '../components/ui'
import { LinkButton } from '../components/research/bits'
import { KV_STORE, idbGet, idbSet } from '../../persist/indexeddb'
import { REVIEW_SECTIONS, draftCharCount, selectSlot, useReviewDraft } from '../../stores/reviewDraft'
import { useReviewDemoEnabled, useReviewView } from './review/useReviewView'
import { DEMO_REVIEW_DATE } from './review/demo'
import { SECTION_PAYLOAD_NAME, parseHeader, parseSections, type ReviewSectionView } from './review/parse'
import SectionMetrics from './review/SectionMetrics'

/**
 * 每日复盘（数据面，`/review`，UI-SPEC §3.1 / QUOT-344-S2 / DEC-049）。
 *
 * 一屏三层里的**数据**那两层：3 张指标卡（程序直出，不可编辑）+ 五段固定顺序
 * （情绪→资金→题材→龙虎榜→龙头）的程序指标，加「我的判断」手写草稿（IndexedDB）。
 *
 * 这一屏**不出现 AI**：没有 `narrative`、没有模型名、没有段级 `status`、没有
 * 「重新生成 / 重试失败段」。那些是 AI复盘 `/review/ai` 的内容（DEC-049 §2），
 * 两屏读同一份 `/api/review` 产物，各取一半。
 *
 * 「我的判断」归这一屏：它是人写的、按段对齐数据，不是模型产物（DEC-049 §3 裁定），
 * AI复盘屏不写第二份草稿存储。
 *
 * 演示源（`?demo=1` 或设置页「虚拟数据」）只替换两条读接口，且**关掉写入口**：
 * 「我的判断」不写入本机草稿、不由演示数据预填——隐私内容不能靠演示数据冒充。
 * 真实请求失败绝不回落到 `review/demo.ts`。
 *
 * 任何缺失值显示 `—` + 原因，不补 0、不编造（QUOT-319）。
 */

const NO_SECTIONS: ReviewSectionView[] = []

/** 段级数据态：只看程序指标有没有落盘，与 AI 段落状态无关。 */
function sectionDataState(section: ReviewSectionView | undefined): {
  label: string
  tone: BadgeTone
} {
  if (!section) return { label: '待生成', tone: 'neutral' }
  if (section.metrics.available === false) return { label: '缺数', tone: 'warn' }
  return { label: '程序直出', tone: 'ok' }
}

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

/** §4.1 草稿时间口径：当日 `HH:mm`，跨日 `MM-DD HH:mm`（不是 `formatTime` 的秒级）。 */
function formatDraftTime(iso: string | null): string {
  if (!iso) return MISSING
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const today = new Date()
  const sameDay =
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate()
  return sameDay
    ? clock
    : `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`
}

/** §4.1 字数口径：去空白后四舍五入到个位、千分位。 */
function formatCharCount(value: number): string {
  return Math.round(value).toLocaleString('zh-CN')
}

/**
 * 「本机恢复出来的草稿」长什么样，按当前 URL 的那一个交易日算。
 * 快照取的是当帧时刻的分片内容，用来把「恢复的旧笔记」和「本次新写的」分开。
 */
function draftSnapshot(date: string) {
  const slot = selectSlot(useReviewDraft.getState(), date)
  const filled = REVIEW_SECTIONS.filter((section) => slot.sections[section.key].trim().length > 0)
  return {
    chars: draftCharCount(slot.sections),
    updatedAt: slot.updatedAt,
    labels: filled.map((section) => section.label),
    firstKey: filled[0]?.key,
  }
}

export default function ReviewScreen() {
  const [params, setParams] = useSearchParams()
  const demo = useReviewDemoEnabled()

  // 交易日只认 URL（QUOT-354 BUG-B）：草稿分片里不再存日期，`?date=` 深链优先于一切本机状态。
  // 演示态默认落在事实基线的交易日（同 `limitup` 屏口径），避免出现「日期 11-02 / 正文 10-30」的错位
  const tradeDate = params.get('date') ?? (demo ? DEMO_REVIEW_DATE : localDateIso())
  const setTradeDate = (date: string | null) => {
    const merged = new URLSearchParams(params)
    if (date) merged.set('date', date)
    else merged.delete('date')
    setParams(merged, { replace: true })
  }

  // 草稿按交易日分槽（QUOT-354 BUG-C）：切日期只换这一天的五段正文，不会覆盖另一天的笔记
  const slot = useReviewDraft((state) => selectSlot(state, tradeDate))
  const updatedAt = slot.updatedAt
  const draftSections = slot.sections
  const setSection = useReviewDraft((state) => state.setSection)
  const setActiveSection = useReviewDraft((state) => state.setActiveSection)
  const clearDate = useReviewDraft((state) => state.clearDate)

  const view = useReviewView({ date: tradeDate })
  const forced = view.forced
  const review = view.review
  const sentiment = view.sentiment
  const requests = [review, sentiment]
  const reloadAll = view.reloadAll
  const busy = requests.some((request) => request.status === 'loading')

  // 首帧快照：区分「本机恢复的草稿」与「本次新写的笔记」；换交易日时按新的那一天重新快照
  const [restored, setRestored] = useState(() => draftSnapshot(tradeDate))
  const restoredDate = useRef(tradeDate)
  if (restoredDate.current !== tradeDate) {
    restoredDate.current = tradeDate
    setRestored(draftSnapshot(tradeDate))
  }

  const total = useMemo(() => draftCharCount(draftSections), [draftSections])

  const [confirmReset, setConfirmReset] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [cached, setCached] = useState<ReviewCache | null>(null)

  // 离线回放：只读取本机 IndexedDB 里的复盘快照（程序指标 + 正文），与 AI 屏共用同一份缓存键
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
    // 演示数据不写本机离线回放缓存：否则断网时会把 demo.ts 当成真实快照回放
    if (demo || review.status !== 'ready') return
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
  }, [demo, review.status, review.data, sentiment.status, sentiment.data, tradeDate])

  const reviewData = review.data?.payload.review ?? null
  const dataSections = useMemo(() => parseSections(reviewData), [reviewData])
  const header = useMemo(() => parseHeader(reviewData), [reviewData])
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
      // 数据面的部分失败主场景：某段数据集没落盘（`available: false`）即进入 partial，逐段标注
      const missing = parseSections(reviewData).filter((section) => section.metrics.available === false)
      return missing.length > 0 ? 'partial' : 'normal'
    }
    return 'loading'
  }, [review.status, sentiment.status, reviewData]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'

  // 离线回放：只用本机 IndexedDB 快照，日期不符就不冒充当天数据
  const replay = state === 'offline' && cached && cached.date === tradeDate ? cached : null
  const viewSections = useMemo(
    () => (usable ? dataSections : replay ? parseSections(replay.review) : NO_SECTIONS),
    [usable, dataSections, replay],
  )
  const viewHeader = usable ? header : replay ? parseHeader(replay.review) : null
  const viewSent = usable ? sent : replay?.sentiment ?? null
  const missingNames = useMemo(
    () => viewSections.filter((section) => section.metrics.available === false).map((section) => section.name),
    [viewSections],
  )

  const sectionMap = useMemo(() => {
    const map = new Map<string, ReviewSectionView>()
    viewSections.forEach((section) => map.set(section.name, section))
    return map
  }, [viewSections])

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
    if (state === 'empty') return `${tradeDate} 尚未生成复盘`
    if (state === 'partial') {
      if (failedLabels.length > 0) return `${tradeDate} · 部分数据源不可用`
      return `${tradeDate} · 五段中 ${formatInt(missingNames.length)} 段缺数`
    }
    if (demo) return `${tradeDate} · 演示数据（非真实行情）· 不写本机草稿`
    return `${tradeDate} · 草稿 ${formatInt(total)} 字 · 最后写入 ${formatTime(updatedAt)}`
  }, [state, tradeDate, total, updatedAt, failedLabels.length, missingNames.length, replay, demo])

  const handleReset = () => {
    clearDate(tradeDate)
    setConfirmReset(false)
    setRestored(draftSnapshot(tradeDate))
    setNotice(`${tradeDate} 的本机复盘草稿已清空`)
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

  const pageHeader = (
    <PageHeader
      title="每日复盘"
      subtitle={summary}
      badge={<DemoStateBadge state={forced} />}
      refresh={{
        onClick: reloadAll,
        loading: busy && state !== 'loading',
        disabled: state === 'loading',
      }}
      actions={
        <>
          {/* 模型面的内容不在这屏，给一个明确出口，避免用户在两屏间来回找「重新生成」 */}
          <LinkButton to="/review/ai">AI复盘</LinkButton>
        </>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {pageHeader}
        <p className="text-caption text-text-muted" role="status">
          程序指标计算中。
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
          code: null,
        }
    return (
      <div className="flex flex-col gap-5">
        {pageHeader}
        {/* 文案写的是「重试取数」，按钮就得真叫这个名字（QUOT-354 BUG-D：默认标签「重试」与文案不一致） */}
        <ErrorBlock {...errorInfo} onRetry={reloadAll} retryLabel="重试取数" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {pageHeader}

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
          本机服务未连接，IndexedDB 里也没有 {tradeDate} 的复盘快照。恢复连接后点「刷新」。
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
      {state === 'partial' && failedLabels.length === 0 && missingNames.length > 0 && (
        // §5.2/§5.3：数据集缺项 = `partial` → danger，色序不得反转（R-12 的原始缺陷就在这条横幅上）
        <Banner
          tone="danger"
          title={`五段中 ${formatInt(missingNames.length)} 段数据缺失`}
          action={
            <Button variant="secondary" onClick={() => focusSection(missingNames[0])}>
              定位首个缺失段
            </Button>
          }
        >
          缺的是本机当天该数据集的产物，不是模型：每个缺失段单独标注原因，段内计数整列按 {MISSING} 显示，
          其余段落照常渲染。补数后回到本页点「刷新」即可；也可以点右上角「本页数据同步」立即开始。
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
        <Badge tone="neutral">{tradeDate} 草稿 {formatInt(total)} 字</Badge>
        <Badge tone="neutral">最后写入 {formatTime(updatedAt)}</Badge>
        {viewHeader?.generatedAt && <Badge tone="neutral">复盘生成于 {formatTime(viewHeader.generatedAt)}</Badge>}
        <div className="grow" />
        <Button
          variant="secondary"
          onClick={() => setConfirmReset(true)}
          disabled={total === 0}
          leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
        >
          清空当日草稿
        </Button>
      </div>

      {restored.chars > 0 && (
        <Banner
          tone="info"
          title={`已恢复 ${tradeDate} 的草稿（${formatDraftTime(restored.updatedAt)}）`}
          action={
            <>
              {restored.firstKey && (
                <Button
                  variant="secondary"
                  onClick={() => focusSection(SECTION_PAYLOAD_NAME[restored.firstKey!])}
                >
                  查看改动
                </Button>
              )}
              <Button variant="secondary" onClick={() => setConfirmReset(true)}>
                清空当日草稿
              </Button>
            </>
          }
        >
          {restored.labels.join(' / ')} {formatCharCount(restored.labels.length)} 段，共{' '}
          <span className="ldst-num">{formatCharCount(restored.chars)}</span> 字。刷新、崩溃、升级后都会原样回填。
        </Banner>
      )}
      {notice && (
        <p className="text-caption text-text-secondary" role="status">
          {notice}
        </p>
      )}

      {state === 'empty' && (
        <SectionCard
          title="这一天还没有复盘产物"
          subtitle={`数据面：只读本机复盘产物 · ${tradeDate}`}
          bodyClassName="p-0"
          actions={<Badge tone="neutral">待生成</Badge>}
        >
          <EmptyState
            icon={<FileText aria-hidden="true" />}
            title={`${tradeDate} 尚未生成复盘`}
            description="打开本页会自动补取并生成五段复盘（情绪 / 资金 / 题材 / 龙虎榜 / 龙头）。也可以点右上角「本页数据同步」立即开始；完成后回到本页点「重新检查」。没有配置模型凭据只影响 AI复盘屏，不影响本屏。"
            action={
              <Button
                variant="secondary"
                onClick={reloadAll}
                leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
              >
                重新检查
              </Button>
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

      {/* `review.headline` 是 `pipeline._headline()` 用情绪 + 天梯直出的摘要，不是模型产物，
          所以留在数据面；此前它挂着「AI 摘要」的标签与模型名，是这次拆屏纠正的错标。 */}
      {viewHeader && viewHeader.headline.length > 0 && (
        <section className="rounded border border-border bg-surface-2 px-4 py-3" aria-label="当日摘要">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone="neutral">程序直出</Badge>
            <span className="text-caption text-text-muted">当日摘要</span>
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
          const dataState = sectionDataState(payload)
          const draftText = draftSections[section.key]
          const textareaId = `review-draft-${section.key}`
          return (
            <div key={section.key} id={`review-section-${payloadName}`} className="scroll-mt-6">
              <SectionCard
                tone={dataState.tone === 'warn' ? 'warn' : 'neutral'}
                title={
                  <span>
                    <span className="ldst-num mr-2 text-caption text-text-muted">{index + 1}</span>
                    {section.label}
                  </span>
                }
                subtitle={payload?.title ?? section.hint}
                actions={<Badge tone={dataState.tone}>{dataState.label}</Badge>}
              >
                <div className="grid gap-4 desk:grid-cols-[2fr_3fr]">
                  <div className="rounded border border-border bg-surface-2 p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <span className="text-caption font-medium text-text-secondary">程序指标</span>
                      <span className="text-caption text-text-muted">不可编辑</span>
                    </div>
                    <SectionMetrics sectionName={payloadName} metrics={payload?.metrics ?? {}} />
                  </div>

                  <div className="flex flex-col gap-1">
                    <Field
                      label={`我的判断 · ${section.label}`}
                      htmlFor={textareaId}
                      hint={
                        demo
                          ? '演示态只读：演示数据不写入本机草稿，也不冒充你的判断。关开关后恢复可写。'
                          : '自动存本机（IndexedDB），刷新、崩溃、升级后原样回填；不联网、不上传。'
                      }
                    >
                      <Textarea
                        id={textareaId}
                        value={demo ? '' : draftText}
                        readOnly={demo}
                        aria-readonly={demo}
                        placeholder={demo ? '演示态不填写判断（内容不会落本机）' : `写今天的${section.label}判断…`}
                        onChange={(event) => {
                          if (demo) return
                          setSection(tradeDate, section.key, event.target.value)
                        }}
                        onFocus={() => setActiveSection(section.key)}
                      />
                    </Field>
                    <p className="self-end text-caption text-text-muted">
                      {formatInt(demo ? 0 : draftText.trim().length)} 字
                    </p>
                  </div>
                </div>
              </SectionCard>
            </div>
          )
        })}
      </div>

      <p className="text-caption text-text-muted">
        指标口径：情绪温度 0–100（50 中性）；晋级率为昨日涨停今日晋级比例；梯队断层统计断档档数。
        指标由程序直出，不可编辑；缺失显示 {MISSING} 并注明原因。AI 叙述与逐段生成状态在{' '}
        <LinkButton to="/review/ai" className="inline">
          AI复盘
        </LinkButton>
        。
      </p>

      <ConfirmDialog
        open={confirmReset}
        title={`清空 ${tradeDate} 的本机复盘草稿？`}
        description="这一天的五段正文会一并清除，该操作不可撤销；其它交易日的草稿不受影响，也只影响本机 IndexedDB，不动后端已生成的复盘。"
        confirmLabel="清空当日草稿"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={handleReset}
      />
    </div>
  )
}
