import { useEffect, useMemo, useRef, useState } from 'react'
import { Copy, Download, GitCompareArrows, Share2 } from 'lucide-react'

import {
  RESEARCH_STAGES,
  STAGE_STATUS_LABEL,
  STAGE_TITLE,
  describeResearchError,
  loadResearchRuns,
  stageOf,
  type ResearchOverview,
  type ResearchRunList,
  type ResearchSource,
  type ResearchStatus,
} from '../../../api/research'
import {
  MAX_SECTION_LINES,
  describeStageKind,
  diffStage,
  summarizeStages,
  type DiffKind,
  type SectionDiff,
  type StageDiff,
  type StageSnapshot,
  type ValueDiff,
} from '../../../lib/reportDiff'
import {
  SHARE_CARD_SIZE,
  buildShareCard,
  drawShareCard,
  shareStatement,
  shareSummary,
  type ShareCardData,
  type ShareLayout,
} from '../../../lib/shareCard'
import { MISSING, formatTime } from '../../../lib/format'
import { cn } from '../../../lib/cn'
import {
  Badge,
  Button,
  EmptyState,
  ErrorBlock,
  SectionCard,
  Segmented,
  Select,
  SkeletonText,
  type BadgeTone,
} from '../../ui'
// 标签表与「md 缺失时按叙述拼一份」的口径只有 facts.ts 一处（研究报告页同一份），
// 对比块必须复用，不能再写第二套中文标签。
import { artifactMarkdown, scalarFacts } from '../../../screens/research/facts'

/**
 * 区块 I：报告对比 + 分享卡（QUOT-453）。
 *
 * 参考库是 `ReportCompare` + `RunCompareCard` + `ShareCardPanel` 三件，落在研究报告
 * 之后的同一张卡上，所以这里合成一个文件、两个挂载点：
 *
 * 1. **报告对比**：在同一标的的历史运行（`GET /api/research/{code}/runs` 给日期）里挑
 *    两次，逐段并列「写了什么」——段落按 `## ` 标题对齐做行级差异，关键数据按标签对齐
 *    比展示值。差异分成 改了 / 本次新增 / 本次没有 / 没变 四类，「这条事实不见了」
 *    不会被读成噪音。只并列，不解读涨跌，也不计算任何新数字。
 * 2. **分享卡**：把本页已取到的行情 / K 线区间 / 财务读数画成 PNG，或复制成文字；
 *    缺的字段保留 `—` 并进「没取到的」，不含论点正文、持仓等私有内容。
 *
 * 三态各自有明确文案：接口没接（错误 + 重试）、只有一次运行（说「还没有可比的第二次」）、
 * 选中那次没产物（点名是哪一次没取到）。演示模式（`?demo=1`）不读真实运行清单。
 */

export interface CompareSectionProps {
  code: string | null
  reportHref: string | null
  /** 页面同一个取数通道（真实 HTTP 或演示源），两次产物都从这里读 */
  source: ResearchSource
  /** 页面当前日期：演示模式用它造出可比的第二次，真实模式只作默认值参考 */
  date: string
  demo: boolean
  /** 分享卡的数字来源：概览接口那份，页面正在渲染什么就画什么 */
  overview: ResearchOverview | null
  name: string | null
  className?: string
  /** 运行清单通道：单测注入替身用 */
  runsLoader?: (code: string, signal?: AbortSignal) => Promise<ResearchRunList>
}

const KIND_TONE: Record<DiffKind, BadgeTone> = {
  same: 'neutral',
  changed: 'warn',
  added: 'info',
  removed: 'danger',
}

const OPS_MAX = 40

function shiftDay(iso: string, days: number): string {
  const stamp = Date.parse(`${iso}T00:00:00Z`)
  if (!Number.isFinite(stamp)) return iso
  const moved = new Date(stamp + days * 86400000)
  return moved.toISOString().slice(0, 10)
}

/** 演示模式没有盘上历史：用演示日期与它的前一天造两次，产物仍由演示源给同一份。 */
function demoRunList(code: string, date: string): Promise<ResearchRunList> {
  const dates = [date, shiftDay(date, -1)]
  return Promise.resolve({ code, dates, latest: dates[0], count: dates.length })
}

