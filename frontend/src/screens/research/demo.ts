/**
 * R8 页面演示数据（`?demo=1` 显式开启，页面带「演示数据」水印）。
 *
 * 形状与 `research/artifacts.build_document()` 逐键对齐；引用角标、〔未核实〕、
 * 六段状态、失败段都按真实产物的样子构造，用来：
 * 1. 在后端研究读取接口合入前，验证页面结构与五态；
 * 2. 截图取证（`?demo=1&state=…`）。
 *
 * 绝不在真实通道里回落到这份数据——演示源只能在 URL 显式要求时被选中。
 */

import {
  type StructuredMetrics,
  type ValuationBasis,
} from '../../api/financecoffer'
import {
  RESEARCH_STAGES,
  STAGE_TITLE,
  type ProvenanceSlot,
  type ProvenanceSource,
  type ResearchJob,
  type ResearchRunHandle,
  type ResearchSource,
  type ResearchStage,
  type ResearchStageName,
  type ResearchStatus,
  type StageDocument,
  type StageStatus,
} from '../../api/research'

export const DEMO_CODE = '600000'
export const DEMO_DATE = '2026-10-31'
export const DEMO_NAME = '浦发银行'

export type DemoVariant = 'full' | 'partial' | 'empty' | 'all-unverified'

function source(
  id: string,
  dataset: string,
  metric: string,
  stage: ResearchStageName,
  options: {
    endpoint?: string | null
    as_of?: string
    value?: number | string
    unit?: string
    verified?: boolean
    upstream_id?: string
  } = {},
): ProvenanceSource {
  return {
    id,
    dataset,
    params: { metric, stage, ...(options.upstream_id ? { upstream_id: options.upstream_id } : {}) },
    retrieved_at: '2026-10-30T08:15:00+08:00',
    value: options.value ?? null,
    unit: options.unit ?? null,
    endpoint: options.endpoint ?? null,
    as_of: options.as_of ?? '2025-12-31',
    url: null,
    verified: options.verified ?? true,
  }
}

function slot(sources: ProvenanceSource[], values: Record<string, string[]>): ProvenanceSlot {
  const mapped: ProvenanceSlot['values'] = {}
  for (const [key, ids] of Object.entries(values)) {
    mapped[key] = { sources: ids, unverified: false }
  }
  return { values: mapped, sources, unverified: sources.length === 0 }
}

const COMPANY_SOURCES = [
  source('S1', 'tickhub-cn', 'industry', 'company', { endpoint: '/v1/f10/company/600000' }),
  source('S2', 'tickhub-cn', 'revenue', 'company', {
    endpoint: '/v1/financials/600000',
    value: 120360000000,
    unit: '元',
  }),
]
const FINANCIAL_SOURCES = [
  source('S1', 'tickhub-cn', 'revenue', 'financial', {
    endpoint: '/v1/financials/600000',
    value: 120360000000,
    unit: '元',
  }),
  source('S2', 'tickhub-cn', 'net_profit', 'financial', {
    endpoint: '/v1/financials/600000',
    value: 8420000000,
    unit: '元',
  }),
  source('S3', 'computed', 'roe', 'financial', { value: 12.4, unit: '%' }),
]
const FORECAST_SOURCES = [
  source('S1', 'wire', 'eps_2026', 'forecast', {
    endpoint: '/v1/research/consensus/600000',
    as_of: '2026-10-28',
    value: 1.32,
    unit: '元',
  }),
  source('S2', 'wire', 'net_profit_growth', 'forecast', {
    endpoint: '/v1/research/consensus/600000',
    as_of: '2026-10-28',
    value: 8.5,
    unit: '%',
  }),
]
const VALUATION_SOURCES = [
  source('S1', 'computed', 'pe', 'valuation', { value: 5.2, unit: '倍' }),
  source('S2', 'computed', 'pb', 'valuation', { value: 0.58, unit: '倍' }),
]
const RISK_SOURCES = [
  source('S1', 'computed', 'drawdown_60d', 'risk', { value: 12.5, unit: '%' }),
  source('S2', 'tickhub-cn', 'pledge_ratio', 'risk', {
    endpoint: '/v1/f10/pledge/600000',
    value: 8.1,
    unit: '%',
  }),
]
const REPORT_SOURCES = [
  ...COMPANY_SOURCES.map((item, index) => ({ ...item, id: `S${index + 1}` })),
  ...FINANCIAL_SOURCES.map((item, index) => ({ ...item, id: `S${COMPANY_SOURCES.length + index + 1}` })),
  ...FORECAST_SOURCES.map((item, index) => ({
    ...item,
    id: `S${COMPANY_SOURCES.length + FINANCIAL_SOURCES.length + index + 1}`,
  })),
  ...VALUATION_SOURCES.map((item, index) => ({
    ...item,
    id: `S${COMPANY_SOURCES.length + FINANCIAL_SOURCES.length + FORECAST_SOURCES.length + index + 1}`,
  })),
  ...RISK_SOURCES.map((item, index) => ({
    ...item,
    id: `S${COMPANY_SOURCES.length + FINANCIAL_SOURCES.length + FORECAST_SOURCES.length + VALUATION_SOURCES.length + index + 1}`,
  })),
]

