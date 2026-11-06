import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Check, Copy, FileText, RefreshCw, RotateCcw } from 'lucide-react'

import {
  RESEARCH_STAGES,
  STAGE_STATUS_LABEL,
  STAGE_TITLE,
  describeResearchError,
  stageOf,
  type ResearchStage,
  type ResearchStageName,
  type ResearchStatus,
  type ResearchOverview,
  type StageArtifact,
  type StageDocument,
} from '../../api/research'
import { buildCitationIndex } from '../../lib/citation'
import { MISSING, formatTime } from '../../lib/format'
import { cn } from '../../lib/cn'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  asSourceBadgeStatus,
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  EmptyState,
  ErrorBlock,
  MetricCard,
  SectionCard,
  Skeleton,
  SkeletonText,
  SourceStatusBadge,
  Sparkline,
  Tabs,
  type Column,
} from '../../components/ui'
import { MarkdownView } from '../../components/research/MarkdownView'
import { ProvenanceDrawer } from '../../components/research/ProvenanceDrawer'
import { ResearchSymbolBar } from '../../components/research/ResearchSymbolBar'
import { StageRail } from '../../components/research/StageRail'
import { LinkButton, NUMBER_CHIP, PercentileBar } from '../../components/research/bits'
import { CompareSection } from '../../components/research/sections/CompareSection'
import { EventSection } from '../../components/research/sections/EventSection'
import { ReportSection } from '../../components/research/sections/ReportSection'
import { ThesisSection } from '../../components/research/sections/ThesisSection'
import { ResearchAiDock } from '../../components/research/sections/AiDock'
import { ValuationBasisPanel } from '../../components/valuation/ValuationBasisPanel'
import { createDemoValuationLoader } from './demo'
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
import { useAsync } from '../../lib/useAsync'
import OverviewTab from './OverviewTab'

/**
 * `/research/:code` 个股深挖（R8 / QUOT-448）。
 *
 * 页头是标的搜索（两个分页共用，换股只改代码、保留 `demo / variant / date / state`）：
 * - 概览（默认）：行情条 → 走势 K 线 → 财报前瞻（`OverviewTab` 内的 `TrendSection` / `EarningsSection`）。
 * - 深度研究：六段状态卡 + 估值（既有产物，入口不删）。
 * 两个 tab 之后是共用的 `pageTail`：事件回看 → 我的论点与判断 → 研究报告 →
 * 报告对比 / 分享卡 → 研究助手抽屉；块序与 tab 无关，照参考库 `pages/Research.tsx`。
 * 各块都是独立组件文件，后续任务只改自己那一个。
 * 所有数字都来自落盘产物、缓存或注入的 facts，缺失显示 `—` + 原因，不补 0、不计算新比率。
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

/**
 * 换标的时原样带走的取证参数（QUOT-448）。`tab` 不在此列：换股票后回到概览，
 * 免得刚输完代码就落到上一只的深度研究里。
 */
const CARRIED_PARAMS = ['demo', 'variant', 'date', 'state'] as const

function stageBadgeTone(stage: ResearchStage): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (stage.status === 'ok') return 'ok'
  if (stage.status === 'degraded') return 'warn'
  if (stage.status === 'failed') return 'danger'
  return 'neutral'
}

function Fact({ label, value, wide }: { label: string; value: string | null; wide?: boolean }) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-half', wide && 'desk:col-span-2')}>
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
    // 三列而不是五列：本卡在 `desk:` 下占 7/12（约 690px），五列会把每卡压到 130px，
    // 「1,203.6」这种七位数字连 `text-page` 都放不下，数值会顶穿卡片右边。
    <div className="grid grid-cols-2 gap-3 rail:grid-cols-3 desk:grid-cols-3">
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
        `这一段还没有落盘产物；去报告页点「${failed ? '重试本段' : '运行本段'}」生成，概览不受影响。`
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

/**
 * 卡片底部的来源行。
 *
 * 原本这里放的是 `CardFooter`，靠外层 `-mx-4 -mb-4` 负边距把页脚从 `SectionCard` 的
 * `p-4` 体里顶出去（同屏 4 次）。负边距是「组件没有 footer 插槽」的补偿写法：
 * 一旦卡片内距改了，四处一起错位。现在改成体内收尾行——上边框分隔、随正文自然收尾，
 * 不需要知道父级的内距是多少。`SectionCard` 补 `footer` 插槽后（已记给组件层）可再收一次。
 */
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
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-caption text-text-secondary">
      {/* 数字单独套 .ldst-num：它会换字体族，套整句会让中文也走数字字体 */}
      <span>
        来源 <span className="ldst-num">{sources.length}</span> 条 · 已核实{' '}
        <span className="ldst-num">{verified}</span> 条 · 未核实{' '}
        <span className="ldst-num">{sources.length - verified}</span> 条
      </span>
      <Button variant="ghost" onClick={() => onOpen(stage)}>
        查看来源
      </Button>
    </div>
  )
}