function snapshotOf(status: ResearchStatus, stage: (typeof RESEARCH_STAGES)[number]): StageSnapshot | null {
  const item = stageOf(status, stage)
  const artifact = item.artifact
  if (!artifact) return null
  const values: Record<string, string> = {}
  for (const fact of scalarFacts(artifact.document.facts)) {
    values[fact.unit ? `${fact.label}（${fact.unit}）` : fact.label] = fact.value
  }
  return {
    name: stage,
    title: STAGE_TITLE[stage],
    // 段落里的状态说明要中文，不能把后端枚举原样端给用户（UI-SPEC §8）
    status: STAGE_STATUS_LABEL[item.status],
    markdown: artifactMarkdown(artifact.document, artifact.markdown),
    values,
  }
}

function buildStageDiffs(
  older: ResearchStatus | null,
  newer: ResearchStatus | null,
): StageDiff[] {
  const out: StageDiff[] = []
  for (const stage of RESEARCH_STAGES) {
    const diff = diffStage(
      older ? snapshotOf(older, stage) : null,
      newer ? snapshotOf(newer, stage) : null,
    )
    if (diff) out.push(diff)
  }
  return out
}

function ValueRow({ diff }: { diff: ValueDiff }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-border py-1.5 text-body last:border-0">
      <Badge tone={diff.kind === 'removed' ? 'danger' : diff.kind === 'added' ? 'info' : 'warn'}>
        {diff.kind === 'removed' ? '消失' : diff.kind === 'added' ? '新增' : '变了'}
      </Badge>
      <span className="text-text">{diff.label}</span>
      <span className="ldst-num ml-auto text-text-secondary">
        <span title={diff.before === null ? '旧的那次没有这项' : undefined}>
          {diff.before ?? MISSING}
        </span>
        <span className="px-1.5 text-text-muted">→</span>
        <span title={diff.after === null ? '新的那次没有这项' : undefined}>
          {diff.after ?? MISSING}
        </span>
      </span>
    </div>
  )
}

function SectionRow({ section }: { section: SectionDiff }) {
  const head = (
    <>
      <Badge tone={KIND_TONE[section.kind]}>{section.kind === 'same' ? '没变' : describeStageKind(section.kind)}</Badge>
      <span className={cn('text-body', section.kind === 'same' ? 'text-text-muted' : 'text-text')}>
        {section.title}
      </span>
      {section.kind !== 'same' && (
        <span className="ldst-num text-caption text-text-muted">
          {section.added > 0 ? `+${section.added} 行` : ''}
          {section.added > 0 && section.removed > 0 ? ' ' : ''}
          {section.removed > 0 ? `−${section.removed} 行` : ''}
        </span>
      )}
    </>
  )
  if (section.kind === 'same' || section.ops.length === 0) {
    return <li className="flex flex-wrap items-baseline gap-2 py-1">{head}</li>
  }
  const hidden = section.ops.filter((op) => op.kind !== 'same').length - OPS_MAX
  return (
    <li className="py-1">
      <details>
        <summary className="flex cursor-pointer flex-wrap items-baseline gap-2 text-body">{head}</summary>
        <ul className="mt-1.5 border-l border-border pl-3 text-caption leading-body">
          {section.ops
            .filter((op) => op.kind !== 'same')
            .slice(0, OPS_MAX)
            .map((op, index) => (
              <li
                key={`${op.kind}-${index}`}
                className={op.kind === 'add' ? 'text-text' : 'text-text-muted'}
              >
                <span
                  className={cn('mr-1.5 inline-block w-3 font-mono', op.kind === 'add' ? 'text-accent' : 'text-text-muted')}
                  aria-label={op.kind === 'add' ? '新的那次' : '旧的那次'}
                >
                  {op.kind === 'add' ? '+' : '−'}
                </span>
                {op.text || '（空行）'}
              </li>
            ))}
          {section.truncated && (
            <li className="text-text-muted">
              这一节超过 {MAX_SECTION_LINES} 行，只比对了前 {MAX_SECTION_LINES} 行。
            </li>
          )}
          {hidden > 0 && <li className="text-text-muted">还有 {hidden} 行没列出</li>}
        </ul>
      </details>
    </li>
  )
}

