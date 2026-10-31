import { describe, expect, it } from 'vitest'

import { ApiError } from '../src/api'
import {
  describeResearchError,
  isResearchEndpointMissing,
  normalizeResearchCode,
  parseResearchJob,
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

describe('错误文案', () => {
  it('404 是「尚未生成研究产物」，不是页面故障', () => {
    const error = new ApiError('http', 'not found', 404)
    expect(isResearchEndpointMissing(error)).toBe(true)
    expect(describeResearchError(error).title).toBe('尚未生成研究产物')
  })

  it('未连接 → 研究接口未接入', () => {
    const info = describeResearchError(new ApiError('not-connected', 'down'))
    expect(info.title).toBe('研究接口未接入')
    expect(info.detail).toMatch(/ldst run/)
  })
})
