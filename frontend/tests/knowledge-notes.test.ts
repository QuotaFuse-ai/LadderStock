import { describe, expect, it } from 'vitest'

import { markdownLiteText, renderMarkdownLite } from '../src/lib/markdown-lite'
import { parseHit, parseNote } from '../src/api/knowledgeNotes'

/**
 * E 包 / DEC-091 前端解析口径：笔记行、检索命中、markdown 渲染。
 *
 * 后端契约见 `src/ladderstock/knowledge/notes.py` 与
 * `src/ladderstock/web/knowledge_write.py`；这里钉住「缺字段不硬造」与
 * 「markdown 先转义再叠结构」两条安全线。
 */

describe('笔记行解析', () => {
  it('生产载荷原样解析：target 由 market:symbol 拼出', () => {
    const note = parseNote({
      id: 3,
      market: 'SH',
      symbol: '600000',
      title: '观察要点',
      body: '# 观点\n等三季报',
      created_at: '2026-11-22T10:00:00+08:00',
      updated_at: '2026-11-22T11:00:00+08:00',
    })
    expect(note.id).toBe(3)
    expect(note.target).toBe('SH:600000')
    expect(note.title).toBe('观察要点')
    expect(note.tags).toEqual([])
  })

  it('后端附带 tags 与 target 时原样保留', () => {
    const note = parseNote({ id: 1, target: '600519', tags: ['白酒', '观察'] })
    expect(note.target).toBe('600519')
    expect(note.tags).toEqual(['白酒', '观察'])
    expect(note.market).toBe('')
    expect(note.symbol).toBe('')
  })

  it('缺字段不硬造：非对象给全空，id 非数字归 0', () => {
    const empty = parseNote(null)
    expect(empty.id).toBe(0)
    expect(empty.body).toBe('')
    expect(empty.tags).toEqual([])
    expect(parseNote({ id: 'x' }).id).toBe(0)
  })
})

describe('检索命中解析', () => {
  it('向量命中：分数与片段原样保留', () => {
    const hit = parseHit({
      market: 'SH',
      symbol: '600000',
      as_of: '2026-11-20',
      status: 'fresh',
      section: '结论',
      score: 0.81234,
      snippet: '股息率……',
      source: 'vector',
    })
    expect(hit.source).toBe('vector')
    expect(hit.score).toBeCloseTo(0.81234)
    expect(hit.section).toBe('结论')
  })

  it('本地降级命中：as_of / score 缺省为 null，source 缺省 local', () => {
    const hit = parseHit({ market: 'SH', symbol: '600519', snippet: '字面命中' })
    expect(hit.as_of).toBeNull()
    expect(hit.score).toBeNull()
    expect(hit.source).toBe('local')
    expect(hit.status).toBeNull()
  })
})

describe('markdown 极简渲染', () => {
  it('标题 / 列表 / 引用 / 段落', () => {
    const html = renderMarkdownLite('# 标题\n- 甲\n- 乙\n> 引用一句\n正文一段')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<li>甲</li>')
    expect(html).toContain('<blockquote>引用一句</blockquote>')
    expect(html).toContain('<p>正文一段</p>')
  })

  it('有序列表 / 代码块 / 行内代码与加粗', () => {
    const html = renderMarkdownLite('1. 第一\n2. 第二\n\n```\nconst x = 1\n```\n这是 `code` 与 **重点**')
    expect(html).toContain('<ol>')
    expect(html).toContain('<pre><code>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<strong>重点</strong>')
  })

  it('先转义再叠结构：HTML 注入不复活', () => {
    const html = renderMarkdownLite('<script>alert(1)</script>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
    expect(markdownLiteText('<img src=x onerror=alert(1)>')).not.toContain('<img')
  })

  it('链接只接受 http(s)，其余协议不生成 <a>', () => {
    const ok = renderMarkdownLite('[官网](https://example.com)')
    expect(ok).toContain('<a href="https://example.com"')
    const bad = renderMarkdownLite('[点我](javascript:alert(1))')
    expect(bad).not.toContain('<a ')
    expect(bad).toContain('javascript:alert(1)') // 原文保留（已转义），但不可点击
  })

  it('未闭合代码块自动收口，不留断树', () => {
    const html = renderMarkdownLite('```\n未闭合')
    expect(html.endsWith('</code></pre>')).toBe(true)
  })
})
