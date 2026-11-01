import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowLeft, FileText, RefreshCw, RotateCcw } from 'lucide-react'

import {
  RESEARCH_STAGES,
  STAGE_STATUS_LABEL,
  STAGE_TITLE,
  describeResearchError,
  isResearchEndpointMissing,
  isStageName,
  type ResearchStage,
  type ResearchStageName,
} from '../../api/research'
import { buildCitationIndex } from '../../lib/citation'
import { MISSING, formatTime } from '../../lib/format'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  EmptyState,
  ErrorBlock,
} from '../../components/ui'
import { MarkdownView } from '../../components/research/MarkdownView'
import { ProvenanceDrawer } from '../../components/research/ProvenanceDrawer'
import { StageRail } from '../../components/research/StageRail'
import { LinkButton } from '../../components/research/bits'
import { artifactMarkdown } from './facts'
import { asSourceBadgeStatus, SourceStatusBadge } from '../industry/sourceBadges'
import { detailHref } from './links'
import { useResearchRun } from './useResearchRun'
import { useResearchView } from './useResearchView'

/**
 * `/research/:code/report` 六阶段研究报告（R8）。
 *
 * 左侧阶段导航展示每段 ok / degraded / failed / pending；右侧渲染该段 Markdown。
 * `[n]` 角标打开来源抽屉；失败段「重试本段」调用 `research_run --only <stage>`
 * （HTTP: POST /api/research/{code}/run），随后 1s 轮询任务直到终态。
 */

type DrawerState = { stage: ResearchStageName; citation: number | null } | null

function stageTone(stage: ResearchStage): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (stage.status === 'ok') return 'ok'
  if (stage.status === 'degraded') return 'warn'
  if (stage.status === 'failed') return 'danger'
  return 'neutral'
}

function RunProgress({
  title,
  progress,
  step,
}: {
  title: string
  progress: number
  step: string | null
}) {
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)
  return (
    <div className="flex flex-col gap-2" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-2 text-body">
        <span className="text-text">正在重跑「{title}」…</span>
        <span className="ldst-num text-text-secondary">{pct}%</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${title}重跑进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="h-2 w-full overflow-hidden rounded-pill bg-surface-3"
      >
        <span className="block h-full bg-accent transition-[width] duration-base" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-caption text-text-muted">{step ?? '等待任务返回…'} · 每秒轮询一次</p>
    </div>
  )
}

function runErrorView(error: unknown): { title: string; detail: string; code: string | null } {
  if (isResearchEndpointMissing(error)) {
    return {
      title: '研究重试接口未接入',
      detail:
        'POST /api/research/{code}/run 尚未上线；接口合入后，此按钮才会真正触发 research_run --only <stage>。',
      code: 'HTTP 404',
    }
  }
  return describeResearchError(error)
}