const NARRATIVE: Record<ResearchStageName, string[]> = {
  company: [
    '浦发银行是全国性股份制商业银行，主营公司金融、零售金融与资金业务，净利息收入仍是收入主体[1]。2025 年实现营业收入 1,203.6 亿元[2]，规模在股份行中处于中游。',
    '控股股东为上海国际集团有限公司，股权结构稳定；管理层近三年未发生重大变动[1]。公司披露的产能利用率约 78.4%〔未核实〕，该数字没有可核实的出处。',
    '结论：一家区域禀赋突出、对公底色明显的全国性股份行；资产质量与净息差是后续最需要跟踪的两条线。',
  ],
  financial: [
    '2025 年营业收入 1,203.6 亿元，同比增长 3.8%[1]；归母净利润 84.2 亿元，同比增长 5.1%[2]。收入增速慢于利润增速，成本端贡献大于规模扩张。',
    '加权 ROE 12.4%[3]，较上年基本持平；净息差继续承压，但信用成本回落对冲了部分影响。',
  ],
  forecast: [
    '近 30 日共有 12 家机构给出盈利预测，2026 年一致预期 EPS 1.32 元[1]，对应净利润同比增速 8.5%[2]。',
    '预测分歧主要来自净息差下行斜率与零售不良的处置节奏；一致性预期本身不构成买卖依据。',
  ],
  valuation: [
    '按 FinanceCoffer 快照口径，当前 PE 5.2 倍[1]、PB 0.58 倍[2]，均低于股份行中位数。',
    '估值分位处于近五年 21% 分位，低估值反映的是资产质量折价，而不是单纯的情绪折价。',
  ],
  risk: [
    '近 60 个交易日最大回撤 12.5%[1]，波动高于国有大行；质押比例 8.1%[2]，处于行业偏低水平。',
    '主要风险：净息差持续收窄、零售不良生成率抬头、房地产相关敞口减值超预期。',
  ],
  report: [
    '公司画像、财务、盈利预期、估值与风险五段已汇总为一份可追溯的研究报告；正文中的每个数字都带角标或〔未核实〕标记。',
    '综合结论：低估值与稳定分红提供安全边际，但净息差与资产质量决定弹性；在盈利预期上修之前，更适合作为防御性配置而非进攻标的[1]。',
  ],
}

const NOTES: Record<ResearchStageName, string[]> = {
  company: ['本段结论仅基于快照，未引入外部检索（DEC-006）。'],
  financial: ['同比增速由程序按快照计算，未做年化。'],
  forecast: ['一致预期来自 Wire，非本公司预测。'],
  valuation: ['估值数字的唯一来源是 facts；本段不重算 PE / PB（DEC-025）。'],
  risk: ['风险清单来自结构化产物，按影响程度排序。'],
  report: ['报告段只消费前五段已落盘产物，不重新取数（DEC-024）。'],
}

const FACTS: Record<ResearchStageName, Record<string, unknown>> = {
  company: {
    name: DEMO_NAME,
    industry: '股份制银行',
    main_business: '公司金融、零售金融、资金业务',
    controlling_shareholder: '上海国际集团有限公司',
    listed_board: '上交所主板',
    employees: 60800,
  },
  financial: {
    revenue: 120360000000,
    revenue_yoy: 3.8,
    net_profit: 8420000000,
    net_profit_yoy: 5.1,
    roe: 12.4,
    revenue_series: [986.5, 1042.3, 1108.7, 1203.6],
    net_profit_series: [71.2, 76.4, 80.1, 84.2],
  },
  forecast: {
    eps_2026: 1.32,
    eps_2027: 1.48,
    net_profit_growth: 8.5,
    institution_count: 12,
  },
  valuation: {
    basis: 'snapshot',
    basis_echo: 'snapshot',
    default_basis: 'snapshot',
    available_bases: ['snapshot'],
    pe: 5.2,
    pb: 0.58,
    roe: 0.124,
    pe_reason: null,
    pb_reason: null,
    roe_reason: null,
    quarter_basis: null,
    pe_percentile_5y: 21,
    pb_percentile_5y: 18,
  },
  risk: {
    drawdown_60d: 12.5,
    pledge_ratio: 8.1,
    bad_loan_ratio: 1.42,
  },
  report: {
    upstream: ['company', 'financial', 'forecast', 'valuation', 'risk'],
  },
}

