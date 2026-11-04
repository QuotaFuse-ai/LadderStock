import { describe, expect, it } from 'vitest'

import { ApiError } from '../src/api'
import {
  classify404,
  describeResearchError,
  isResearchEndpointMissing,
  normalizeResearchCode,
  parseResearchJob,
  parseResearchOverview,
  parseResearchStatus,
  stageArtifact,
  stageOf,
} from '../src/api/research'

const DOC = {
  code: '600000',
  date: '2026-10-31',
  stage: 'financial',
  status: 'ok',
  mode: 'structured',
  degraded: false,
  narrative: ['营收 1,203.6 亿元[1]'],
  notes: [],
  facts: { revenue: 120360000000 },
  provenance: {
    values: { revenue: { sources: ['S1'], unverified: false } },
    sources: [
      { id: 'S1', dataset: 'tickhub-cn', endpoint: '/v1/financials/600000', as_of: '2025-12-31', verified: true },
    ],
    unverified: false,
  },
  failure: null,
}

function payload() {
  return {
    code: '600000',
    date: '2026-10-31',
    available: true,
    complete: false,
    checkpoint: 'data/state/research_checkpoint/600000/2026-10-31.json',
    updated_at: '2026-10-31T08:20:00+08:00',
    source_status: 'partial',
    stages: [
      { name: 'financial', status: 'ok', artifact: { document: DOC, markdown: '# 财务\n' } },
      { name: 'valuation', status: 'failed', mode: 'structured' },
    ],
  }
}

describe('证券代码', () => {
  it('接受 6 位数字与交易所后缀，统一大写', () => {
    expect(normalizeResearchCode('600000')).toBe('600000')
    expect(normalizeResearchCode(' 600000.sh ')).toBe('600000.SH')
    expect(normalizeResearchCode('000001.SZ')).toBe('000001.SZ')
  })

  it('拒绝非法输入', () => {
    expect(normalizeResearchCode('60000')).toBeNull()
    expect(normalizeResearchCode('ABCDEF')).toBeNull()
    expect(normalizeResearchCode('')).toBeNull()
    expect(normalizeResearchCode(null)).toBeNull()
  })
})

describe('研究状态解析', () => {
  it('补齐六段、保持规范顺序，并解析 artifact', () => {
    const status = parseResearchStatus(payload(), '600000')
    expect(status.stages.map((stage) => stage.name)).toEqual([
      'company',
      'financial',
      'forecast',
      'valuation',
      'risk',
      'report',
    ])
    const financial = stageOf(status, 'financial')
    expect(financial.status).toBe('ok')
    expect(stageArtifact(status, 'financial')?.document.facts.revenue).toBe(120360000000)
    expect(stageArtifact(status, 'financial')?.markdown).toBe('# 财务\n')
    expect(stageArtifact(status, 'financial')?.document.provenance.sources[0].verified).toBe(true)

    const company = stageOf(status, 'company')
    expect(company.status).toBe('pending')
    expect(company.artifact).toBeNull()
    expect(status.failed).toEqual(['valuation'])
    expect(status.pending).toEqual(['company', 'forecast', 'risk', 'report'])
  })

  it('兼容 MCP 的 payload.research 外壳', () => {
    const status = parseResearchStatus({ payload: { research: payload() } }, '600000')
    expect(status.stages).toHaveLength(6)
    expect(stageOf(status, 'financial').status).toBe('ok')
  })

  it('缺少 stages 视为解析失败，不猜结构', () => {
    expect(() => parseResearchStatus({ code: '600000', date: '2026-10-31' })).toThrow(/stages/)
  })

  it('支持 markdown 的 md 别名', () => {
    const raw = payload()
    raw.stages[0].artifact = { document: DOC, md: '# 财务（md）' } as never
    const status = parseResearchStatus(raw)
    expect(stageArtifact(status, 'financial')?.markdown).toBe('# 财务（md）')
  })
})

describe('研究任务解析', () => {
  it('保留进度与终态', () => {
    expect(
      parseResearchJob({ job_id: 'job-1', status: 'running', stage: 'valuation', progress: 0.45, step: '重跑估值段' }),
    ).toEqual({ id: 'job-1', status: 'running', stage: 'valuation', progress: 0.45, step: '重跑估值段', error: null })
  })

  it('未知状态按 running 处理，进度收敛到 0–1', () => {
    const job = parseResearchJob({ id: 'j', status: 'weird', progress: 9 })
    expect(job.status).toBe('running')
    expect(job.progress).toBe(1)
  })
})

