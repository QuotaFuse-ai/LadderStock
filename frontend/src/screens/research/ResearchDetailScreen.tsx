import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { FileText, RefreshCw, RotateCcw } from 'lucide-react'

import {
  RESEARCH_STAGES,
  STAGE_STATUS_LABEL,
  STAGE_TITLE,
  describeResearchError,
  stageOf,
  type ResearchStage,
  type ResearchStageName,
  type ResearchStatus,
  type StageArtifact,
  type StageDocument,
} from '../../api/research'
import { buildCitationIndex } from '../../lib/citation'
import { MISSING, formatTime } from '../../lib/format'
import { cn } from '../../lib/cn'
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
  MetricCard,
  SectionCard,
  Sparkline,
} from '../../components/ui'
import { MarkdownView } from '../../components/research/MarkdownView'
import { ProvenanceDrawer } from '../../components/research/ProvenanceDrawer'
import { StageRail } from '../../components/research/StageRail'
import { LinkButton, PercentileBar } from '../../components/research/bits'
import { ValuationBasisPanel } from '../../components/valuation/ValuationBasisPanel'
import { createDemoValuationLoader } from './demo'
import { asSourceBadgeStatus, SourceStatusBadge } from '../industry/sourceBadges'
import {
  MONEY,
  formatFactValue,
  numberFact,
  percentileFacts,
  riskItems,
  seriesFacts,
  stringFact,
  type SeriesFact,
} from './facts'
import { reportHref } from './links'
import { useResearchView, type ResearchView } from './useResearchView'

/**
 * `/research/:code` 个股深挖（R8）。
 *
 * 左列三张卡（公司画像 / 财务趋势 / 风险），右列盈利预期 + 六段状态；
 * 估值单独给 FinanceCoffer 面板与「估值分位」。所有数字都来自落盘产物或注入的 facts，
 * 缺失显示 `—` + 原因，不补 0、不计算新比率。
 */

const FINANCIAL_METRICS = [
  { key: 'revenue', label: '营业收入' },
  { key: 'revenue_yoy', label: '营收同比' },
  { key: 'net_profit', label: '归母净利润' },
  { key: 'net_profit_yoy', label: '净利润同比' },
  { key: 'roe', label: 'ROE' },
]

const FORECAST_METRICS = [
  { key: 'eps_2026', label: '2026E EPS' },
  { key: 'eps_2027', label: '2027E EPS' },
  { key: 'net_profit_growth', label: '净利润增速' },
  { key: 'institution_count', label: '覆盖机构数' },
]

const RISK_METRICS = [
  { key: 'drawdown_60d', label: '近 60 日最大回撤' },
  { key: 'pledge_ratio', label: '质押比例' },
  { key: 'bad_loan_ratio', label: '不良率' },
]

type DrawerState = { stage: ResearchStageName; citation: number | null } | null

function stageBadgeTone(stage: ResearchStage): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (stage.status === 'ok') return 'ok'
  if (stage.status === 'degraded') return 'warn'
  if (stage.status === 'failed') return 'danger'
  return 'neutral'
}

function Fact({ label, value, wide }: { label: string; value: string | null; wide?: boolean }) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-0.5', wide && 'min-[1024px]:col-span-2')}>
      <dt className="text-caption text-text-muted">{label}</dt>
      <dd className="break-words text-body text-text">{value ?? MISSING}</dd>
    </div>
  )
}

/** 金额单位单独交给 MetricCard.unit，避免「1,203.6 亿」在窄卡里折行。 */
function moneyParts(key: string, text: string): { value: string; unit: string | null } {
  const matched = MONEY.has(key) ? /^(.*\d)\s+(亿|万)$/.exec(text) : null
  return matched ? { value: matched[1], unit: matched[2] } : { value: text, unit: null }
}

function MetricRow({
  facts,
  metrics,
}: {
  facts: Record<string, unknown>
  metrics: Array<{ key: string; label: string }>
}) {
  return (
    <div className="grid grid-cols-2 gap-3 rail:grid-cols-3 desk:grid-cols-5">
      {metrics.map(({ key, label }) => {
        const present = facts[key] !== null && facts[key] !== undefined
        const parts = present
          ? moneyParts(key, formatFactValue(key, facts[key]))
          : { value: MISSING, unit: null }
        return (
          <MetricCard
            key={key}
            label={label}
            value={parts.value}
            unit={parts.unit ?? undefined}
            note={present ? undefined : '本段未提供'}
          />
        )
      })}
    </div>
  )
}

