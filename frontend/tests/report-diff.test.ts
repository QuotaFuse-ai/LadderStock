/**
 * 报告对比的差异引擎（QUOT-453）：分段、行级差异、四类归并、诚实截断。
 *
 * 这里比的是**展示值**，不生产数字：断言的重点是
 * 「这条事实不见了」不能被算成噪音、比不过的段落要明说、样本侧缺失要点名。
 */

import { describe, expect, it } from 'vitest'

import {
  MAX_SECTION_LINES,
  describeStageKind,
  diffLines,
  diffSections,
  diffStage,
  diffValues,
  splitSections,
  summarizeStages,
  type StageSnapshot,
} from '../src/lib/reportDiff'

function snapshot(
  patch: Partial<StageSnapshot> & Pick<StageSnapshot, 'name' | 'title'>,
): StageSnapshot {
  return { status: 'ok', markdown: null, values: {}, ...patch }
}

describe('splitSections', () => {
  it('按二级标题切段，标题前的导语算一段', () => {
    const sections = splitSections('导语一行\n\n## 业务\n做银行\n\n## 风险\n息差收窄')

    expect(sections.map((section) => section.title)).toEqual(['', '业务', '风险'])
    expect(sections[0].lines).toEqual(['导语一行', ''])
  })

  it('空文档不产出段落，一级标题不算分节', () => {
    expect(splitSections('')).toEqual([])
    expect(splitSections(null)).toEqual([])
    expect(splitSections('# 一级标题\n正文')).toEqual([{ title: '', lines: ['# 一级标题', '正文'] }])
  })
})

describe('diffLines', () => {
  it('只把真正不一样的行标成增删，公共行留在原地', () => {
    const { ops, added, removed } = diffLines(['a', 'b', 'c'], ['a', 'x', 'c'])

    expect(ops.map((op) => op.kind)).toEqual(['same', 'remove', 'add', 'same'])
    expect(added).toBe(1)
    expect(removed).toBe(1)
  })

  it('首尾空行先剪掉：换个空行数不算变化', () => {
    expect(diffLines(['正文', '', ''], ['正文']).added).toBe(0)
  })

  it('超过上限只比前 N 行并如实标 truncated，不假装比过整节', () => {
    const before = Array.from({ length: MAX_SECTION_LINES + 5 }, (_, index) => `行${index}`)
    const after = Array.from({ length: MAX_SECTION_LINES + 5 }, (_, index) => `改${index}`)
    const result = diffLines(before, after, 10)

    expect(result.truncated).toBe(true)
    // 截断后只比前 10 行：比对的行数由上限决定，剩下的行不算进去
    expect(result.added).toBe(10)
    expect(result.removed).toBe(10)
  })
})

describe('diffSections', () => {
  it('标题相同才算同一节：改标题 = 删掉一节 + 新增一节', () => {
    const sections = diffSections('## 盈利能力\nROE 12%', '## 盈利质量\nROE 12%')

    expect(sections.map((section) => [section.title, section.kind])).toEqual([
      ['盈利能力', 'removed'],
      ['盈利质量', 'added'],
    ])
  })

  it('内容一致的一节降级为 same，不产出假的差异', () => {
    const sections = diffSections('## 业务\n做银行', '## 业务\n做银行')

    expect(sections).toHaveLength(1)
    expect(sections[0].kind).toBe('same')
    expect(sections[0].added).toBe(0)
  })

  it('新增的一节即使没有正文行也保持 added，不被抹成 same', () => {
    const sections = diffSections('## 业务\n做银行', '## 业务\n做银行\n\n## 或有事项')

    const added = sections.find((section) => section.title === '或有事项')
    expect(added?.kind).toBe('added')
    expect(added?.ops).toEqual([])
  })

  it('只有旧版有的段落算「消失」，逐行给出旧文本', () => {
    const sections = diffSections('## 风险\n质押比例 40%\n诉讼一起', '## 风险\n质押比例 40%')

    const risk = sections[0]
    expect(risk.kind).toBe('changed')
    expect(risk.ops.filter((op) => op.kind === 'remove').map((op) => op.text)).toEqual(['诉讼一起'])
  })
})