const AI_EXTRAS: Partial<Record<ResearchStageName, Record<string, unknown>>> = {
  risk: {
    risks: [
      '净息差持续收窄，利息净收入承压',
      '零售不良生成率抬头，信用成本存在上行风险',
      '房地产相关敞口减值可能超预期',
    ],
  },
  report: {
    highlights: ['低估值', '分红稳定', '区域对公优势'],
  },
}

function markdownOf(document: StageDocument): string {
  const lines = [
    `# ${document.title}${document.status === 'degraded' ? '（降级）' : ''}`,
    '',
    `- 代码：${document.code}`,
    `- 日期：${document.date}`,
    `- 状态：${document.status} · mode=${document.mode ?? '-'} · 模型=${document.model ?? '-'}`,
    '',
    '## 叙述',
    '',
    ...document.narrative.flatMap((item) => [item, '']),
  ]
  const metrics = Object.entries(document.facts).filter(
    ([key, value]) =>
      key !== 'upstream' && ['string', 'number', 'boolean'].includes(typeof value),
  )
  if (metrics.length > 0) {
    lines.push('## 指标', '')
    for (const [key, value] of metrics) {
      lines.push(`- ${key}：${String(value)}`)
    }
    lines.push('')
  }
  if (document.notes.length > 0) {
    lines.push('## 备注', '')
    for (const note of document.notes) lines.push(`- ${note}`)
    lines.push('')
  }
  if (document.degraded) {
    lines.push('> 本段为降级产物（占位/纯文本），未走结构化输出。', '')
  }
  lines.push('## 来源', '')
  if (document.provenance.sources.length === 0) {
    lines.push('（本段无引用来源；正文中的数字一律标〔未核实〕。）')
  } else {
    document.provenance.sources.forEach((entry, index) => {
      lines.push(
        `[${index + 1}] ${entry.dataset} · ${entry.endpoint ?? '—'} · ${entry.as_of ?? '—'} · ${entry.url ?? '—'}`,
      )
    })
  }
  return `${lines.join('\n')}\n`
}

interface StageSeed {
  status: StageStatus
  sources?: ProvenanceSource[]
  values?: Record<string, string[]>
  narrative?: string[]
  notes?: string[]
  facts?: Record<string, unknown>
  ai?: Record<string, unknown>
  failure?: StageDocument['failure']
  degraded?: boolean
  mode?: string | null
  model?: string | null
}

function buildStage(name: ResearchStageName, seed: StageSeed): ResearchStage {
  const document: StageDocument = {
    code: DEMO_CODE,
    date: DEMO_DATE,
    stage: name,
    title: STAGE_TITLE[name],
    status: seed.status,
    mode: seed.mode ?? 'structured',
    model: seed.model ?? 'deepseek-v4.1-flash',
    degraded: seed.degraded ?? seed.status === 'degraded',
    narrative: seed.narrative ?? NARRATIVE[name],
    notes: seed.notes ?? NOTES[name],
    facts: seed.facts ?? FACTS[name],
    ai: seed.ai ?? AI_EXTRAS[name] ?? { narrative: NARRATIVE[name] },
    provenance: slot(seed.sources ?? [], seed.values ?? {}),
    failure: seed.failure ?? null,
    attempts: [],
  }
  return {
    name,
    title: STAGE_TITLE[name],
    status: seed.status,
    mode: document.mode,
    degraded: document.degraded,
    artifact: { document, markdown: markdownOf(document) },
  }
}