function SeriesCell({ series }: { series: SeriesFact }) {
  const first = series.values[0]
  const last = series.values[series.values.length - 1]
  const tone = last > first ? 'up' : last < first ? 'down' : 'flat'
  return (
    <Sparkline
      values={series.values}
      tone={tone}
      ariaLabel={`${series.label}，${series.values.length} 个数据点，从 ${first} 到 ${last}`}
    />
  )
}

/**
 * 历史序列走 C-09 `DataTable`（原来是手写 `ul`/`li` 三列：标签 + 数字 + 走势线，
 * 折行时三者各自换行、行间不成列）。数字列 `numeric` 右对齐 + `.ldst-num`，
 * 走势列定宽不随标签长度抖；数值一律用 `facts` 里的原值，页面不重算。
 */
const SERIES_COLUMNS: Array<Column<SeriesFact>> = [
  {
    key: 'label',
    header: '指标',
    truncate: true,
    sort: (a, b) => a.label.localeCompare(b.label, 'zh-CN'),
    render: (row) => row.label,
  },
  {
    key: 'range',
    header: '首期 → 末期',
    numeric: true,
    width: '160px',
    // 自动布局下列宽只是提示，两个数字加箭头会被挤成两行；数字列折行比横向滚动更难读
    className: 'whitespace-nowrap',
    render: (row) => `${row.values[0]} → ${row.values[row.values.length - 1]}`,
  },
  {
    key: 'points',
    header: '期数',
    numeric: true,
    width: '56px',
    render: (row) => row.values.length,
  },
  {
    key: 'trend',
    header: '走势',
    align: 'right',
    width: '112px',
    render: (row) => <SeriesCell series={row} />,
    cellTitle: (row) => `${row.label} 走势`,
  },
]

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
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 desk:grid-cols-2">
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
        <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
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
              <DataTable<SeriesFact>
                columns={SERIES_COLUMNS}
                rows={series}
                rowKey={(row) => row.key}
                caption="首期 → 末期与期数取自 facts 的原值，页面不重算、不补点"
                density="compact"
                stickyFirstColumn={false}
              />
            )}
          </div>
          <Narrative document={document} onCitation={(n) => onCitation(stage.name, n)} />
        </>
      )}
      {artifact && (
        <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
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
        <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
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
          {/* 断点表达与上方 MetricRow 同一条（`grid-cols-2 rail:grid-cols-3`）：
              原来这里用 `min-[1024px]:` 自己造了一档，同一屏两套断点写法。 */}
          <div className="grid grid-cols-2 gap-3 rail:grid-cols-3">
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
                    <span className={cn(NUMBER_CHIP, 'ldst-num mt-half bg-warn-soft text-warn')}>
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
        <SourceFoot stage={stage.name} artifact={artifact} onOpen={(name) => onCitation(name, null)} />
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

/**
 * 整屏 loading 骨架：与 settle 后的「三行 7/5 双卡」同形。
 *
 * 原来这里是 `ScreenSkeleton metrics={5} rows={6}`——五张指标卡加一张表格，
 * 而这屏正文是六张内容卡，加载完成时整页重排（QUOT-309 审计 P2「骨架与正文不同源」）。
 * 只借用 `components/ui` 的骨架件，不新造样式。
 */
function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载…</span>
      {Array.from({ length: 3 }).map((_, row) => (
        <div key={row} className="grid gap-4 desk:grid-cols-12">
          <Card className="min-w-0 desk:col-span-7">
            <CardHeader title="区块标题" subtitle="状态 · 来源" />
            <CardBody className="flex flex-col gap-4">
              <Skeleton className="h-5 w-1/4" />
              <SkeletonText lines={row === 1 ? 5 : 3} />
            </CardBody>
          </Card>
          <Card className="min-w-0 desk:col-span-5">
            <CardHeader title="区块标题" subtitle="状态 · 来源" />
            <CardBody>
              <SkeletonText lines={4} />
            </CardBody>
          </Card>
        </div>
      ))}
    </div>
  )
}

/** 深挖页的两个视角（QUOT-400）：概览（走势 + 财报，不依赖六段）与深度研究（六段）。 */
type DeepDiveTab = 'overview' | 'research'

const DEEP_DIVE_TABS = [
  { value: 'overview' as const, label: '概览' },
  { value: 'research' as const, label: '深度研究' },
]

