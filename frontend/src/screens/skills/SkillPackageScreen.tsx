import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Check, Copy, Package, Sparkles } from 'lucide-react'

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
import {
  getPackage,
  readCatalog,
  SKILL_STATUS_LABEL,
  type SkillStatus,
  type SkillStep,
} from '../../lib/skills'

/**
 * `/skills/pkg/:name` 整包详情（QUOT-474 / DEC-067）。
 *
 * `name` 是 `content/skills/index.json` 里的包名。展示整包的全部步骤（7/6/5 步）与
 * 该包承载的方向条目清单（从 `catalog.json` 反查，不写第二份）。每步都可「问 AI」预填。
 *
 * `unconnected` / `partial` 包：抽屉与页顶出 `offline_notice`；步骤与提示词照常可读。
 */

const PACKAGE_STATUS: Record<SkillStatus, { tone: 'ok' | 'warn' | 'neutral'; hint: string }> = {
  ready: { tone: 'ok', hint: '数据源已接通，可直接执行' },
  partial: { tone: 'warn', hint: '部分段落依赖未接通数据源' },
  unconnected: { tone: 'neutral', hint: '数据源未接通，只提供研究步骤与提示词' },
}

const PREVIEW_LINES = 3

function StepRow({
  step,
  index,
  onAsk,
}: {
  step: SkillStep
  index: number
  onAsk: (step: SkillStep) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const lines = useMemo(() => step.prompt.split('\n'), [step.prompt])
  const truncated = lines.length > PREVIEW_LINES
  const shown = expanded || !truncated ? lines : lines.slice(0, PREVIEW_LINES)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(step.prompt)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // 剪贴板不可用：不改状态，用户仍可手动选中
    }
  }

  return (
    <Card className="min-w-0">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="ldst-num inline-flex size-5 shrink-0 items-center justify-center rounded-pill bg-accent-soft text-caption font-medium text-accent">
              {step.n}
            </span>
            <span className="truncate">{step.title}</span>
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
              onClick={() => setExpanded((current) => !current)}
              aria-expanded={expanded}
              className="mt-1 text-caption font-medium text-accent underline underline-offset-2"
            >
              {expanded ? '收起提示词' : `展开全部 ${lines.length} 行`}
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
          <Button
            variant="secondary"
            onClick={() => onAsk(step)}
            leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
          >
            问 AI
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}

export default function SkillPackageScreen() {
  const { name } = useParams()
  const skill = getPackage(name)
  // 抽屉是 Layout 里唯一那一份（QUOT-475）：页面只请求打开，不渲染第二套
  const shell = useAssistantShell()

  // 反查本包承载的方向条目（catalog 是唯一数据源），按 id 排序
  const directions = useMemo(
    () =>
      skill
        ? readCatalog()
            .items.filter((item) => item.package === skill.name)
            .sort((a, b) => a.id - b.id)
        : [],
    [skill],
  )

  if (!skill) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="研究工具" subtitle="未找到这个研究工具包" />
        <ErrorBlock
          variant="panel"
          title="没有这个研究工具包"
          detail={`content/skills/ 下没有名为「${name ?? ''}」的包。可能名字拼错，或这一步的数据还没入库。`}
          code="E_SKILL_PACKAGE_NOT_FOUND"
        />
        <div>
          <LinkButton to="/skills" variant="secondary" leadingIcon={<ArrowLeft className="size-3.5" aria-hidden="true" />}>
            返回工具总览
          </LinkButton>
        </div>
      </div>
    )
  }

  const meta = PACKAGE_STATUS[skill.status]

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={skill.label}
        subtitle={skill.description || '本包未提供一句话定位。'}
        badge={
          <>
            <DemoStateBadge state={null} />
            <Badge tone={meta.tone} title={meta.hint}>
              {SKILL_STATUS_LABEL[skill.status]}
            </Badge>
          </>
        }
        meta={
          <span className="text-caption text-text-secondary">
            <span className="font-medium text-text">{skill.steps.length}</span> 步 ·{' '}
            <span className="font-medium text-text">{skill.tool_count}</span> 个工具 ·{' '}
            <span className="font-medium text-text">{directions.length}</span> 条方向
          </span>
        }
        actions={
          <LinkButton to="/skills" variant="secondary" leadingIcon={<ArrowLeft className="size-3.5" aria-hidden="true" />}>
            工具总览
          </LinkButton>
        }
      />

      {skill.blocked_by && (
        <Banner tone={skill.status === 'unconnected' ? 'warn' : 'info'} title="阻塞原因">
          {skill.blocked_by}
        </Banner>
      )}

      {directions.length > 0 && (
        <Card>
          <CardHeader
            title="本包承载的方向"
            subtitle="每一条方向对应该包的一段步骤"
            actions={<Package className="size-4 text-text-muted" aria-hidden="true" />}
          />
          <CardBody className="flex flex-wrap gap-2">
            {directions.map((item) => (
              <Link
                key={item.id}
                to={`/skills/${item.id}`}
                className="inline-flex items-center gap-1.5 rounded-pill border border-border-strong bg-surface px-2.5 py-1 text-caption text-text-secondary transition-colors duration-fast hover:bg-surface-3 hover:text-text"
              >
                <span className="ldst-num text-text-muted">#{item.id}</span>
                <span>{item.label}</span>
                {item.steps.length > 0 && (
                  <span className="ldst-num text-text-muted">第 {item.steps.join('/')} 步</span>
                )}
              </Link>
            ))}
          </CardBody>
        </Card>
      )}

      {skill.steps.length === 0 ? (
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
      ) : (
        <ol className="flex flex-col gap-4">
          {skill.steps.map((step, index) => (
            <li key={step.n}>
              <StepRow
                step={step}
                index={index}
                onAsk={(target) =>
                  shell?.open({
                    boundSkill: skill.name,
                    initialDraft: target.prompt,
                    offlineNotice: skill.offline_notice || null,
                  })
                }
              />
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
