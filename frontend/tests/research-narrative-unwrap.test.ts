import { describe, expect, it } from 'vitest'

import type { StageDocument } from '../src/api/research'
import { artifactMarkdown, unwrapNarrativeJson } from '../src/screens/research/facts'

/**
 * QUOT-444 验收发现：旧产物（mode=parsed）把模型返回的整包 JSON 原样写成一段叙述，
 * 页面照原样渲染就是一屏花括号。这里锁住「只拆包、不改写」的展示层口径。
 */

function document(overrides: Partial<StageDocument> = {}): StageDocument {
  return {
    code: '600000',
    date: '2026-11-02',
    stage: 'risk',
    status: 'ok',
    narrative: [],
    notes: [],
    facts: {},
    provenance: { values: {}, sources: [], unverified: false },
    ...overrides,
  } as StageDocument
}

describe('unwrapNarrativeJson', () => {
  it('普通叙述原样返回，连没带包装的 JSON 片段都不动', () => {
    const plain = '本段的风险叙述基于程序事实快照。'
    expect(unwrapNarrativeJson(plain)).toBe(plain)
    expect(unwrapNarrativeJson('结论见 {"补充": 一句话')).toContain('结论见')
  })

  it('整包 JSON 拆成段落，文案一字不改', () => {
    const wrapped = '{"narrative":["第一段。","第二段。"],"evidence":["依据一。"]}'
    expect(unwrapNarrativeJson(wrapped)).toBe('第一段。\n\n第二段。\n\n依据一。')
  })

  it('包装串自己写坏了（数组收尾多一个引号）也照样拆', () => {
    // 600000 · 2026-11-02 · risk.json 的真实形状：narrative 数组收尾写成 `"]"`
    const broken = '{"narrative":["第一段。","第二段。"]","highlights":["要点一。"]}'
    expect(unwrapNarrativeJson(broken)).toBe('第一段。\n\n第二段。\n\n要点一。')
  })

  it('拆不出来就原样返回：数组、标量、坏 JSON 都不猜', () => {
    expect(unwrapNarrativeJson('{"narrative":["没闭合')).toContain('没闭合')
    const list = '["第一段。","第二段。"]'
    expect(unwrapNarrativeJson(list)).toBe(list)
    expect(unwrapNarrativeJson('{"narrative":[1,2]}')).toBe('{"narrative":[1,2]}')
  })

  it('只换掉包装那一行，文档其余部分（含标题）不动', () => {
    const md = '# 风险\n\n## 叙述\n\n{"narrative":["第一段。"]}\n\n## 备注\n\n- 快照未提供'
    expect(unwrapNarrativeJson(md)).toBe('# 风险\n\n## 叙述\n\n第一段。\n\n## 备注\n\n- 快照未提供')
  })
})

describe('artifactMarkdown', () => {
  it('有落盘 md 时以 md 为准，并把其中的包装叙述拆开', () => {
    const out = artifactMarkdown(
      document({ narrative: ['不该用这条'] }),
      '# 风险\n\n## 叙述\n\n{"narrative":["本段快照为空对象。"]}',
    )
    expect(out).toContain('本段快照为空对象。')
    expect(out).not.toContain('不该用这条')
    expect(out).not.toContain('{"narrative"')
  })

  it('没有 md 时用叙述拼等价视图，同样拆开包装', () => {
    const out = artifactMarkdown(
      document({ narrative: ['{"narrative":["第一段。"]}'], notes: ['快照未提供。'] }),
      '',
    )
    expect(out).toContain('第一段。')
    expect(out).toContain('## 备注')
    expect(out).toContain('- 快照未提供。')
    expect(out).not.toContain('{"narrative"')
  })
})