export default function ResearchReportScreen() {
  const view = useResearchView()
  const [params, setParams] = useSearchParams()
  const [drawer, setDrawer] = useState<DrawerState>(null)

  const stages = useMemo<ResearchStage[]>(
    () =>
      view.status?.stages ??
      RESEARCH_STAGES.map((name) => ({
        name,
        title: STAGE_TITLE[name],
        status: 'pending' as const,
        artifact: null,
      })),
    [view.status],
  )

  const run = useResearchRun(view.source, view.code, view.date, view.reload)

  const stageParam = params.get('stage')
  const active: ResearchStageName = isStageName(stageParam)
    ? stageParam
    : view.status?.failed[0] ?? 'report'
  const ordinal = RESEARCH_STAGES.indexOf(active) + 1
  const stage =
    stages.find((item) => item.name === active) ?? {
      name: active,
      title: STAGE_TITLE[active],
      status: 'pending' as const,
      artifact: null,
    }
  const artifact = stage.artifact ?? null
  const document = artifact?.document ?? null
  const sources = document?.provenance.sources ?? []
  const verified = sources.filter((source) => source.verified).length
  const index = useMemo(() => buildCitationIndex(document?.provenance), [document])
  const runningThis = run.running && run.stage === active

  const drawerStage = drawer ? stages.find((item) => item.name === drawer.stage) ?? null : null
  const drawerIndex = useMemo(
    () => buildCitationIndex(drawerStage?.artifact?.document.provenance),
    [drawerStage],
  )

  const problems = stages.filter((item) => item.status === 'failed' || item.status === 'pending')
  const degraded = stages.filter((item) => item.degraded)

  const selectStage = (name: ResearchStageName) => {
    const copy = new URLSearchParams(params)
    copy.set('stage', name)
    setParams(copy, { replace: true })
  }

  const title = view.name ?? (view.code ? view.code : '研究报告')
  const summary =
    view.state === 'offline'
      ? '本机服务未连接'
      : view.state === 'error'
        ? '取数失败'
        : view.state === 'empty'
          ? '还没有研究产物'
          : view.status?.complete
            ? `六段齐备 · 更新于 ${formatTime(view.status.updated_at)}`
            : `${view.status?.failed.length ?? 0} 段失败 · ${view.status?.pending.length ?? 0} 段待跑`

  const header = (
    <PageHeader
      title={title}
      subtitle={view.code ? `${view.code} · ${view.date} · 研究报告` : '研究报告'}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          {view.demo && (
            <Badge tone="info" title="演示数据：不是真实研究产物">
              演示数据（?demo=1）
            </Badge>
          )}
          <DemoStateBadge state={view.forced} />
          {view.status && (
            <SourceStatusBadge
              status={
                asSourceBadgeStatus(view.status.data_source_status) ?? view.status.source_status
              }
              detail={view.status.reason}
            />
          )}
        </div>
      }
      meta={<span className="text-caption text-text-secondary">{summary}</span>}
      actions={
        <>
          <Button
            variant="secondary"
            onClick={view.reload}
            loading={view.requesting && view.state !== 'loading'}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
          >
            刷新
          </Button>
          {view.code && (
            <LinkButton
              to={detailHref(view.code, view)}
              leadingIcon={<ArrowLeft className="size-3.5" aria-hidden="true" />}
            >
              个股深挖
            </LinkButton>
          )}
        </>
      }
    />
  )

  if (view.state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={6} rows={5} />
      </div>
    )
  }

  if (view.state === 'error') {
    const info = !view.code
      ? {
          title: '证券代码不合法',
          detail: '路径应为 /research/600000/report（6 位数字，可带 .SH / .SZ / .BJ 后缀）。',
          code: 'E_CODE',
        }
      : view.error
        ? describeResearchError(view.error)
        : {
            title: '研究取数失败',
            detail: '（演示态）没有真实错误；?state=error 只用于演练错误态的排版。',
            code: null,
          }
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock {...info} onRetry={view.code ? view.reload : undefined} />
      </div>
    )
  }

  if (view.state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={view.reload} />
        <EmptyState
          title="离线：暂时读不到研究报告"
          description="本机服务恢复后点「重试」会重新读取六段产物；页面不会用缓存冒充新数据。"
        />
      </div>
    )
  }

  if (view.state === 'empty') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <EmptyState
          icon={<FileText aria-hidden="true" />}
          title={view.endpointMissing ? '研究读取接口未接入' : '六段产物还没有生成'}
          description={
            view.endpointMissing
              ? '后端尚未提供 /api/research/* 只读接口（HTTP 404）；接口上线后本页会自动读取落盘产物。当前不会伪造任何数据。'
              : `先运行 ldst research ${view.code ?? '<code>'} --date ${view.date} 生成六段产物，再刷新本页。`
          }
          action={
            <Button
              variant="secondary"
              onClick={view.reload}
              leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
            >
              刷新
            </Button>
          }
        />
      </div>
    )
  }

  const stageSubtitle = [
    `第 ${ordinal} 段`,
    STAGE_STATUS_LABEL[stage.status],
    stage.degraded ? '降级' : null,
    document?.mode ? `mode=${document.mode}` : null,
    document?.model ? `模型=${document.model}` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="flex flex-col gap-5">
      {header}

      {view.state === 'partial' && problems.length > 0 && (
        <Banner tone="warn" title={`还有 ${problems.length} 段未完成`}>
          失败段可「重试本段」单独续跑；其余段落的产物不受影响，已生成内容照常展示。
        </Banner>
      )}

      {degraded.length > 0 && (
        <Banner tone="warn" title={`${degraded.length} 段为降级产物`}>
          {degraded.map((item) => item.title).join('、')}：结构化输出不完整；缺失项显示 —，不能当作完整数据。
        </Banner>
      )}

      <div className="flex flex-col gap-4 desk:flex-row desk:items-start">
        <Card className="min-w-0 desk:w-[208px] desk:shrink-0 desk:sticky desk:top-4">
          <CardHeader title="研究阶段" subtitle="六段 · 点击切换" />
          <CardBody className="p-2">
            <StageRail
              stages={stages}
              active={active}
              runningStage={run.running ? run.stage : null}
              progress={run.progress}
              onSelect={selectStage}
            />
          </CardBody>
        </Card>

        <Card className="min-w-0 flex-1">
          <CardHeader
            title={`${ordinal}. ${stage.title}`}
            subtitle={stageSubtitle}
            actions={
              <>
                <Badge tone={stageTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>
                <Button
                  variant="ghost"
                  onClick={() => setDrawer({ stage: active, citation: null })}
                  disabled={!document}
                  disabledReason={document ? undefined : '本段还没有产物'}
                >
                  来源（{sources.length}）
                </Button>
              </>
            }
          />
          <CardBody className="flex flex-col gap-3">
            {run.error !== null && (
              <Banner
                tone="danger"
                title={runErrorView(run.error).title}
                action={
                  view.code ? (
                    <Button
                      variant="secondary"
                      size="md"
                      onClick={() => run.run(active)}
                      disabled={run.running}
                      leadingIcon={<RotateCcw className="size-3.5" aria-hidden="true" />}
                    >
                      再试一次
                    </Button>
                  ) : undefined
                }
              >
                {runErrorView(run.error).detail}
                {runErrorView(run.error).code ? `（${runErrorView(run.error).code}）` : ''}
              </Banner>
            )}

            {runningThis ? (
              <RunProgress title={stage.title} progress={run.progress} step={run.step} />
            ) : stage.status === 'failed' ? (
              <div className="flex flex-col gap-3">
                <ErrorBlock
                  title="本段生成失败"
                  detail={document?.failure?.message ?? '本段没有产物，失败原因未记录。'}
                  code={document?.failure?.kind ?? null}
                  onRetry={view.code ? () => run.run(active) : undefined}
                  retryLabel="重试本段"
                />
                {document?.failure?.action && (
                  <p className="text-caption leading-body text-text-muted">
                    建议：{document.failure.action}
                  </p>
                )}
                <p className="text-caption text-text-muted">
                  等价命令：
                  <code className="ldst-num ml-1">
                    ldst research {view.code} --date {view.date} --only {active}
                  </code>
                </p>
                {document?.failure?.retryable === false && (
                  <p className="text-caption text-warn">
                    后端把这条错误标记为不可重试；先修复数据源或模型配置，再点重试。
                  </p>
                )}
              </div>
            ) : stage.status === 'pending' ? (
              <div className="flex flex-col gap-3">
                <EmptyState
                  compact
                  title="本段尚未运行"
                  description={`这一段还没有落盘产物；单独续跑不会重算其他已完成的段落。`}
                  action={
                    <Button
                      variant="primary"
                      onClick={() => run.run(active)}
                      disabled={run.running}
                      loading={run.running}
                      leadingIcon={<RotateCcw className="size-3.5" aria-hidden="true" />}
                    >
                      运行本段
                    </Button>
                  }
                />
                <p className="text-caption text-text-muted">
                  等价命令：
                  <code className="ldst-num ml-1">
                    ldst research {view.code} --date {view.date} --only {active}
                  </code>
                </p>
              </div>
            ) : artifact && document ? (
              <>
                {stage.degraded && (
                  <Banner tone="warn" title="本段为降级产物">
                    结构化输出不完整或走了纯文本兜底；数字仍需交叉核对，缺失处不补 0。
                  </Banner>
                )}
                <MarkdownView
                  markdown={artifactMarkdown(document, artifact.markdown)}
                  index={index}
                  onCitation={(n) => setDrawer({ stage: active, citation: n })}
                />
              </>
            ) : (
              <EmptyState
                compact
                title="本段没有可渲染的产物"
                description="状态显示已完成，但 artifact 为空；请检查落盘文件或重跑本段。"
              />
            )}
          </CardBody>
          {document && (
            <CardFooter>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  来源 {sources.length} 条 · 已核实 {verified} 条 · 未核实 {sources.length - verified} 条
                  {document.provenance.unverified ? ' · 本段至少一处未核实' : ''}
                </span>
                <span>
                  {document.failure
                    ? `失败：${document.failure.kind ?? 'unknown'}`
                    : `更新于 ${formatTime(view.status?.updated_at ?? null)}`}
                </span>
              </div>
            </CardFooter>
          )}
        </Card>
      </div>

      <p className="text-caption leading-body text-text-muted">
        报告段只消费前五段已落盘产物，不重新取数（DEC-024）；正文角标可点开
        来源 · endpoint · as_of · url。缺失值显示 {MISSING}，〔未核实〕表示该数字没有可追溯来源。
      </p>

      <ProvenanceDrawer
        open={drawer !== null}
        onClose={() => setDrawer(null)}
        stageTitle={drawer ? (drawerStage?.title ?? STAGE_TITLE[drawer.stage]) : ''}
        citation={drawer?.citation ?? null}
        index={drawerIndex}
      />
    </div>
  )
}
