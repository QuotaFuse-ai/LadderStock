import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Check, Copy, Sparkles } from 'lucide-react'

import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { ResearchAiDock } from '../../components/research/sections/AiDock'
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
 * `/skills/:id` 单条方向详情（QUOT-474 / DEC-065）。
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

function Missing({ detail, code }: { detail: string; code: string }) {
  return (
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
  // 页面级抽屉：受控开合，每步「问 AI」把该步 prompt 送进来预填
  const [askStep, setAskStep] = useState<{ n: number; prompt: string } | null>(null)
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
  const canAsk = item.status === 'ready' || item.status === 'partial'
  const mapped = new Set(item.steps)
  // 「问 AI」预填：该方向映射的第一步；没有映射就退回包的首步
  const firstStep = skill
    ? (skill.steps.find((step) => mapped.has(step.n)) ?? skill.steps[0]) ?? null
    : null

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

      {!canViewSteps ? (
        <Banner tone={item.status === 'not_supported' ? 'danger' : 'info'} title="这条方向不由 skill 承载">
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
                    onAsk={(target) => setAskStep({ n: target.n, prompt: target.prompt })}
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

      {/* 单挂点：整页只有抽屉这一处（打回项 5：不许第二套 AI 抽屉）。
          `trigger="drawer-only"`：执行入口是每步卡片里的「问 AI」，抽屉自身不出按钮；
          受控 `open` 由 `askStep` 驱动；`initialDraft` 是该步提示词，`offlineNotice` 给未接通包说明。 */}
      {skill && canViewSteps && (
        <ResearchAiDock
          trigger="drawer-only"
          pageKey={`skills:${skill.name}`}
          name={skill.label}
          initialDraft={askStep?.prompt ?? firstStep?.prompt ?? ''}
          offlineNotice={skill.offline_notice || undefined}
          open={askStep !== null}
          onOpenChange={(next) => {
            if (!next) setAskStep(null)
          }}
        />
      )}
    </div>
  )
}