export default function ResearchDetailScreen() {
  const view = useResearchView()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [drawer, setDrawer] = useState<DrawerState>(null)
  const [copied, setCopied] = useState(false)

  // tab 记在地址里（?tab=research），刷新、后退都还在；缺省概览（QUOT-400）
  const tab: DeepDiveTab = params.get('tab') === 'research' ? 'research' : 'overview'
  const pickTab = (next: DeepDiveTab) => {
    const query = new URLSearchParams(params)
    if (next === 'overview') query.delete('tab')
    else query.set('tab', next)
    setParams(query, { replace: true })
  }

  /**
   * 顶部搜索选中一只后换页（QUOT-448）：只改路径里的代码，当前 `demo / variant /
   * date / state` 原样带过去，取证链接不会因为换股断掉。
   */
  const openResearch = (next: string) => {
    const query = new URLSearchParams()
    for (const key of CARRIED_PARAMS) {
      const value = params.get(key)
      if (value !== null) query.set(key, value)
    }
    const search = query.toString()
    navigate(`/research/${encodeURIComponent(next)}${search ? `?${search}` : ''}`)
  }

  // 概览不依赖六段产物：拿到数据就渲染，拿不到按缺数态处理
  const overviewRequest = useAsync<ResearchOverview>(
    (signal) => view.source.overview(view.code as string, signal),
    [view.code, view.source],
    { enabled: Boolean(view.code) && tab === 'overview' },
  )

  const researchCommand = view.code ? `ldst research ${view.code} --date ${view.date}` : null
  const copyCommand = () => {
    if (!researchCommand) return
    const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
    if (!clipboard?.writeText) return
    void clipboard
      .writeText(researchCommand)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setCopied(false))
  }

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
  // 计数与左栏 StageRail 读同一份 `stages`，避免「副标题 0 段待跑 / rail 六段待跑」自相矛盾
  const failedCount = stages.filter((stage) => stage.status === 'failed').length
  const pendingCount = stages.filter((stage) => stage.status === 'pending').length

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

  const refreshing =
    tab === 'overview'
      ? overviewRequest.status === 'loading'
      : (view.requesting && view.state !== 'loading')

  const tabsBar = (
    <Tabs<DeepDiveTab> tabs={DEEP_DIVE_TABS} value={tab} onChange={pickTab} ariaLabel="个股深挖视图" />
  )

  // 顶部标的搜索两个分页共用（参考库页头口径）：换股只改代码，不改 tab
  const displayName = view.name ?? overviewRequest.data?.name ?? null
  const symbolBar = <ResearchSymbolBar code={view.code} name={displayName} onOpen={openResearch} />

  const header = (
    <PageHeader
      title={title}
      subtitle={view.code ? `${view.code} · ${view.date} · 个股深挖` : '个股深挖'}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          <DemoStateBadge state={view.forced} />
          {status && (
            <SourceStatusBadge
              status={asSourceBadgeStatus(status.data_source_status) ?? status.source_status}
              detail={status.reason}
            />
          )}
        </div>
      }
      meta={<span className="ldst-num text-caption text-text-secondary">{summary}</span>}
      refresh={{
        loading: refreshing,
        onClick: () => {
          if (tab === 'overview') overviewRequest.reload()
          view.reload()
        },
      }}
      actions={
        <>
          {view.code && (
            <LinkButton to={reportHref(view.code, view)} variant="primary" leadingIcon={<FileText className="size-3.5" aria-hidden="true" />}>
              研究报告
            </LinkButton>
          )}
        </>
      }
    />
  )

  /**
   * 页脚的共用块（QUOT-448 整页编排）：事件回看 → 我的论点与判断 →
   * 研究报告 → 报告对比（含分享卡）→ 研究助手抽屉。
   *
   * 放在分页之外、两个 tab 共用同一份，块序不随 tab 变化；每个块按验收要求
   * 独立成文件，后续任务（QUOT-451/452/453…）只改自己那一个文件。
   */
  const pageTail = (
    <>
      <EventSection code={view.code} date={view.date} />
      <ThesisSection code={view.code} name={displayName} />
      <ReportSection
        code={view.code}
        href={view.code ? reportHref(view.code, view) : null}
        stages={{ total: stages.length, ready: artifacts.size }}
      />
      <CompareSection
        code={view.code}
        reportHref={view.code ? reportHref(view.code, view) : null}
        source={view.source}
        date={view.date}
        demo={view.demo}
        overview={overviewRequest.data}
        name={displayName}
      />
      <ResearchAiDock
        code={view.code}
        name={displayName}
        scope={{ tab, date: view.date }}
        overview={overviewRequest.data}
      />
    </>
  )

  if (tab === 'overview') {
    // 代码不合法是路径级问题，与 tab 无关：概览也直接给错误出口
    if (!view.code) {
      return (
        <div className="flex flex-col gap-5">
          {header}
          {symbolBar}
          {tabsBar}
          <ErrorBlock
            title="证券代码不合法"
            detail="路径应为 /research/600000（6 位数字，可带 .SH / .SZ / .BJ 后缀）。"
            code="E_CODE"
          />
        </div>
      )
    }
    // 概览 tab：不依赖六段状态机，六段还没跑过也能出走势与财报（QUOT-400 核心诉求）
    return (
      <div className="flex flex-col gap-5">
        {header}
        {symbolBar}
        {tabsBar}
        <OverviewTab
          overview={overviewRequest.data}
          code={view.code}
          error={overviewRequest.status === 'error' ? overviewRequest.error : null}
          requesting={overviewRequest.status === 'loading'}
          reload={overviewRequest.reload}
        />
        {pageTail}
      </div>
    )
  }

  if (view.state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {symbolBar}
        {tabsBar}
        <DetailSkeleton />
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
        {symbolBar}
        {tabsBar}
        <ErrorBlock {...info} onRetry={view.code ? view.reload : undefined} />
        <Link
          to="/"
          className="w-fit text-caption text-info underline underline-offset-2 transition-colors duration-fast ease-ldst hover:text-accent-hover"
        >
          回到首页
        </Link>
      </div>
    )
  }

  if (view.state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {symbolBar}
        {tabsBar}
        <OfflineBanner onRetry={view.reload} />
        <EmptyState
          title="离线：暂时读不到研究产物"
          description="本机服务恢复后点「重试」会重新读取；页面不会展示缓存以外的旧数据。"
        />
      </div>
    )
  }

  if (view.state === 'empty') {
    // QUOT-400：404 三态在空态里的落点——「没跑过研究」给可复制命令；
    // 只有路由本身没接（FastAPI 默认 404）才说「接口未接入」。
    const noRecord = view.notFoundKind === 'no-record'
    return (
      <div className="flex flex-col gap-5">
        {header}
        {symbolBar}
        {tabsBar}
        <EmptyState
          icon={<FileText aria-hidden="true" />}
          title={
            noRecord
              ? '这只还没跑过研究'
              : view.endpointMissing
                ? '研究读取接口未接入'
                : '还没有研究产物'
          }
          description={
            view.endpointMissing
              ? '后端尚未提供 /api/research/* 只读接口（HTTP 404 Not Found）；接口上线后本页会自动读取落盘产物。当前不会伪造任何数据。'
              : noRecord
                ? '概览标签已经可以直接看走势与财报；跑一次研究就能看六段深度内容。'
                : `本机还没有 ${view.code ?? '<code>'} 在 ${view.date} 的六段深度产物；概览标签可直接查看走势与财报，点「刷新」重新读取。`
          }
          action={
            noRecord && researchCommand ? (
              <Button
                variant="secondary"
                onClick={copyCommand}
                leadingIcon={
                  copied ? (
                    <Check className="size-3.5" aria-hidden="true" />
                  ) : (
                    <Copy className="size-3.5" aria-hidden="true" />
                  )
                }
              >
                {copied ? '已复制' : '复制命令'}
              </Button>
            ) : (
              <Button
                variant="secondary"
                onClick={view.reload}
                leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
              >
                刷新
              </Button>
            )
          }
        />
        {noRecord && researchCommand && (
          <code className="ldst-num w-fit rounded-xs bg-surface-3 px-2 py-1 text-caption text-text-secondary">
            {researchCommand}
          </code>
        )}
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
      {symbolBar}
      {tabsBar}

      {view.state === 'partial' && problems.length > 0 && (
        <Banner tone="warn" title={`${problems.length} 个段落未完成`}>
          {problems.map((stage) => stage.title).join('、')}：未完成；其余段落照常展示。
          研究报告页可对单段「重试本段」。
        </Banner>
      )}

      <div className="grid gap-4 desk:grid-cols-12 desk:items-start">
        <div className="min-w-0 desk:col-span-7">
          {companyStage && <CompanyCard {...sectionProps(companyStage)} />}
        </div>
        <div className="min-w-0 desk:col-span-5">
          {forecastStage && <ForecastCard {...sectionProps(forecastStage)} />}
        </div>
      </div>

      <div className="grid gap-4 desk:grid-cols-12 desk:items-start">
        <div className="min-w-0 desk:col-span-7">
          {financialStage && <FinancialCard {...sectionProps(financialStage)} />}
        </div>
        <div className="min-w-0 desk:col-span-5">
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
            subtitle={
              status?.complete
                ? '六段齐备'
                : `失败 ${failedCount} · 待跑 ${pendingCount} · 共 ${stages.length} 段`
            }
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

      {pageTail}

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
