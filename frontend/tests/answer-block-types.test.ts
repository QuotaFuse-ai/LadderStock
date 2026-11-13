import { describe, expect, it } from 'vitest'

import {
  BACKEND_MAX_BLOCKS,
  MAX_BLOCKS,
  MAX_CHART_POINTS,
  MAX_METRIC_ITEMS,
  parseBlock,
  parseBlocks,
} from '../src/components/answer-blocks'
import {
  ACTION_BLOCK,
  CHART_BLOCK,
  CITATION_BLOCK,
  CONCLUSION_BLOCK,
  FULL_ANSWER_BLOCKS,
  METRIC_BLOCK,
  NOTICE_BLOCK,
  TABLE_BLOCK,
} from './fixtures/answerBlocks'

/**
 * 答案块协议的形状校验（任务书 §6.3 / §6.4）。
 *
 * 钉的是「前端只消费、不重算」：载荷合法就原样过；不合法的那条整块占位说明，
 * 不补 0、不猜日期、不静默吞掉。
 */

describe('parseBlock · 七个块类型原样透传', () => {
  it('阶段 E fixture 每个块都能解析，载荷不被改写', () => {
    const cases = [CONCLUSION_BLOCK, METRIC_BLOCK, CHART_BLOCK, TABLE_BLOCK, CITATION_BLOCK, NOTICE_BLOCK, ACTION_BLOCK]
    for (const raw of cases) {
      const parsed = parseBlock(raw)
      expect(parsed.type).toBe(raw.type)
      if (parsed.type === 'invalid') throw new Error(`fixture ${raw.type} 不该被判为不合法：${parsed.reason}`)
      expect(parsed.payload).toEqual(raw.payload)
    }
  })

  it('保留服务端给的块 id 作为 React key', () => {
    const parsed = parseBlock({ ...NOTICE_BLOCK, id: 'b-3' })
    expect(parsed).toMatchObject({ type: 'notice', id: 'b-3' })
  })
})

