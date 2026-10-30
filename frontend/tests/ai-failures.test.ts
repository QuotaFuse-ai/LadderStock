import { describe, expect, it } from 'vitest'

import { parseAiFailures } from '../src/api'

describe('parseAiFailures', () => {
  it('原样带出四类失败与 internal，不丢 section / action / retryable', () => {
    const parsed = parseAiFailures([
      { section: 'sentiment', kind: 'network', message: '网络请求失败：连接超时', action: '重试', retryable: true },
      { section: 'capital', kind: 'quota', message: '额度或频率受限：HTTP 429', action: '换模型', retryable: true },
      { section: 'theme', kind: 'format', message: '结构化输出解析失败', action: '重试一次', retryable: true },
      { section: 'dragon_tiger', kind: 'truncated', message: '输出被截断', action: '调大 max_tokens', retryable: false },
      { section: 'leader', kind: 'internal', message: 'AI 调用未归类失败：鉴权失败', action: '查密钥', retryable: false },
    ])

    expect(parsed.map((failure) => failure.kind)).toEqual([
      'network',
      'quota',
      'format',
      'truncated',
      'internal',
    ])
    expect(parsed[0]).toEqual({
      section: 'sentiment',
      kind: 'network',
      message: '网络请求失败：连接超时',
      action: '重试',
      retryable: true,
    })
    expect(parsed[3].retryable).toBe(false)
  })

  it('未知 kind 归 internal，缺 section 或非对象的条目丢弃', () => {
    const parsed = parseAiFailures([
      { section: 'capital', kind: 'teapot', message: '未知失败' },
      { kind: 'network', message: '没有段名' },
      null,
      'not-an-object',
      { section: '   ', kind: 'network' },
    ])

    expect(parsed).toHaveLength(1)
    expect(parsed[0].kind).toBe('internal')
    expect(parsed[0].retryable).toBe(false)
    expect(parsed[0].action).toBe('')
  })

  it('非数组与脏数据返回空数组，不抛错', () => {
    expect(parseAiFailures(undefined)).toEqual([])
    expect(parseAiFailures(null)).toEqual([])
    expect(parseAiFailures({})).toEqual([])
    expect(parseAiFailures('ai_failures')).toEqual([])
  })
})
