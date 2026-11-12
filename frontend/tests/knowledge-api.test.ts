import { describe, expect, it } from 'vitest'

import { ApiError } from '../src/api'
import {
  describeKnowledgeError,
  inferKnowledgeMarket,
  isKnowledgeMissing,
  knowledgePath,
  normalizeKnowledgeMarket,
  normalizeKnowledgeSymbol,
  parseKnowledgeDossier,
} from '../src/api/knowledge'

/**
 * QUOT-567：知识库档案接口的解析与错误口径。
 *
 * 载荷形状取自生产（浦发 600000）：扁平 JSON，`status` 由后端现算，
 * 404 的 `detail` 是「档案不存在：SH:600000」，与「接口没接」要分得开。
 */
function dossier(overrides: Record<string, unknown> = {}) {
  return {
    market: 'SH',
    symbol: '600000',
    name: '浦发银行',
    as_of: '2026-11-09',
    status: 'fresh',
    valid_days: 90,
    sources: [
      {
        source: 'tencent',
        tool: 'tencent_quote_batch',
        confidence: 'A',
        fetched_at: '2026-11-09T15:02:11+08:00',
        trace_id: 'a9581b29e56f48de',
      },
    ],
    body: '## 1. 公司速览\n\n浦发银行是一家股份制商业银行。',
    recalled: { as_of: '2026-09-30', status: 'fresh', valid_days: 90, truncated: false, text: '旧档案正文' },
    ...overrides,
  }
}

describe('市场与代码', () => {
  it('按前缀推断市场：60/68→SH，00/30→SZ，8/4→BJ', () => {
    expect(inferKnowledgeMarket('600000')).toBe('SH')
    expect(inferKnowledgeMarket('688981')).toBe('SH')
    expect(inferKnowledgeMarket('000001')).toBe('SZ')
    expect(inferKnowledgeMarket('300750')).toBe('SZ')
    expect(inferKnowledgeMarket('830799')).toBe('BJ')
    expect(inferKnowledgeMarket('430047')).toBe('BJ')
  })

  it('推断不出就返回 null，不硬猜', () => {
    expect(inferKnowledgeMarket('123456')).toBeNull()
    expect(inferKnowledgeMarket('60000')).toBeNull()
    expect(inferKnowledgeMarket('ABCDEF')).toBeNull()
    expect(inferKnowledgeMarket('')).toBeNull()
  })

  it('市场大小写归一，非法值返回 null', () => {
    expect(normalizeKnowledgeMarket('sh')).toBe('SH')
    expect(normalizeKnowledgeMarket(' sz ')).toBe('SZ')
    expect(normalizeKnowledgeMarket('HK')).toBeNull()
    expect(normalizeKnowledgeMarket(null)).toBeNull()
  })

  it('代码只认 6 位数字', () => {
    expect(normalizeKnowledgeSymbol('600000')).toBe('600000')
    expect(normalizeKnowledgeSymbol(' 600000 ')).toBe('600000')
    expect(normalizeKnowledgeSymbol('600000.SH')).toBeNull()
    expect(normalizeKnowledgeSymbol('60000')).toBeNull()
  })

  it('请求路径带市场与代码', () => {
    expect(knowledgePath('SH', '600000')).toBe('/api/knowledge/SH/600000')
  })
})

describe('档案解析', () => {
  it('生产载荷原样解析：frontmatter + 正文 + 来源', () => {
    const parsed = parseKnowledgeDossier(dossier())
    expect(parsed.market).toBe('SH')
    expect(parsed.symbol).toBe('600000')
    expect(parsed.name).toBe('浦发银行')
    expect(parsed.as_of).toBe('2026-11-09')
    expect(parsed.status).toBe('fresh')
    expect(parsed.valid_days).toBe(90)
    expect(parsed.sources[0].trace_id).toBe('a9581b29e56f48de')
    expect(parsed.body).toContain('## 1. 公司速览')
    expect(parsed.recalled?.as_of).toBe('2026-09-30')
  })

  it('缺名回落代码；缺正文/来源/召回按空值处理，不炸', () => {
    const parsed = parseKnowledgeDossier({
      market: 'SZ',
      symbol: '000001',
      status: 'stale',
    })
    expect(parsed.name).toBe('000001')
    expect(parsed.as_of).toBe('')
    expect(parsed.valid_days).toBeNull()
    expect(parsed.sources).toEqual([])
    expect(parsed.body).toBe('')
    expect(parsed.recalled).toBeNull()
  })

  it('未知 status 抛 parse 错误，不当成 fresh', () => {
    expect(() => parseKnowledgeDossier(dossier({ status: 'unknown' }))).toThrow(ApiError)
    expect(() => parseKnowledgeDossier(dossier({ status: 'unknown' }))).toThrow(/fresh \/ stale \/ refuted/)
  })

  it('市场或代码不合法抛 parse 错误', () => {
    expect(() => parseKnowledgeDossier(dossier({ market: 'HK' }))).toThrow(/市场或代码不合法/)
    expect(() => parseKnowledgeDossier(dossier({ symbol: '60000' }))).toThrow(/市场或代码不合法/)
    expect(() => parseKnowledgeDossier(null)).toThrow(/不是 JSON 对象/)
  })
})

describe('错误口径', () => {
  it('404「档案不存在」是业务空态', () => {
    const error = new ApiError('http', '404', 404, '档案不存在：SH:600000')
    expect(isKnowledgeMissing(error)).toBe(true)
  })

  it('FastAPI 默认 404（接口没接）不算空态', () => {
    const error = new ApiError('http', '404', 404, 'Not Found')
    expect(isKnowledgeMissing(error)).toBe(false)
    expect(describeKnowledgeError(error).code).toBe('HTTP 404')
  })

  it('未连接 / 超时 / 解析失败都可重试，并带代码', () => {
    const offline = describeKnowledgeError(new ApiError('not-connected', 'x'))
    expect(offline.title).toBe('本机服务未连接')
    expect(offline.retryable).toBe(true)

    const timeout = describeKnowledgeError(new ApiError('timeout', '超时'))
    expect(timeout.code).toBe('TIMEOUT')
    expect(timeout.retryable).toBe(true)

    const parse = describeKnowledgeError(new ApiError('parse', '解析'))
    expect(parse.code).toBe('E_PARSE')
  })

  it('400 / 403 / 500 不提示重试', () => {
    for (const status of [400, 403, 500]) {
      expect(describeKnowledgeError(new ApiError('http', 'x', status)).retryable).toBe(false)
    }
  })
})