describe('parseBlock · 兜底与截断', () => {
  it('未识别类型 / 缺 type / 非对象都返回 invalid，并给出人能看懂的原因', () => {
    expect(parseBlock({ type: 'video', payload: {} })).toEqual({
      type: 'invalid',
      reason: '未识别的答案块类型「video」',
    })
    expect(parseBlock({ payload: {} })).toEqual({ type: 'invalid', reason: '答案块缺少 type' })
    expect(parseBlock(null)).toEqual({ type: 'invalid', reason: '答案块不是对象' })
    expect(parseBlock([1, 2])).toEqual({ type: 'invalid', reason: '答案块不是对象' })
  })

  it('结论缺正文 → invalid；缺 confidence 不补默认值，给 null', () => {
    expect(parseBlock({ type: 'conclusion', payload: { text: '   ', tone: 'neutral' } })).toEqual({
      type: 'invalid',
      reason: '结论块缺少正文',
    })
    const parsed = parseBlock({ type: 'conclusion', payload: { text: '偏弱', tone: 'sideways' } })
    expect(parsed).toMatchObject({ type: 'conclusion', payload: { text: '偏弱', tone: 'neutral', confidence: null } })
  })

  it('指标：丢掉无数值的格子，最多 4 个，方向只认 up/down/flat', () => {
    const parsed = parseBlock({
      type: 'metric',
      payload: {
        items: [
          { label: 'A', value: 1 },
          { label: 'B', value: '一点二' },
          { label: 'C' },
          { label: 'D', value: 2, direction: 'sideways' },
          { label: 'E', value: 3 },
          { label: 'F', value: 4 },
          { label: 'G', value: 5 },
        ],
      },
    })
    expect(parsed.type).toBe('metric')
    if (parsed.type !== 'metric') return
    expect(parsed.payload.items).toHaveLength(MAX_METRIC_ITEMS)
    expect(parsed.payload.items.map((item) => item.label)).toEqual(['A', 'D', 'E', 'F'])
    expect(parsed.payload.items[1].direction).toBeUndefined()
  })

  it('指标一个可展示的数值都没有 → invalid，不渲染空格子', () => {
    expect(parseBlock({ type: 'metric', payload: { items: [{ label: '涨幅', value: null }] } })).toEqual({
      type: 'invalid',
      reason: '指标块没有可展示的数值',
    })
  })

  it('图表：坏点丢弃、序列截到 120 个点、未知 kind 退到折线', () => {
    const points = Array.from({ length: MAX_CHART_POINTS + 10 }, (_, index) => [`2026-01-${index}`, index])
    const parsed = parseBlock({
      type: 'chart',
      payload: { kind: 'scatter', series: [{ name: '收盘价', points: [...points, ['坏点', 'x']] }] },
    })
    expect(parsed.type).toBe('chart')
    if (parsed.type !== 'chart') return
    expect(parsed.payload.kind).toBe('line')
    expect(parsed.payload.series[0].points).toHaveLength(MAX_CHART_POINTS)
  })

  it('表格：rows_total 不小于已给行数；没给 rows_total 按已给行数算', () => {
    const short = parseBlock({
      type: 'table',
      payload: { columns: ['指标'], rows: [['营业收入']], rows_total: 0 },
    })
    expect(short).toMatchObject({ type: 'table', payload: { rows_total: 1 } })

    const missing = parseBlock({ type: 'table', payload: { columns: ['指标'], rows: [['a'], ['b']] } })
    expect(missing).toMatchObject({ type: 'table', payload: { rows_total: 2 } })
  })

  it('来源：只丢缺 name / fetched_at 的行；无 url 保留但不可点，symbol/as_of 缺失保留 null', () => {
    const parsed = parseBlock({
      type: 'citation',
      payload: {
        items: [
          { name: '无链接', fetched_at: '2026-11-11T15:02:01+08:00', url: '', symbol: '600036', as_of: null },
          { fetched_at: '2026-11-11T15:02:01+08:00', url: 'https://example.com/b' },
          { name: '腾讯行情', fetched_at: '2026-11-11T15:02:01+08:00', url: 'https://example.com/a', symbol: null, as_of: null },
        ],
      },
    })
    expect(parsed.type).toBe('citation')
    if (parsed.type !== 'citation') return
    // 后端 validate_citation 只强制 name + fetched_at，url 可缺省：缺了列出来但不可点
    expect(parsed.payload.items).toHaveLength(2)
    expect(parsed.payload.items[0]).toMatchObject({ name: '无链接', url: null, symbol: '600036' })
    expect(parsed.payload.items[1]).toMatchObject({ name: '腾讯行情', url: 'https://example.com/a', symbol: null, as_of: null })
  })

  it('动作：只认 open_screen；未知动作 / 缺 target 的条目丢弃', () => {
    const parsed = parseBlock({
      type: 'action',
      payload: {
        items: [
          { label: '买', action: 'place_order', target: '/research' },
          { label: '去研究', action: 'open_screen', target: '/research' },
        ],
      },
    })
    expect(parsed).toMatchObject({ type: 'action', payload: { items: [{ label: '去研究' }] } })
  })

  it('提示：未知 kind 原样保留（白名单在渲染层，不在解析层）', () => {
    expect(parseBlock({ type: 'notice', payload: { kind: 'brand_new', message: '新类型' } })).toMatchObject({
      type: 'notice',
      payload: { kind: 'brand_new' },
    })
    expect(parseBlock({ type: 'notice', payload: { message: '缺 kind' } })).toEqual({
      type: 'invalid',
      reason: '提示块缺少 kind',
    })
  })
})

describe('parseBlocks · 整轮', () => {
  it('非数组 / 空数组 → 空列表', () => {
    expect(parseBlocks(null)).toEqual([])
    expect(parseBlocks({ blocks: [] })).toEqual([])
    expect(parseBlocks([])).toEqual([])
  })

  it('保持服务端顺序，只截到 MAX_BLOCKS 块', () => {
    const many = Array.from({ length: MAX_BLOCKS + 3 }, (_, index) => ({
      type: 'notice',
      payload: { kind: 'degraded', message: `第 ${index} 条` },
    }))
    const parsed = parseBlocks(many)
    expect(parsed).toHaveLength(MAX_BLOCKS)
    expect(parsed[0]).toMatchObject({ payload: { message: '第 0 条' } })
  })

  it('MAX_BLOCKS 与后端常量一致（QUOT-581 提交 34f708e3 拍板 6），防止两套数漂移', () => {
    expect(MAX_BLOCKS).toBe(6)
    expect(MAX_BLOCKS).toBe(BACKEND_MAX_BLOCKS)
  })

  it('full_answer 整串：结论在前、来源在后，全部能解析', () => {
    const parsed = parseBlocks(FULL_ANSWER_BLOCKS)
    expect(parsed.map((block) => block.type)).toEqual([
      'conclusion',
      'metric',
      'chart',
      'table',
      'notice',
      'citation',
    ])
    expect(parsed.every((block) => block.type !== 'invalid')).toBe(true)
  })
})