function Narrative({
  document,
  onCitation,
}: {
  document: StageDocument
  onCitation: (n: number) => void
}) {
  if (document.narrative.length === 0) return null
  return (
    <div className="mt-4 border-t border-border pt-3">
      <h3 className="mb-2 text-body font-semibold text-text">研究摘要</h3>
      <MarkdownView
        markdown={document.narrative.join('\n\n')}
        index={buildCitationIndex(document.provenance)}
        onCitation={onCitation}
      />
      {document.notes.length > 0 && (
        <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-caption text-text-muted">
          {document.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function StageProblem({
  stage,
  view,
}: {
  stage: ResearchStage
  view: ResearchView
}) {
  const failed = stage.status === 'failed'
  const detail = stage.artifact?.document.failure?.message
  return (
    <EmptyState
      compact
      icon={<FileText aria-hidden="true" />}
      title={failed ? `${stage.title}段生成失败` : `${stage.title}段尚未运行`}
      description={
        detail ??
        `运行 ldst research ${view.code} --date ${view.date} --only ${stage.name} 生成这一段。`
      }
      action={
        view.code ? (
          <LinkButton
            to={reportHref(view.code, view, stage.name)}
            leadingIcon={<RotateCcw className="size-3.5" aria-hidden="true" />}
          >
            {failed ? '去报告页重试' : '去报告页运行'}
          </LinkButton>
        ) : undefined
      }
    />
  )
}

function SourceFoot({
  stage,
  artifact,
  onOpen,
}: {
  stage: ResearchStageName
  artifact: StageArtifact
  onOpen: (stage: ResearchStageName) => void
}) {
  const sources = artifact.document.provenance.sources
  const verified = sources.filter((source) => source.verified).length
  return (
    <CardFooter>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          来源 {sources.length} 条 · 已核实 {verified} 条 · 未核实 {sources.length - verified} 条
        </span>
        <button
          type="button"
          onClick={() => onOpen(stage)}
          className="font-medium text-info underline underline-offset-2"
        >
          查看来源
        </button>
      </div>
    </CardFooter>
  )
}

function SeriesRow({ series }: { series: SeriesFact }) {
  const first = series.values[0]
  const last = series.values[series.values.length - 1]
  return (
    <li className="flex flex-wrap items-center justify-between gap-2">
      <span className="min-w-0 text-body text-text">{series.label}</span>
      <span className="ldst-num text-caption text-text-secondary">
        {first} → {last} · {series.values.length} 期
      </span>
      <Sparkline
        values={series.values}
        ariaLabel={`${series.label}，${series.values.length} 个数据点，从 ${first} 到 ${last}`}
      />
    </li>
  )
}

interface SectionProps {
  stage: ResearchStage
  artifact: StageArtifact | null
  view: ResearchView
  onCitation: (stage: ResearchStageName, citation: number | null) => void
}

function CompanyCard({ stage, artifact, view, onCitation }: SectionProps) {
  const document = artifact?.document ?? null
  const facts = document?.facts ?? {}
  return (
    <SectionCard
      className="min-w-0"
      title="公司画像"
      subtitle={stage.status === 'ok' ? '主营 / 行业 / 股东' : STAGE_STATUS_LABEL[stage.status]}
      tone={stage.status === 'ok' ? 'neutral' : stage.status === 'failed' ? 'danger' : 'warn'}
      actions={<Badge tone={stageBadgeTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>}
    >
      {!document ? (
        <StageProblem stage={stage} view={view} />
      ) : (
        <>
          <dl className="grid gap-x-6 gap-y-3 min-[1024px]:grid-cols-2">
            <Fact label="所属行业" value={stringFact(facts, ['industry'])} />
            <Fact label="上市板块" value={stringFact(facts, ['listed_board'])} />
            <Fact label="主营业务" value={stringFact(facts, ['main_business'])} wide />
            <Fact label="控股股东" value={stringFact(facts, ['controlling_shareholder'])} />
            <Fact
              label="员工数"
              value={
                numberFact(facts, ['employees']) === null
                  ? null
                  : `${formatFactValue('employees', facts.employees)} 人`
              }
            />
          </dl>
          <Narrative document={document} onCitation={(n) => onCitation(stage.name, n)} />
        </>
      )}
      {artifact && (
        <div className="-mx-4 -mb-4 mt-4">
          <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
        </div>
      )}
    </SectionCard>
  )
}

function FinancialCard({ stage, artifact, view, onCitation }: SectionProps) {
  const document = artifact?.document ?? null
  const facts = document?.facts ?? {}
  const series = document ? seriesFacts(facts) : []
  return (
    <SectionCard
      className="min-w-0"
      title="财务趋势"
      subtitle="当期快照 + 历史序列（不重算）"
      tone={stage.status === 'ok' ? 'neutral' : stage.status === 'failed' ? 'danger' : 'warn'}
      actions={<Badge tone={stageBadgeTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>}
    >
      {!document ? (
        <StageProblem stage={stage} view={view} />
      ) : (
        <>
          <MetricRow facts={facts} metrics={FINANCIAL_METRICS} />
          <div className="mt-4 border-t border-border pt-3">
            <h3 className="mb-2 text-body font-semibold text-text">历史序列</h3>
            {series.length === 0 ? (
              <EmptyState
                compact
                title="没有历史序列"
                description="本段只提供当期数字，facts 中没有 `*_series`；页面不会自行拼一条趋势线。"
              />
            ) : (
              <ul className="flex flex-col gap-3">
                {series.map((item) => (
                  <SeriesRow key={item.key} series={item} />
                ))}
              </ul>
            )}
          </div>
          <Narrative document={document} onCitation={(n) => onCitation(stage.name, n)} />
        </>
      )}
      {artifact && (
        <div className="-mx-4 -mb-4 mt-4">
          <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
        </div>
      )}
    </SectionCard>
  )
}

function ForecastCard({ stage, artifact, view, onCitation }: SectionProps) {
  const document = artifact?.document ?? null
  const facts = document?.facts ?? {}
  return (
    <SectionCard
      className="min-w-0"
      title="盈利预期"
      subtitle="一致预期快照，非本公司预测"
      tone={stage.degraded ? 'warn' : stage.status === 'failed' ? 'danger' : 'neutral'}
      actions={<Badge tone={stageBadgeTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>}
    >
      {!document ? (
        <StageProblem stage={stage} view={view} />
      ) : (
        <>
          {stage.degraded && (
            <Banner tone="warn" title="本段为降级产物" className="mb-3">
              结构化字段可能不完整；缺失项显示 —，不会用其他数字顶替。
            </Banner>
          )}
          <div className="grid grid-cols-2 gap-3">
            {FORECAST_METRICS.map(({ key, label }) => {
              const present = facts[key] !== null && facts[key] !== undefined
              return (
                <MetricCard
                  key={key}
                  label={label}
                  value={present ? formatFactValue(key, facts[key]) : MISSING}
                  note={present ? undefined : '本段未提供'}
                />
              )
            })}
          </div>
          <Narrative document={document} onCitation={(n) => onCitation(stage.name, n)} />
        </>
      )}
      {artifact && (
        <div className="-mx-4 -mb-4 mt-4">
          <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
        </div>
      )}
    </SectionCard>
  )
}

function RiskCard({ stage, artifact, view, onCitation }: SectionProps) {
  const document = artifact?.document ?? null
  const facts = document?.facts ?? {}
  const risks = document ? riskItems(document.ai) : []
  return (
    <SectionCard
      className="min-w-0"
      title="风险清单"
      subtitle="结构化风险 + 关键指标"
      tone={stage.status === 'failed' ? 'danger' : risks.length > 0 ? 'warn' : 'neutral'}
      actions={<Badge tone={stageBadgeTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>}
    >
      {!document ? (
        <StageProblem stage={stage} view={view} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 min-[1024px]:grid-cols-3">
            {RISK_METRICS.map(({ key, label }) => {
              const present = facts[key] !== null && facts[key] !== undefined
              return (
                <MetricCard
                  key={key}
                  label={label}
                  value={present ? formatFactValue(key, facts[key]) : MISSING}
                  note={present ? undefined : '本段未提供'}
                />
              )
            })}
          </div>
          <div className="mt-4 border-t border-border pt-3">
            {risks.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {risks.map((risk, index) => (
                  <li key={`${index}-${risk}`} className="flex items-start gap-2">
                    <span className="ldst-num mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-xs bg-warn-soft text-caption font-medium text-warn">
                      {index + 1}
                    </span>
                    <span className="min-w-0 text-body leading-body text-text-secondary">{risk}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                compact
                title="本段没有结构化风险清单"
                description="ai.risks 为空；下面保留叙述原文，不自行归纳风险条目。"
              />
            )}
            <Narrative document={document} onCitation={(n) => onCitation(stage.name, n)} />
          </div>
        </>
      )}
      {artifact && (
        <div className="-mx-4 -mb-4 mt-4">
          <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
        </div>
      )}
    </SectionCard>
  )
}

function PercentileCard({ stage, artifact }: { stage: ResearchStage; artifact: StageArtifact | null }) {
  const facts = artifact?.document.facts ?? {}
  const percentiles = percentileFacts(facts)
  const unavailable = stage.status === 'failed' || stage.status === 'pending'
  return (
    <Card className="min-w-0">
      <CardHeader
        title="估值分位"
        subtitle="来自 facts；页面只展示，不计算分位"
        actions={<Badge tone={stageBadgeTone(stage)}>{STAGE_STATUS_LABEL[stage.status]}</Badge>}
      />
      <CardBody className="flex flex-col gap-4">
        {unavailable ? (
          <EmptyState
            compact
            title="估值段未完成"
            description="FinanceCoffer 的 PE / PB / ROE 仍可独立查看；估值分位需要估值段的结构化产物。"
          />
        ) : percentiles.length === 0 ? (
          <p className="text-body leading-body text-text-secondary">
            当前快照未提供估值分位（facts 中没有 0–100 的分位字段）。页面不会用自算分位补位。
          </p>
        ) : (
          percentiles.map((item) => (
            <PercentileBar key={item.key} label={item.label} value={item.value} />
          ))
        )}
      </CardBody>
    </Card>
  )
}

export default function ResearchDetailScreen() {
  const view = useResearchView()
  const navigate = useNavigate()
  const [drawer, setDrawer] = useState<DrawerState>(null)

  const artifacts = useMemo(() => {
    const map = new Map<ResearchStageName, StageArtifact>()
    for (const stage of view.status?.stages ?? []) {
      if (stage.artifact) map.set(stage.name, stage.artifact)
    }
    return map
  }, [view.status])

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

  const drawerArtifact = drawer ? artifacts.get(drawer.stage) ?? null : null
  const drawerIndex = useMemo(
    () => buildCitationIndex(drawerArtifact?.document.provenance),
    [drawerArtifact],
  )

  const demoLoader = useMemo(
    () => (view.demo ? createDemoValuationLoader() : undefined),
    [view.demo],
  )

  const title = view.name ?? (view.code ? view.code : '个股研究')
  const status: ResearchStatus | null = view.status
  const companyStage = status ? stageOf(status, 'company') : null
  const financialStage = status ? stageOf(status, 'financial') : null
  const forecastStage = status ? stageOf(status, 'forecast') : null
  const valuationStage = status ? stageOf(status, 'valuation') : null
  const riskStage = status ? stageOf(status, 'risk') : null
  const problems = stages.filter((stage) => stage.status === 'failed' || stage.status === 'pending')

  const summary =
    view.state === 'offline'
      ? '本机服务未连接'
      : view.state === 'error'
        ? '取数失败'
        : view.state === 'empty'
          ? '还没有研究产物'
          : status?.complete
            ? `六段齐备 · 更新于 ${formatTime(status.updated_at)}`
            : `部分段落未完成 · 更新于 ${formatTime(status?.updated_at ?? null)}`

  const header = (
    <PageHeader
      title={title}
      subtitle={view.code ? `${view.code} · ${view.date} · 个股深挖` : '个股深挖'}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          {view.demo && (
            <Badge tone="info" title="演示数据：不是真实研究产物">
              演示数据（?demo=1）
            </Badge>
          )}
          <DemoStateBadge state={view.forced} />
          {status && (
            <SourceStatusBadge
              status={asSourceBadgeStatus(status.data_source_status) ?? status.source_status}
              detail={status.reason}
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
            <LinkButton to={reportHref(view.code, view)} variant="primary" leadingIcon={<FileText className="size-3.5" aria-hidden="true" />}>
              研究报告
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
        <ScreenSkeleton metrics={5} rows={6} />
      </div>
    )
  }

  if (view.state === 'error') {
    const info = !view.code
      ? {
          title: '证券代码不合法',
          detail: '路径应为 /research/600000（6 位数字，可带 .SH / .SZ / .BJ 后缀）。',
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
        <Link to="/" className="text-caption text-info underline underline-offset-2">
          回到首页
        </Link>
      </div>
    )
  }

  if (view.state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={view.reload} />
        <EmptyState
          title="离线：暂时读不到研究产物"
          description="本机服务恢复后点「重试」会重新读取；页面不会展示缓存以外的旧数据。"
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
          title={view.endpointMissing ? '研究读取接口未接入' : '还没有研究产物'}
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
        {view.demo && (
          <p className="text-caption text-text-muted">
            演示模式：试试 <code className="ldst-num">?demo=1&amp;state=normal</code>。
          </p>
        )}
      </div>
    )
  }

  const sectionProps = (stage: ResearchStage): SectionProps => ({
    stage,
    artifact: artifacts.get(stage.name) ?? null,
    view,
    onCitation: (name, citation) => setDrawer({ stage: name, citation }),
  })

  return (
    <div className="flex flex-col gap-5">
      {header}

      {view.state === 'partial' && problems.length > 0 && (
        <Banner tone="warn" title={`${problems.length} 个段落未完成`}>
          {problems.map((stage) => stage.title).join('、')}：未完成；其余段落照常展示。
          研究报告页可对单段「重试本段」。
        </Banner>
      )}

      <div className="grid gap-4 desk:grid-cols-12 desk:items-start">
        <div className="desk:col-span-7">
          {companyStage && <CompanyCard {...sectionProps(companyStage)} />}
        </div>
        <div className="desk:col-span-5">
          {forecastStage && <ForecastCard {...sectionProps(forecastStage)} />}
        </div>
      </div>

      <div className="grid gap-4 desk:grid-cols-12 desk:items-start">
        <div className="desk:col-span-7">
          {financialStage && <FinancialCard {...sectionProps(financialStage)} />}
        </div>
        <div className="desk:col-span-5">
          {riskStage && <RiskCard {...sectionProps(riskStage)} />}
        </div>
      </div>

      <div className="grid gap-4 desk:grid-cols-12 desk:items-start">
        <div className="min-w-0 desk:col-span-7">
          <ValuationBasisPanel
            symbol={view.code}
            loader={demoLoader}
            title="估值指标"
            className="min-w-0"
          />
        </div>
        <div className="flex min-w-0 flex-col gap-4 desk:col-span-5">
          {valuationStage && <PercentileCard stage={valuationStage} artifact={artifacts.get('valuation') ?? null} />}
          <SectionCard
            title="六段状态"
            subtitle={status?.complete ? '六段齐备' : `失败 ${status?.failed.length ?? 0} · 待跑 ${status?.pending.length ?? 0}`}
            bodyClassName="p-2"
          >
            <StageRail
              stages={stages}
              onSelect={(name) => {
                if (view.code) navigate(reportHref(view.code, view, name))
              }}
            />
            {view.code && (
              <div className="mt-2 px-2 pb-1">
                <LinkButton to={reportHref(view.code, view)} className="w-full">
                  打开六阶段研究报告
                </LinkButton>
              </div>
            )}
          </SectionCard>
        </div>
      </div>

      <p className="text-caption leading-body text-text-muted">
        口径：六段产物只读自本机落盘文件；`facts` 缺失显示 —，不补 0、不重算比率。
        引用角标与〔未核实〕规则见研究报告页与 SPEC-RESEARCH-PROVENANCE。
      </p>

      <ProvenanceDrawer
        open={drawer !== null}
        onClose={() => setDrawer(null)}
        stageTitle={drawer ? (artifacts.get(drawer.stage)?.document.title ?? STAGE_TITLE[drawer.stage]) : ''}
        citation={drawer?.citation ?? null}
        index={drawerIndex}
      />
    </div>
  )
}