const FULL_SEEDS: Record<ResearchStageName, StageSeed> = {
  company: {
    status: 'ok',
    sources: COMPANY_SOURCES,
    values: { industry: ['S1'], revenue: ['S2'] },
  },
  financial: {
    status: 'ok',
    sources: FINANCIAL_SOURCES,
    values: { revenue: ['S1'], net_profit: ['S2'], roe: ['S3'] },
  },
  forecast: {
    status: 'degraded',
    sources: FORECAST_SOURCES,
    values: { eps_2026: ['S1'], net_profit_growth: ['S2'] },
    notes: [...NOTES.forecast, '一致预期覆盖机构数低于 20，样本代表性有限。'],
    degraded: true,
    mode: 'parsed',
  },
  valuation: {
    status: 'ok',
    sources: VALUATION_SOURCES,
    values: { pe: ['S1'], pb: ['S2'] },
  },
  risk: {
    status: 'ok',
    sources: RISK_SOURCES,
    values: { drawdown_60d: ['S1'], pledge_ratio: ['S2'] },
  },
  report: {
    status: 'ok',
    sources: REPORT_SOURCES,
    values: {},
  },
}

const UNVERIFIED_NARRATIVE: Record<ResearchStageName, string[]> = {
  company: [
    '浦发银行主营公司金融、零售金融与资金业务〔未核实〕；2025 年营业收入 1,203.6 亿元〔未核实〕。',
    '控股股东为上海国际集团有限公司〔未核实〕；本段没有任何可核实的引用来源。',
  ],
  financial: [
    '2025 年营业收入 1,203.6 亿元〔未核实〕，归母净利润 84.2 亿元〔未核实〕，ROE 12.4%〔未核实〕。',
    '全部数字缺出处，按规则只能标〔未核实〕；不做任何推断补全。',
  ],
  forecast: ['一致预期 EPS 1.32 元〔未核实〕，净利润增速 8.5%〔未核实〕。'],
  valuation: ['PE 5.2 倍〔未核实〕、PB 0.58 倍〔未核实〕；本段无引用来源。'],
  risk: ['近 60 日最大回撤 12.5%〔未核实〕，质押比例 8.1%〔未核实〕。'],
  report: ['前五段均无引用来源，报告中的数字一律标〔未核实〕；报告仍可交付，但不可作为审计依据。'],
}

function unverifiedDocument(name: ResearchStageName): ResearchStage {
  const seed: StageSeed = {
    status: 'degraded',
    sources: [],
    values: {},
    narrative: UNVERIFIED_NARRATIVE[name],
    notes: ['本段没有任何可核实来源，整段按〔未核实〕渲染。'],
    facts: name === 'report' ? {} : FACTS[name],
    degraded: true,
    mode: 'text',
  }
  return buildStage(name, { ...seed, status: 'degraded', degraded: true })
}

function assemble(
  seeds: Partial<Record<ResearchStageName, StageSeed>>,
  deleted: ResearchStageName[] = [],
): ResearchStatus {
  const stages = RESEARCH_STAGES.map((name) => {
    if (deleted.includes(name)) {
      return { name, title: STAGE_TITLE[name], status: 'pending' as const, artifact: null }
    }
    return buildStage(name, seeds[name] ?? FULL_SEEDS[name])
  })
  const failed = stages.filter((stage) => stage.status === 'failed').map((stage) => stage.name)
  const pending = stages.filter((stage) => stage.status === 'pending').map((stage) => stage.name)
  const available = stages.some((stage) => stage.status !== 'pending')
  return {
    code: DEMO_CODE,
    date: DEMO_DATE,
    available,
    complete: failed.length === 0 && pending.length === 0,
    checkpoint: `data/state/research_checkpoint/${DEMO_CODE}/${DEMO_DATE}.json`,
    updated_at: '2026-10-31T08:20:00+08:00',
    stages,
    pending,
    failed,
    source_status: failed.length > 0 || pending.length > 0 ? 'partial' : 'ok',
    reason: failed.length > 0 ? `六段未完成，缺：${[...failed, ...pending].join('、')}` : null,
    data_source_status: failed.length > 0 || pending.length > 0 ? 'partial' : 'ok',
  }
}