describe('diffValues', () => {
  it('值不同算 changed，缺一侧算 added / removed，同值不进结果', () => {
    const diffs = diffValues(
      { 现价: '9.10', 总市值: '2,700亿', 不良率: '1.36%' },
      { 现价: '9.20', 不良率: '1.36%', 拨备覆盖率: '250%' },
    )

    const byLabel = new Map(diffs.map((item) => [item.label, item]))
    expect(byLabel.get('现价')?.kind).toBe('changed')
    expect(byLabel.get('总市值')?.kind).toBe('removed')
    expect(byLabel.get('总市值')?.after).toBeNull()
    expect(byLabel.get('拨备覆盖率')?.kind).toBe('added')
    expect(byLabel.get('拨备覆盖率')?.before).toBeNull()
    // 两次一样的读数不参与对比展示
    expect(diffs.some((item) => item.label === '不良率')).toBe(false)
  })

  it('只比展示值：格式不同就是不同，不回头重算数字', () => {
    const diffs = diffValues({ 营业收入: '1,200亿' }, { 营业收入: '—' })

    expect(diffs[0].kind).toBe('changed')
    expect(diffs[0].after).toBe('—')
  })
})

describe('diffStage', () => {
  it('两侧都没有产物 → 不产出这段（不编造空差异）', () => {
    expect(diffStage(null, null)).toBeNull()
  })

  it('一侧没有产物时点名是哪一侧缺，整段按新增处理', () => {
    const diff = diffStage(
      null,
      snapshot({ name: 'risk', title: '风险', markdown: '## 风险\n有质押' }),
    )

    expect(diff?.kind).toBe('added')
    expect(diff?.notes.join('')).toContain('旧的那次没有这一段产物')
  })

  it('段落一致但读数变了 = 有变化，并在读数里给出前后值', () => {
    const diff = diffStage(
      snapshot({ name: 'valuation', title: '估值', markdown: '## 估值\nPE 5.1', values: { 市盈率: '5.1' } }),
      snapshot({ name: 'valuation', title: '估值', markdown: '## 估值\nPE 5.1', values: { 市盈率: '5.3' } }),
    )

    expect(diff?.kind).toBe('changed')
    expect(diff?.sections.every((section) => section.kind === 'same')).toBe(true)
    expect(diff?.values[0]).toMatchObject({ label: '市盈率', before: '5.1', after: '5.3' })
  })

  it('段落与读数都一致 → same，不给用户假的「有变化」', () => {
    const doc = snapshot({ name: 'company', title: '公司画像', markdown: '## 业务\n银行', values: { 员工数: '5万' } })
    expect(diffStage(doc, doc)?.kind).toBe('same')
  })

  it('段状态变了要写在 notes 里（用中文标签，不端枚举）', () => {
    const diff = diffStage(
      snapshot({ name: 'financial', title: '财务', status: '完成', markdown: '## 财务\n一行' }),
      snapshot({ name: 'financial', title: '财务', status: '降级', markdown: '## 财务\n一行' }),
    )

    expect(diff?.kind).toBe('changed')
    expect(diff?.notes.join('')).toContain('状态从 完成 变为 降级')
  })
})

describe('归类与汇总', () => {
  it('四类说法分开：有变化 / 本次新增 / 本次没有 / 没变', () => {
    expect([
      describeStageKind('changed'),
      describeStageKind('added'),
      describeStageKind('removed'),
      describeStageKind('same'),
    ]).toEqual(['有变化', '本次新增', '本次没有', '没变'])
  })

  it('汇总按段计数，段落出入与读数出入分开', () => {
    const summary = summarizeStages([
      diffStage(
        snapshot({ name: 'a', title: 'A', markdown: '## 一\n旧' }),
        snapshot({ name: 'a', title: 'A', markdown: '## 一\n新' }),
      )!,
      diffStage(null, snapshot({ name: 'b', title: 'B', markdown: '## 一\n只有新' }))!,
      diffStage(
        snapshot({ name: 'c', title: 'C', markdown: '## 一\n同', values: { 数: '1' } }),
        snapshot({ name: 'c', title: 'C', markdown: '## 一\n同', values: { 数: '2' } }),
      )!,
    ])

    expect(summary.stages).toBe(3)
    expect(summary.changed).toBe(2)
    expect(summary.added).toBe(1)
    expect(summary.same).toBe(0)
    expect(summary.sectionChanged).toBe(2)
    expect(summary.valueChanged).toBe(1)
  })
})