describe('概览解析（QUOT-400）', () => {
  const ENVELOPE = {
    payload: {
      code: '600000',
      name: '浦发银行',
      as_of: '2026-11-03',
      data_source_status: 'ok',
      kline: {
        bars: [
          { date: '2026-11-02', open: 9.4, high: 9.7, low: 9.3, close: 9.6, volume: 75000 },
          { date: '2026-11-03', open: 9.6, high: 9.9, low: 9.5, close: 9.8, volume: 80000 },
        ],
        count: 2,
        requested: 120,
        reason: null,
      },
      quote: {
        price: 9.8,
        prev_close: 9.6,
        change_pct: 2.08,
        volume: 80000,
        amount: 77566,
        timestamp: '20261104104221',
      },
      quote_reason: null,
      financials: {
        report_name: '2026三季报',
        report_date: '2026-09-30',
        eps: 4.57,
        revenue: 264852000000,
        net_profit: 116476000000,
        roe: 10.1,
        revenue_yoy: 5.34,
        profit_yoy: 2.38,
      },
      financials_reason: null,
    },
    data_source_status: 'ok',
    data_as_of: '2026-11-03',
    meta: { topic: 'research_overview' },
  }

  it('解析四段信封：K 线 / 快照 / 财务三块齐全', () => {
    const overview = parseResearchOverview(ENVELOPE, '600000')
    expect(overview.code).toBe('600000')
    expect(overview.dataSourceStatus).toBe('ok')
    expect(overview.kline.bars).toHaveLength(2)
    expect(overview.kline.bars[1].close).toBe(9.8)
    expect(overview.quote.quote?.price).toBe(9.8)
    expect(overview.financials.financials?.report_name).toBe('2026三季报')
    expect(overview.as_of).toBe('2026-11-03')
  })

  it('缺数态：三块全缺 → missing + null + reason，不补 0', () => {
    const overview = parseResearchOverview(
      {
        payload: {
          code: '600000',
          name: null,
          as_of: null,
          data_source_status: 'missing',
          kline: { bars: [], count: 0, requested: 120, reason: '本地 daily_k 缓存里没有这只代码的行' },
          quote: null,
          quote_reason: '本地 spot_quote 缓存里没有这只代码的快照',
          financials: null,
          financials_reason: '本地 finance_indicators 无缓存',
        },
        data_source_status: 'missing',
        data_as_of: null,
        meta: {},
      },
      '600000',
    )
    expect(overview.dataSourceStatus).toBe('missing')
    expect(overview.kline.bars).toHaveLength(0)
    expect(overview.kline.reason).toMatch(/daily_k/)
    expect(overview.quote.quote).toBeNull()
    expect(overview.quote.reason).toMatch(/spot_quote/)
    expect(overview.financials.financials).toBeNull()
    expect(overview.financials.reason).toMatch(/finance_indicators/)
    expect(overview.as_of).toBeNull()
  })

  it('半缺态：K 线有根但 quote/financials 缺 → 主指标 ok，块各自 null', () => {
    const overview = parseResearchOverview(
      {
        payload: {
          code: '600000',
          name: null,
          as_of: '2026-11-03',
          data_source_status: 'ok',
          kline: { bars: [{ date: '2026-11-03', open: 1, high: 1, low: 1, close: 1, volume: null }], count: 1, requested: 120, reason: null },
          quote: null,
          quote_reason: 'no quote',
          financials: null,
          financials_reason: 'no finance',
        },
        data_source_status: 'ok',
        data_as_of: '2026-11-03',
        meta: {},
      },
      '600000',
    )
    expect(overview.dataSourceStatus).toBe('ok')
    expect(overview.quote.quote).toBeNull()
    expect(overview.financials.financials).toBeNull()
    expect(overview.kline.bars[0].volume).toBeNull()
  })

  it('兼容平铺（无 payload 外壳）', () => {
    const flat = { ...ENVELOPE.payload }
    const overview = parseResearchOverview(flat, '600000')
    expect(overview.kline.count).toBe(2)
  })
})

describe('错误文案', () => {
  it('路由未接（FastAPI 默认 404）→「研究接口未接入」，这是唯一的接口缺失态', () => {
    const error = new ApiError('http', '研究接口 /api/research/600000 返回 HTTP 404', 404, 'Not Found')
    expect(isResearchEndpointMissing(error)).toBe(true)
    expect(classify404(error)).toBe('missing')
    expect(describeResearchError(error).title).toBe('研究接口未接入')
  })

  it('业务空态 404（还没有任何研究记录）不是「接口未接入」，是「这只还没跑过研究」', () => {
    const error = new ApiError(
      'http',
      '研究接口 /api/research/600519 返回 HTTP 404：600519 还没有任何研究记录（先运行 ldst research 600519）',
      404,
      '600519 还没有任何研究记录（先运行 ldst research 600519）',
    )
    expect(isResearchEndpointMissing(error)).toBe(false)
    expect(classify404(error)).toBe('no-record')
    expect(describeResearchError(error).title).toBe('这只还没跑过研究')
  })

  it('「没有研究产物」同属业务空态（按日查询无产物的文案）', () => {
    const error = new ApiError('http', 'not found', 404, '600001 在 2026-10-30 没有研究产物')
    expect(classify404(error)).toBe('no-record')
  })

  it('未知代码 404 →「未知证券代码」，既不是接口缺失也不是空态', () => {
    const error = new ApiError('http', 'not found', 404, '999999 是未知证券代码（A 股个股只支持…）')
    expect(isResearchEndpointMissing(error)).toBe(false)
    expect(classify404(error)).toBe('unknown-code')
    expect(describeResearchError(error).title).toBe('未知证券代码')
  })

  it('503 →「研究引擎不可用」', () => {
    const info = describeResearchError(new ApiError('http', 'down', 503, '研究引擎不可用：读取研究状态失败'))
    expect(info.title).toBe('研究引擎不可用')
  })

  it('未连接 → 研究接口未接入', () => {
    const info = describeResearchError(new ApiError('not-connected', 'down'))
    expect(info.title).toBe('研究接口未接入')
    expect(info.detail).toMatch(/ldst run/)
  })
})