function StageBlock({ diff }: { diff: StageDiff }) {
  const changedSections = diff.sections.filter((section) => section.kind !== 'same')
  return (
    <section className="border-t border-border pt-3 first:border-0 first:pt-0">
      <h4 className="flex flex-wrap items-baseline gap-2 text-body font-medium">
        <Badge tone={KIND_TONE[diff.kind]}>{diff.kind === 'same' ? '没变' : describeStageKind(diff.kind)}</Badge>
        <span className="text-text">{diff.title}</span>
        {diff.values.length > 0 && (
          <span className="ldst-num text-caption font-normal text-text-muted">
            关键数据 变了 {diff.values.filter((item) => item.kind === 'changed').length} · 新增{' '}
            {diff.values.filter((item) => item.kind === 'added').length} · 消失{' '}
            {diff.values.filter((item) => item.kind === 'removed').length}
          </span>
        )}
      </h4>
      {diff.notes.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1 text-caption leading-body text-text-muted">
          {diff.notes.map((note) => (
            <li key={note}>· {note}</li>
          ))}
        </ul>
      )}
      {diff.values.length > 0 && (
        <div className="mt-2">
          {diff.values.map((item) => (
            <ValueRow key={item.label} diff={item} />
          ))}
        </div>
      )}
      {diff.kind === 'same' ? (
        <p className="mt-1.5 text-caption text-text-muted">段落与关键数据两次一致。</p>
      ) : (
        <>
          {changedSections.length === 0 && diff.values.length > 0 && (
            <p className="mt-1.5 text-caption text-text-muted">段落文字没有出入，只有上面的读数变了。</p>
          )}
          {diff.sections.length > 0 && (
            <ul className="mt-1.5 divide-y divide-border">
              {diff.sections.map((section, index) => (
                <SectionRow key={`${section.title}-${index}`} section={section} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

interface PeerState {
  loading: boolean
  error: string | null
  older: ResearchStatus | null
  newer: ResearchStatus | null
}

function ShareCardBlock({ data }: { data: ShareCardData }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [layout, setLayout] = useState<ShareLayout>('portrait')
  const [note, setNote] = useState<string | null>(null)
  const summary = useMemo(() => shareSummary(data), [data])

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context) return
    const size = SHARE_CARD_SIZE[layout]
    element.width = size.width
    element.height = size.height
    drawShareCard(context, data, layout)
  }, [data, layout])

  const download = () => {
    const element = canvas.current
    if (!element) return
    element.toBlob((blob) => {
      if (!blob) {
        setNote('图片没生成出来（浏览器拒绝导出画布），可以改用下面的文字版。')
        return
      }
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `研究分享卡-${data.code}-${layout === 'portrait' ? '竖版' : '横版'}.png`
      link.click()
      URL.revokeObjectURL(url)
      setNote(`已导出 ${link.download}`)
    }, 'image/png')
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary.join('\n'))
      setNote('文字版已复制到剪贴板')
    } catch {
      // 局域网 http 打开时浏览器不给写剪贴板：下面的文字版可以手动选中复制
      setNote('浏览器没让写剪贴板（非安全上下文），可以在下面「文字版」里手动选中复制。')
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          ariaLabel="分享卡版式"
          size="md"
          value={layout}
          onChange={setLayout}
          options={[
            { value: 'portrait', label: '竖版（手机）' },
            { value: 'landscape', label: '横版' },
          ]}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            size="md"
            variant="secondary"
            leadingIcon={<Download className="size-3.5" aria-hidden="true" />}
            onClick={download}
          >
            下载图片
          </Button>
          <Button
            size="md"
            variant="secondary"
            leadingIcon={<Copy className="size-3.5" aria-hidden="true" />}
            onClick={() => void copy()}
          >
            复制文字
          </Button>
        </div>
      </div>
      <p className="text-caption leading-body text-text-muted">
        卡片只放本页已取到的原始读数、区间与缺口；{shareStatement()} 论点正文与持仓不进卡片。
      </p>
      {note && <p role="status" className="text-caption text-text-secondary">{note}</p>}
      <div className="flex justify-center rounded border border-border bg-surface-2 p-2">
        <canvas
          ref={canvas}
          aria-label={`研究分享卡预览（${layout === 'portrait' ? '竖版' : '横版'}）`}
          className={layout === 'portrait' ? 'h-auto w-full max-w-share-portrait' : 'h-auto w-full max-w-share-landscape'}
        />
      </div>
      <details className="text-caption">
        <summary className="cursor-pointer text-text-muted">文字版</summary>
        <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-border bg-surface-2 p-2 leading-body text-text-secondary">
          {summary.join('\n')}
        </pre>
      </details>
    </div>
  )
}

export function CompareSection({
  code,
  reportHref,
  source,
  date,
  demo,
  overview,
  name,
  className,
  runsLoader,
}: CompareSectionProps) {
  // 稳定引用：这个函数在下面的 effect 依赖里，每次渲染新建一个会让取清单的循环停不下来
  const loadRuns = useMemo(
    () => runsLoader ?? (demo ? (target: string) => demoRunList(target, date) : loadResearchRuns),
    [runsLoader, demo, date],
  )
  const [runs, setRuns] = useState<ResearchRunList | null>(null)
  const [runsError, setRunsError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const [olderDay, setOlderDay] = useState<string | null>(null)
  const [newerDay, setNewerDay] = useState<string | null>(null)
  const [peer, setPeer] = useState<PeerState>({ loading: false, error: null, older: null, newer: null })
  const seq = useRef(0)

  useEffect(() => {
    if (!code) {
      setRuns(null)
      setRunsError(null)
      return
    }
    let alive = true
    setRuns(null)
    setRunsError(null)
    loadRuns(code)
      .then((list) => {
        if (!alive) return
        setRuns(list)
        const days = list.dates
        setNewerDay(days[0] ?? null)
        setOlderDay(days[1] ?? null)
      })
      .catch((error: unknown) => {
        if (alive) setRunsError(error)
      })
    return () => {
      alive = false
    }
    // `date` / `demo` 决定演示清单；`attempt` 是手动重试
  }, [code, demo, date, attempt, loadRuns])

  useEffect(() => {
    if (!code || !olderDay || !newerDay) {
      setPeer({ loading: false, error: null, older: null, newer: null })
      return
    }
    const mine = ++seq.current
    setPeer({ loading: true, error: null, older: null, newer: null })
    void Promise.allSettled([
      source.status(code, olderDay),
      source.status(code, newerDay),
    ]).then(([older, newer]) => {
      if (mine !== seq.current) return
      const reason = (result: PromiseSettledResult<ResearchStatus>): string | null => {
        if (result.status === 'fulfilled') return null
        return describeResearchError(result.reason).detail
      }
      const olderError = reason(older)
      const newerError = reason(newer)
      setPeer({
        loading: false,
        error:
          olderError && newerError
            ? `两次产物都没取到：${olderError}`
            : olderError || newerError
              ? `${olderError ? olderDay : newerDay} 那一次没取到：${olderError ?? newerError}`
              : null,
        older: older.status === 'fulfilled' ? older.value : null,
        newer: newer.status === 'fulfilled' ? newer.value : null,
      })
    })
  }, [code, olderDay, newerDay, source])

  const shareData = useMemo(
    () => (code ? buildShareCard({ code, name, date, overview }) : null),
    [code, name, date, overview],
  )

  if (!code) {
    return (
      <div className={className}>
        <SectionCard title="报告对比" subtitle="同一只股票两次研究的关键数据逐条对齐">
          <EmptyState
            compact
            icon={<GitCompareArrows aria-hidden="true" />}
            title="先在上面选一只股票"
            description="报告对比读的是同一标的的历史运行；没有标的时这里不猜任何一次产物。"
          />
        </SectionCard>
        <SectionCard className="mt-4" title="分享卡" subtitle="只分享本页已取到的读数">
          <EmptyState
            compact
            icon={<Share2 aria-hidden="true" />}
            title="未选中标的"
            description="分享卡的内容来自本页概览读数；没有标的就没有可分享的数字，这里不生成空图。"
          />
        </SectionCard>
      </div>
    )
  }

  if (runsError !== null) {
    const info = describeResearchError(runsError)
    return (
      <div className={className}>
        <SectionCard title="报告对比" subtitle="同一只股票两次研究的关键数据逐条对齐">
          <ErrorBlock {...info} onRetry={() => setAttempt((value) => value + 1)} />
        </SectionCard>
        {shareData && (
          <SectionCard className="mt-4" title="分享卡" subtitle="只分享本页已取到的读数">
            <ShareCardBlock data={shareData} />
          </SectionCard>
        )}
      </div>
    )
  }

  const diffs = peer.older || peer.newer ? buildStageDiffs(peer.older, peer.newer) : []
  const summary = summarizeStages(diffs)
  const days = runs?.dates ?? []
  const noPeer = days.length < 2

  return (
    <div className={className}>
      <SectionCard
        title="报告对比"
        subtitle={
          runs === null
            ? '正在找这家公司以前的研究…'
            : `本机有 ${runs.count} 次研究产物${demo ? '（演示模式：两次都来自演示数据）' : ''}`
        }
        actions={
          noPeer ? undefined : (
            <Button
              size="md"
              variant="secondary"
              onClick={() => setAttempt((value) => value + 1)}
              loading={peer.loading}
            >
              重新对比
            </Button>
          )
        }
      >
        {runs === null ? (
          <SkeletonText lines={3} />
        ) : noPeer ? (
          <EmptyState
            compact
            icon={<GitCompareArrows aria-hidden="true" />}
            title={
              runs.count === 0
                ? `${code} 还没有研究产物`
                : `${code} 还只有 ${runs.latest ?? '一次'} 这一份研究`
            }
            description={
              runs.count === 0
                ? `还没有可比较的产物：先跑一次研究（去报告页按段点「运行本段」），有了第二次才能比「变了什么」。`
                : '还只有这一份，比不了「和上次比变了什么」。再跑一次研究就会出现第二次。'
            }
            action={
              reportHref ? (
                <Button size="md" variant="secondary" onClick={() => window.location.assign(reportHref)}>
                  打开研究报告
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-end gap-3">
              <Select
                aria-label="旧的那一次研究"
                value={olderDay ?? ''}
                onChange={(event) => setOlderDay(event.target.value)}
              >
                {days.map((day) => (
                  <option key={`old-${day}`} value={day}>
                    旧 {day}
                  </option>
                ))}
              </Select>
              <Select
                aria-label="新的那一次研究"
                value={newerDay ?? ''}
                onChange={(event) => setNewerDay(event.target.value)}
              >
                {days.map((day) => (
                  <option key={`new-${day}`} value={day}>
                    新 {day}
                  </option>
                ))}
              </Select>
            </div>
            <p className="text-caption leading-body text-text-muted">
              旧 {olderDay ?? MISSING} → 新 {newerDay ?? MISSING}。只并列两次各段写了什么、读数是多少，
              不解读涨跌、不计算新指标；证据编号与取数时刻每次重新生成，比较时已忽略。
            </p>
            {peer.error && (
              <p role="alert" className="rounded border border-border bg-surface-2 px-3 py-2 text-caption text-text-secondary">
                {peer.error} 关键数据与段落只比取到的那一边。
              </p>
            )}
            {peer.loading ? (
              <SkeletonText lines={5} />
            ) : diffs.length === 0 ? (
              <p className="text-body text-text-muted">两次都没有落盘产物，没有可并列的内容。</p>
            ) : (
              <>
                <p className="text-caption text-text-secondary">
                  六段合计：有变化 {summary.changed} · 本次新增 {summary.added} · 本次没有 {summary.removed} · 没变 {summary.same}
                  ；段落出入 {summary.sectionChanged} 处 · 读数出入 {summary.valueChanged} 处
                </p>
                <div className="flex flex-col gap-4">
                  {diffs.map((diff) => (
                    <StageBlock key={diff.name} diff={diff} />
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </SectionCard>

      <SectionCard
        className="mt-4"
        title="分享卡"
        subtitle={overview ? `数据时点 ${formatTime(overview.as_of ?? undefined)}` : '只分享本页已取到的读数'}
        actions={overview ? undefined : <Badge tone="warn">概览未就绪</Badge>}
      >
        {shareData === null ? (
          <SkeletonText lines={3} />
        ) : overview === null ? (
          <EmptyState
            compact
            icon={<Share2 aria-hidden="true" />}
            title="分享卡还没有数据"
            description="概览接口没返回数据（未取到或还在加载），分享卡不生成占位图——图上的数字必须与页面一致。"
          />
        ) : (
          <ShareCardBlock data={shareData} />
        )}
      </SectionCard>
    </div>
  )
}
