import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Check, Copy, Play, Sparkles } from 'lucide-react'

import { api, type SenateTaskView } from '../../api'

import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { useAssistantShell } from '../../lib/assistant/shell'
import { LinkButton } from '../../components/research/bits'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorBlock,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  CATALOG_STATUS_LABEL,
  getCatalogItem,
  getPackage,
  priorityTone,
  type CatalogStatus,
  type SkillStep,
} from '../../lib/skills'

/**
 * `/skills/:id` 单条方向详情（QUOT-474 / DEC-067）。
 *
 * `id` 是 **catalog 条目 id**（不是包名）。页面据条目找映射包与步号，把该方向的步骤
 * 高亮并自动展开；「问 AI」预填该方向 `steps[0]` 那一步的 `prompt`。
 *
 * 三态：
 * - `ready` / `partial`：正常展示，可问 Agent；
 * - `unconnected`：步骤与提示词照常可读，但**不给执行按钮**，只显示包的 `offline_notice`；
 * - `planned` / `existing_page` / `not_supported`：不属于可执行方向，给出说明与（若有）跳转。
 *
 * 数据只读 `content/skills/`，无演示、无回退。
 */

const ITEM_STATUS_TONE: Record<CatalogStatus, 'ok' | 'warn' | 'neutral' | 'info' | 'danger'> = {
  ready: 'ok',
  partial: 'warn',
  unconnected: 'neutral',
  planned: 'neutral',
  existing_page: 'info',
  not_supported: 'danger',
}

/** 提示词折叠时默认显示的行数。 */
const PREVIEW_LINES = 3

/**
 * 单步卡：序号 + 标题 + `output` 列名 + 提示词（默认 3 行，可展开）。
 * `highlight` 表示这一步是本方向映射到的步骤（高亮边框）；`canAsk` 为 false 时不渲染执行按钮。
 */
