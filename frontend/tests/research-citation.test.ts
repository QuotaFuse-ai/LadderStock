import { describe, expect, it } from 'vitest'

import type { ProvenanceSlot, ProvenanceSource } from '../src/api/research'
import { buildCitationIndex, parseInline, parseMarkdown } from '../src/lib/citation'

function source(id: string, overrides: Partial<ProvenanceSource> = {}): ProvenanceSource {
  return {
    id,
    dataset: 'tickhub-cn',
    endpoint: '/v1/financials/600000',
    as_of: '2025-12-31',
    url: null,
    verified: true,
    ...overrides,
  }
}

function slot(sources: ProvenanceSource[]): ProvenanceSlot {
  return {
    values: {},
    sources,
    unverified: sources.every((item) => !item.verified),
  }
}

describe('行内标记', () => {
  it('识别角标、未核实、加粗与代码', () => {
    const tokens = parseInline('营收 1.2 亿[1]，净利〔未核实〕，**结论**，`ROE`')
    expect(tokens).toEqual([
      { type: 'text', text: '营收 1.2 亿' },
      { type: 'citation', n: 1, raw: '[1]' },
      { type: 'text', text: '，净利' },
      { type: 'unverified' },
      { type: 'text', text: '，' },
      { type: 'strong', text: '结论' },
      { type: 'text', text: '，' },
      { type: 'code', text: 'ROE' },
    ])
  })

  it('纯文本原样返回', () => {
    expect(parseInline('没有标记')).toEqual([{ type: 'text', text: '没有标记' }])
  })
})

describe('块级解析', () => {
  it('标题 / 列表 / 引用 / 分隔线 / 段落', () => {
    const blocks = parseMarkdown(
      ['# 标题', '', '第一段续行', '第二行', '', '- 一', '- 二', '', '> 降级说明', '', '---', '', '末段'].join('\n'),
    )
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, text: '标题' },
      { kind: 'paragraph', text: '第一段续行 第二行' },
      { kind: 'list', items: ['一', '二'] },
      { kind: 'quote', text: '降级说明' },
      { kind: 'rule' },
      { kind: 'paragraph', text: '末段' },
    ])
  })
})

describe('角标索引', () => {
  it('只有 verified=true 进入角标；未核实来源单独列出', () => {
    const index = buildCitationIndex(
      slot([
        source('S1'),
        source('S2', { verified: false, dataset: 'web' }),
        source('S3'),
      ]),
    )
    expect([...index.byNumber.keys()]).toEqual([1, 3])
    expect(index.byNumber.get(1)?.id).toBe('S1')
    expect(index.byNumber.has(2)).toBe(false)
    expect(index.idByNumber.get(2)).toBe('S2')
    expect(index.unverifiedSources.map((item) => item.id)).toEqual(['S2'])
    expect(index.empty).toBe(false)
  })

  it('没有来源时 empty 为 true', () => {
    expect(buildCitationIndex(slot([])).empty).toBe(true)
    expect(buildCitationIndex(null).empty).toBe(true)
  })
})