export function demoResearchStatus(variant: DemoVariant = 'full'): ResearchStatus {
  if (variant === 'empty') {
    return {
      code: DEMO_CODE,
      date: DEMO_DATE,
      available: false,
      complete: false,
      checkpoint: `data/state/research_checkpoint/${DEMO_CODE}/${DEMO_DATE}.json`,
      updated_at: null,
      stages: RESEARCH_STAGES.map((name) => ({
        name,
        title: STAGE_TITLE[name],
        status: 'pending' as const,
        artifact: null,
      })),
      pending: [...RESEARCH_STAGES],
      failed: [],
      source_status: 'partial',
      reason: '尚未运行 ldst research',
      data_source_status: 'missing',
    }
  }
  if (variant === 'all-unverified') {
    const stages = RESEARCH_STAGES.map(unverifiedDocument)
    return {
      code: DEMO_CODE,
      date: DEMO_DATE,
      available: true,
      complete: false,
      checkpoint: `data/state/research_checkpoint/${DEMO_CODE}/${DEMO_DATE}.json`,
      updated_at: '2026-10-31T08:20:00+08:00',
      stages,
      pending: [],
      failed: [],
      source_status: 'degraded',
      reason: '全部段落无引用来源，数字均为〔未核实〕',
      data_source_status: 'partial',
    }
  }
  if (variant === 'partial') {
    return assemble(
      {
        valuation: {
          status: 'failed',
          sources: [],
          narrative: [],
          notes: ['估值段生成失败，其他段不受影响'],
          facts: {},
          failure: {
            kind: 'format',
            message: '模型输出不是合法 JSON（第 1 次重试后仍失败）',
            action: '重试本段；仍失败则检查模型配置或稍后再试',
            retryable: true,
          },
        },
        report: {
          status: 'pending',
          sources: [],
          narrative: [],
          notes: [],
          facts: {},
        },
      },
    )
  }
  return assemble({})
}

/**
 * 估值面板的演示取数（`?demo=1` 时注入 `ValuationBasisPanel.loader`）。
 * 只在演示模式使用；真实通道仍然走 FinanceCoffer 只读 REST。
 */
export function createDemoValuationLoader(): (
  symbol: string,
  basis: ValuationBasis,
  signal: AbortSignal,
) => Promise<StructuredMetrics> {
  return async (symbol, basis) => ({
    symbol,
    basis,
    default_basis: 'snapshot',
    available_bases: ['snapshot'],
    pe: basis === 'snapshot' ? 5.2 : null,
    pe_source: 'quote.pe_ttm',
    pe_reason: basis === 'snapshot' ? null : 'basis_not_built',
    pe_cross: null,
    pb: basis === 'snapshot' ? 0.58 : null,
    pb_reason: basis === 'snapshot' ? null : 'basis_not_built',
    roe: basis === 'snapshot' ? 0.124 : null,
    roe_reason: basis === 'snapshot' ? null : 'basis_not_built',
    price_date: DEMO_DATE,
    price_close: '8.6400',
    report_date: '2025-12-31',
    quarter_basis: null,
  })
}

/**
 * 演示源：状态可重载、重试任务两次轮询后成功（方便截图与测试）。
 * 只在 `?demo=1` 时使用。
 */
export function createDemoResearchSource(
  variant: DemoVariant = 'full',
): ResearchSource {
  let current = demoResearchStatus(variant)
  const jobs = new Map<string, { stage: ResearchStageName; ticks: number }>()
  let seed = 0

  return {
    status: async () => current,
    run: async (_code, _date, only): Promise<ResearchRunHandle> => {
      const id = `demo-job-${++seed}`
      jobs.set(id, { stage: only, ticks: 0 })
      return { job_id: id, status: 'queued' }
    },
    job: async (jobId): Promise<ResearchJob> => {
      const entry = jobs.get(jobId)
      if (!entry) {
        return { id: jobId, status: 'failed', progress: 1, error: '未知任务' }
      }
      entry.ticks += 1
      if (entry.ticks < 2) {
        return {
          id: jobId,
          status: 'running',
          stage: entry.stage,
          progress: 0.45,
          step: `${STAGE_TITLE[entry.stage]}段重跑中`,
        }
      }
      const stage = assemble({}).stages.find((item) => item.name === entry.stage)
      current = {
        ...current,
        stages: current.stages.map((item) => (item.name === entry.stage && stage ? stage : item)),
        failed: current.failed.filter((name) => name !== entry.stage),
        pending: current.pending.filter((name) => name !== entry.stage),
      }
      current = {
        ...current,
        complete: current.failed.length === 0 && current.pending.length === 0,
        source_status:
          current.failed.length > 0 || current.pending.length > 0 ? 'partial' : 'ok',
      }
      return {
        id: jobId,
        status: 'ok',
        stage: entry.stage,
        progress: 1,
        step: `${STAGE_TITLE[entry.stage]}段已完成`,
      }
    },
  }
}