function StepCard({
  step,
  index,
  highlight,
  canAsk,
  open,
  onToggle,
  onAsk,
}: {
  step: SkillStep
  index: number
  highlight: boolean
  canAsk: boolean
  open: boolean
  onToggle: () => void
  onAsk: (step: SkillStep) => void
}) {
  const [copied, setCopied] = useState(false)
  const lines = useMemo(() => step.prompt.split('\n'), [step.prompt])
  const truncated = lines.length > PREVIEW_LINES
  const shown = open || !truncated ? lines : lines.slice(0, PREVIEW_LINES)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(step.prompt)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // 剪贴板不可用（非安全上下文 / 权限被拒）：不改状态，用户仍可从展开的正文里手动选中
    }
  }

  return (
    <Card className={cn('min-w-0', highlight && 'border-accent')}>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="ldst-num inline-flex size-5 shrink-0 items-center justify-center rounded-pill bg-accent-soft text-caption font-medium text-accent">
              {step.n}
            </span>
            <span className="truncate">{step.title}</span>
            {highlight && <Badge tone="info">本方向</Badge>}
          </span>
        }
        subtitle={
          step.output ? (
            <>
              输出列 <code className="ldst-num">{step.output}</code>
            </>
          ) : undefined
        }
        actions={<span className="ldst-num text-caption text-text-muted">第 {index + 1} / 步</span>}
      />
      <CardBody className="flex flex-col gap-3">
        <div className="text-body leading-body text-text-secondary">
          <p className="whitespace-pre-wrap break-words">{shown.join('\n')}</p>
          {truncated && (
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={open}
              className="mt-1 text-caption font-medium text-accent underline underline-offset-2"
            >
              {open ? '收起提示词' : `展开全部 ${lines.length} 行`}
            </button>
          )}
        </div>

        {step.tools.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 border-t border-border pt-2">
            <span className="text-caption text-text-muted">本步白名单</span>
            {step.tools.map((tool) => (
              <span
                key={tool}
                className="ldst-num rounded-xs border border-border px-1 text-caption text-text-secondary"
              >
                {tool}
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
          <Button
            variant="ghost"
            onClick={() => void copy()}
            leadingIcon={
              copied ? (
                <Check className="size-3.5" aria-hidden="true" />
              ) : (
                <Copy className="size-3.5" aria-hidden="true" />
              )
            }
          >
            {copied ? '已复制' : '复制提示词'}
          </Button>
          {canAsk ? (
            <Button
              variant="secondary"
              onClick={() => onAsk(step)}
              leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
            >
              问 AI
            </Button>
          ) : (
            <Badge tone="neutral">未接通，暂不执行</Badge>
          )}
        </div>
      </CardBody>
    </Card>
  )
}

/** StockSenate「真跑」：输入 6 位代码 → 发起一次合议 → 按 task_id 轮询进度。 */
export function SenateRunner() {
  const [symbol, setSymbol] = useState('')
  const [task, setTask] = useState<SenateTaskView | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!task || task.status !== 'running') return undefined
    const timer = window.setInterval(() => {
      void api.senate
        .analysisStatus(task.task_id)
        .then(setTask)
        .catch((err: unknown) => setNote(err instanceof Error ? err.message : String(err)))
    }, 3000)
    return () => window.clearInterval(timer)
  }, [task])

  const start = async () => {
    const value = symbol.trim()
    if (!/^\d{6}$/.test(value)) {
      setNote('请输入 6 位 A 股代码，例如 600000')
      return
    }
    setBusy(true)
    setNote(null)
    try {
      setTask(await api.senate.analysisStart({ code: value, hitl: false }))
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const facts = useMemo(() => {
    const out: { label: string; value: string }[] = []
    const result = task?.result
    if (result && typeof result === 'object') {
      const rec = result as Record<string, unknown>
      const wanted: readonly (readonly [string, string])[] = [
        ['code', '标的'],
        ['date', '交易日'],
        ['rating', '研究评级'],
        ['quality_score', '质量分'],
        ['verdict', '结论'],
      ]
      for (const [key, label] of wanted) {
        const value = rec[key]
        if (typeof value === 'string' || typeof value === 'number') {
          out.push({ label, value: String(value) })
        }
      }
    }
    return out
  }, [task])

  return (
    <Card>
      <CardHeader
        title="真跑一次合议"
        subtitle="走 Senate 的 stock_senate_analysis：7 分析师 → 多空辩论 → 研究经理 → 交易员 → 风控 → 终审。分钟级，别重复点。"
        actions={
          <span className="ldst-num text-caption text-text-muted">
            {task
              ? `${task.status === 'running' ? `阶段 ${task.steps_done}/${task.steps_total}` : task.status} · ${task.elapsed_sec}s`
              : '未运行'}
          </span>
        }
      />
      <CardBody className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            placeholder="600000"
            inputMode="numeric"
            aria-label="标的代码"
            className="ldst-num w-32 rounded border border-border bg-surface px-2 py-1.5 text-body text-text placeholder:text-text-muted focus-visible:outline-none"
          />
          <Button
            variant="primary"
            onClick={() => void start()}
            disabled={busy || task?.status === 'running'}
            leadingIcon={<Play className="size-3.5" aria-hidden="true" />}
          >
            {task?.status === 'running' ? '运行中…' : '发起合议'}
          </Button>
          {note && <span className="text-caption text-danger">{note}</span>}
        </div>

        {(task?.status === 'running' || task?.status === 'done') && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-stretch gap-1.5">
              {task.stages.map((stage, index) => {
                const next = task.stages[index + 1]
                const reachedAt = task.stage_log.find((row) => row.stage === stage.name)
                const done = next ? task.steps_done >= next.from_steps : task.status === 'done'
                const active = !done && stage.name === task.stage_label
                return (
                  <div
                    key={stage.name}
                    className={cn(
                      'min-w-[7.2rem] flex-1 rounded border px-2 py-1.5',
                      done && 'border-accent/40 bg-accent-soft',
                      active && 'border-accent bg-surface-2',
                      !done && !active && 'border-border bg-surface',
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          'size-1.5 rounded-pill',
                          done && 'bg-accent',
                          active && 'bg-accent animate-pulse',
                          !done && !active && 'bg-border-strong',
                        )}
                        aria-hidden="true"
                      />
                      <span
                        className={cn(
                          'text-caption',
                          done ? 'text-text' : active ? 'text-text' : 'text-text-muted',
                        )}
                      >
                        {stage.name}
                      </span>
                    </div>
                    <div className="ldst-num text-caption text-text-muted">
                      {reachedAt
                        ? `进入 ${Math.round(reachedAt.at_sec)}s · ${reachedAt.steps} 次`
                        : done
                          ? '已完成'
                          : active
                            ? '进行中'
                            : '等待'}
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="flex items-center justify-between text-caption text-text-secondary">
              <span>{task.stage_label}</span>
              <span className="ldst-num">
                {task.steps_done}/{task.steps_total} 次模型往返 · {Math.round(task.elapsed_sec)}s
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-2">
              <div
                className="h-full rounded-pill bg-accent transition-all duration-slow"
                style={{
                  width: `${Math.min(100, Math.max(4, (task.steps_done / Math.max(1, task.steps_total)) * 100))}%`,
                }}
              />
            </div>
            <p className="text-caption text-text-muted">
              环节与进入时刻来自 Senate 进程日志的真实模型往返计数（每完成一次调用记一笔），
              环节名按次数推断；不是估算的假进度。
            </p>
          </div>
        )}

        {task?.error && (
          <Banner tone="danger" title="这次没跑通">
            {task.error}
          </Banner>
        )}
        {facts.length > 0 && (
          <div className="flex flex-wrap gap-3 border-t border-border pt-2">
            {facts.map((fact) => (
              <span key={fact.label} className="text-caption text-text-secondary">
                {fact.label} <span className="text-body font-medium text-text">{fact.value}</span>
              </span>
            ))}
          </div>
        )}
        {task?.status === 'done' && (
          <details className="text-caption text-text-secondary">
            <summary className="cursor-pointer">原始返回（Senate 原文，未改写）</summary>
            <pre className="ldst-num mt-2 max-h-64 overflow-auto rounded border border-border bg-surface-2 p-2 text-caption">
              {JSON.stringify(task.result, null, 2)}
            </pre>
          </details>
        )}
      </CardBody>
    </Card>
  )
}

function Missing({ detail, code }: { detail: string; code: string }) {  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="研究工具" subtitle="未找到这条研究方向" />
      <ErrorBlock variant="panel" title="没有这条研究方向" detail={detail} code={code} />
      <div>
        <LinkButton
          to="/skills"
          variant="secondary"
          leadingIcon={<ArrowLeft className="size-3.5" aria-hidden="true" />}
        >
          返回工具总览
        </LinkButton>
      </div>
    </div>
  )
}

export default function SkillDetailScreen() {
  const { id } = useParams()
  const item = getCatalogItem(id)
  const skill = getPackage(item?.package)
  // 抽屉是 Layout 里唯一那一份（QUOT-475）：页面只请求打开，不渲染第二套
  const shell = useAssistantShell()
  // 映射到的步骤默认展开（人类口径：定位并展开 steps 里那几步）
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())

  if (!item) {
    return (
      <Missing
        detail={`目录里没有 id 为「${id ?? ''}」的方向。可能链接过期，或这一步的数据还没入库。`}
        code="E_SKILL_ITEM_NOT_FOUND"
      />
    )
  }

  // 可执行方向（有包、有步骤）才展开步骤时间轴；否则是「已在页面 / 待建 / 本阶段不做」
  const detailStatuses: CatalogStatus[] = ['ready', 'partial', 'unconnected']
  const canViewSteps = detailStatuses.includes(item.status) && skill !== null
  // 未接通包也允许点开抽屉——照常展示步骤与提示词，只在抽屉顶部挂 `offline_notice`
  const canAsk = detailStatuses.includes(item.status)
  const mapped = new Set(item.steps)

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={item.label}
        subtitle={item.note || `来自 ${skill?.label ?? item.package} 的研究方向。`}
        badge={
          <>
            <DemoStateBadge state={null} />
            <Badge tone={ITEM_STATUS_TONE[item.status]}>{CATALOG_STATUS_LABEL[item.status]}</Badge>
            <Badge tone={priorityTone(item.priority)}>{item.priority}</Badge>
          </>
        }
        meta={
          <span className="text-caption text-text-secondary">
            编号 <span className="ldst-num font-medium text-text">#{item.id}</span>
            {skill && (
              <>
                {' · '}
                <Link to={`/skills/pkg/${skill.name}`} className="font-medium text-accent underline underline-offset-2">
                  {skill.label}
                </Link>
              </>
            )}
          </span>
        }
        actions={
          <LinkButton to="/skills" variant="secondary" leadingIcon={<ArrowLeft className="size-3.5" aria-hidden="true" />}>
            工具总览
          </LinkButton>
        }
      />

      {skill?.name === 'senate-desk' && <SenateRunner />}

      {!canViewSteps ? (        <Banner tone={item.status === 'not_supported' ? 'danger' : 'info'} title="这条方向不由 skill 承载">
          {item.note ||
            (item.status === 'existing_page'
              ? '这条已由现有页面承载，无需 skill。'
              : item.status === 'planned'
                ? '这条还没建成，先把步骤留档。'
                : '本阶段不做这条。')}
        </Banner>
      ) : (
        <>
          {skill && skill.blocked_by && (
            <Banner tone={item.status === 'unconnected' ? 'warn' : 'info'} title="阻塞原因">
              {skill.blocked_by}
            </Banner>
          )}
          {item.status === 'unconnected' && skill?.offline_notice && (
            <Banner tone="warn" title="本包尚未接通">
              {skill.offline_notice}
            </Banner>
          )}

          {skill && skill.steps.length > 0 ? (
            <ol className="flex flex-col gap-4">
              {skill.steps.map((step, index) => (
                <li key={step.n}>
                  <StepCard
                    step={step}
                    index={index}
                    highlight={mapped.has(step.n)}
                    canAsk={canAsk}
                    open={!collapsed.has(step.n)}
                    onToggle={() =>
                      setCollapsed((current) => {
                        const next = new Set(current)
                        if (next.has(step.n)) next.delete(step.n)
                        else next.add(step.n)
                        return next
                      })
                    }
                    onAsk={(target) => {
                      if (!skill) return
                      shell?.open({
                        boundSkill: skill.name,
                        initialDraft: target.prompt,
                        offlineNotice: skill.offline_notice || null,
                      })
                    }}
                  />
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState
              icon={<Sparkles aria-hidden="true" />}
              title="本包还没有步骤"
              description="content/skills 下这个包的 steps.json 里没有步骤；页面不会替它编一套。"
              action={
                <Link to="/skills" className="text-body font-medium text-accent underline underline-offset-2">
                  返回工具总览
                </Link>
              }
            />
          )}
        </>
      )}
    </div>
  )
}
