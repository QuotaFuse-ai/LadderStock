/**
 * `thesis_notes` 分片（QUOT-452）：校验、判定留痕、到期统计、落库往返与恢复校验。
 *
 * 这些断言保护三件不能退让的事：
 * - 裁决点不接受空条件 / 空到期日，且每条论点至少一个裁决点；
 * - 判定只追加不改写（改判也留痕）；
 * - 落库内容是私有的本机数据，损坏时整片拒绝而不是补默认值。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'

import { KV_STORE, closeStateDatabase, idbDelete, idbGet, idbSet } from '../persist/indexeddb'
import {
  daysPastDue,
  overdueCount,
  overduePoints,
  parseThesisNotesState,
  useThesisNotes,
} from '../stores/thesisNotes'

interface Persisted<T> {
  state: T
  version: number
}

const BASE_INPUT = {
  code: '600000',
  title: '产能护城河，绕不开',
  body: '行业扩产周期见顶，龙头议价权回升。',
  reason: '只观察，等下一份财报确认毛利率',
  condition: '下一季毛利率 ≥ 30% 算成立，否则不成立',
  due: '2020-01-31',
}

async function waitFlush(ms = 80): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

function resetMemory(): void {
  useThesisNotes.setState({ records: [], updatedAt: null })
}

async function readShard(): Promise<Persisted<{ records: unknown[]; updatedAt: string | null }> | null> {
  const raw = await idbGet<string>(KV_STORE, 'thesis_notes')
  return typeof raw === 'string' ? (JSON.parse(raw) as Persisted<{ records: unknown[]; updatedAt: string | null }>) : null
}

beforeEach(async () => {
  resetMemory()
  await waitFlush()
  await closeStateDatabase()
  await idbDelete(KV_STORE, 'thesis_notes')
})

describe('保存校验', () => {
  it('标题为空拒绝保存', () => {
    const result = useThesisNotes.getState().addThesis({ ...BASE_INPUT, title: '   ' })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('标题')
    expect(useThesisNotes.getState().records).toHaveLength(0)
  })

  it('裁决点条件为空拒绝保存（到期判定不接受空条件）', () => {
    const result = useThesisNotes.getState().addThesis({ ...BASE_INPUT, condition: '  ' })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('判定条件')
    expect(useThesisNotes.getState().records).toHaveLength(0)
  })

  it('到期日缺失或不存在都拒绝', () => {
    expect(useThesisNotes.getState().addThesis({ ...BASE_INPUT, due: '' }).ok).toBe(false)
    expect(useThesisNotes.getState().addThesis({ ...BASE_INPUT, due: '2026-02-30' }).ok).toBe(false)
    expect(useThesisNotes.getState().addThesis({ ...BASE_INPUT, due: '2026/02/01' }).ok).toBe(false)
    expect(useThesisNotes.getState().records).toHaveLength(0)
  })

  it('保存成功即带一个待判裁决点', () => {
    const result = useThesisNotes.getState().addThesis(BASE_INPUT)
    expect(result.ok).toBe(true)
    const [record] = useThesisNotes.getState().records
    expect(record).toMatchObject({
      code: '600000',
      title: '产能护城河，绕不开',
      body: BASE_INPUT.body,
      reason: BASE_INPUT.reason,
    })
    expect(record.points).toHaveLength(1)
    expect(record.points[0]).toMatchObject({
      due: '2020-01-31',
      condition: BASE_INPUT.condition,
      verdict: 'pending',
      judgedAt: null,
      history: [],
    })
  })
})

describe('编辑 / 删除', () => {
  it('编辑标题与正文，判定条件保持不变', () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)

    const updated = useThesisNotes.getState().updateThesis(created.id, {
      title: '改过的标题',
      body: '改过的正文',
      reason: '',
    })
    expect(updated.ok).toBe(true)
    const [record] = useThesisNotes.getState().records
    expect(record.title).toBe('改过的标题')
    expect(record.body).toBe('改过的正文')
    expect(record.points[0].condition).toBe(BASE_INPUT.condition)
  })

  it('编辑不存在的论点给出可读错误，不静默新建', () => {
    const result = useThesisNotes.getState().updateThesis('missing', { title: 'x' })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('不存在')
    expect(useThesisNotes.getState().records).toHaveLength(0)
  })

  it('删除只移除目标论点', () => {
    const first = useThesisNotes.getState().addThesis(BASE_INPUT)
    const second = useThesisNotes.getState().addThesis({ ...BASE_INPUT, code: '600001' })
    if (!first.ok || !second.ok) throw new Error('创建失败')

    useThesisNotes.getState().removeThesis(first.id)
    const records = useThesisNotes.getState().records
    expect(records).toHaveLength(1)
    expect(records[0].id).toBe(second.id)
  })
})

describe('判定与留痕', () => {
  it('判定写入结论与判定时间，history 保留第一次', () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)
    const pointId = useThesisNotes.getState().records[0].points[0].id

    const judged = useThesisNotes.getState().judgePoint(created.id, pointId, {
      verdict: 'met',
      note: '毛利率 31.2%，达标',
    })
    expect(judged.ok).toBe(true)
    const point = useThesisNotes.getState().records[0].points[0]
    expect(point.verdict).toBe('met')
    expect(point.judgedAt).toEqual(expect.any(String))
    expect(point.verdictNote).toBe('毛利率 31.2%，达标')
    expect(point.history).toHaveLength(1)
    expect(point.history[0]).toMatchObject({ verdict: 'met', note: '毛利率 31.2%，达标' })
  })

  it('改判追加第二条留痕，第一条不被覆盖', () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)
    const pointId = useThesisNotes.getState().records[0].points[0].id

    useThesisNotes.getState().judgePoint(created.id, pointId, { verdict: 'met', note: '先判成立' })
    useThesisNotes.getState().judgePoint(created.id, pointId, { verdict: 'broken', note: '复核后改判' })

    const point = useThesisNotes.getState().records[0].points[0]
    expect(point.verdict).toBe('broken')
    expect(point.history).toHaveLength(2)
    expect(point.history.map((entry) => entry.note)).toEqual(['复核后改判', '先判成立'])
  })

  it('判定结论为空拒绝，且不写入留痕', () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)
    const pointId = useThesisNotes.getState().records[0].points[0].id

    const result = useThesisNotes.getState().judgePoint(created.id, pointId, {
      verdict: '' as 'met',
      note: '',
    })
    expect(result.ok).toBe(false)
    expect(useThesisNotes.getState().records[0].points[0].history).toHaveLength(0)
  })
})

describe('到期统计', () => {
  it('只统计待判且已过到期日的裁决点', () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)
    const points = useThesisNotes.getState().records[0].points
    expect(overduePoints(points, '2020-02-01').map((point) => point.id)).toEqual([points[0].id])
    expect(overduePoints(points, '2020-01-31')).toHaveLength(0)

    useThesisNotes.getState().judgePoint(created.id, points[0].id, { verdict: 'met', note: '' })
    const after = useThesisNotes.getState().records[0].points
    expect(overduePoints(after, '2020-02-01')).toHaveLength(0)
  })

  it('逾期天数按日历日算', () => {
    expect(daysPastDue('2026-10-30', '2026-11-06')).toBe(7)
    expect(daysPastDue('2026-11-06', '2026-11-06')).toBe(0)
    expect(daysPastDue('2026-11-07', '2026-11-06')).toBe(-1)
  })

  it('跨标的统计只算传入的记录', () => {
    useThesisNotes.getState().addThesis(BASE_INPUT)
    useThesisNotes.getState().addThesis({ ...BASE_INPUT, code: '600001' })
    const records = useThesisNotes.getState().records
    expect(overdueCount(records, '2020-02-01')).toBe(2)
    expect(overdueCount(records.filter((record) => record.code === '600001'), '2020-02-01')).toBe(1)
  })
})

describe('持久化', () => {
  it('写入 → 落 IndexedDB（version=2）→ 清内存 rehydrate 后原样恢复', async () => {
    const created = useThesisNotes.getState().addThesis(BASE_INPUT)
    if (!created.ok) throw new Error(created.error)
    const pointId = useThesisNotes.getState().records[0].points[0].id
    useThesisNotes.getState().judgePoint(created.id, pointId, { verdict: 'met', note: '达标' })
    await waitFlush()

    const shard = await readShard()
    expect(shard?.version).toBe(2)
    expect(shard?.state.records).toHaveLength(1)

    // 模拟关窗重开：内存写空会覆盖分片，先存下原始分片再写回（与 watch_settings 测试同法）
    const raw = await idbGet<string>(KV_STORE, 'thesis_notes')
    resetMemory()
    await waitFlush()
    await idbSet(KV_STORE, 'thesis_notes', raw as string)
    await useThesisNotes.persist.rehydrate()

    const [record] = useThesisNotes.getState().records
    expect(record.title).toBe(BASE_INPUT.title)
    expect(record.points[0].verdict).toBe('met')
    expect(record.points[0].history[0].note).toBe('达标')
  })

  it('恢复校验：裁决点为空的记录整片拒绝，不补默认值', () => {
    expect(
      parseThesisNotesState({
        records: [
          {
            id: 't1',
            code: '600000',
            title: '标题',
            body: '',
            reason: '',
            createdAt: '2026-11-01T00:00:00.000Z',
            updatedAt: '2026-11-01T00:00:00.000Z',
            points: [],
          },
        ],
        updatedAt: '2026-11-01T00:00:00.000Z',
      }),
    ).toBeNull()
  })

  it('恢复校验：到期日非法 / 判定缺时间都拒绝', () => {
    const base = {
      records: [
        {
          id: 't1',
          code: '600000',
          title: '标题',
          body: '',
          reason: '',
          createdAt: '2026-11-01T00:00:00.000Z',
          updatedAt: '2026-11-01T00:00:00.000Z',
          points: [
            {
              id: 'p1',
              due: '2026-02-30',
              condition: '条件',
              verdict: 'pending',
              judgedAt: null,
              verdictNote: '',
              history: [],
            },
          ],
        },
      ],
      updatedAt: '2026-11-01T00:00:00.000Z',
    }
    expect(parseThesisNotesState(base)).toBeNull()

    const missingJudgedAt = {
      ...base,
      records: [
        {
          ...base.records[0],
          points: [
            { ...base.records[0].points[0], due: '2026-03-01', verdict: 'met', judgedAt: null },
          ],
        },
      ],
    }
    expect(parseThesisNotesState(missingJudgedAt)).toBeNull()
  })

  it('恢复校验：合法内容原样通过', () => {
    const parsed = parseThesisNotesState({
      records: [
        {
          id: 't1',
          code: '600000',
          title: '标题',
          body: '正文',
          reason: '理由',
          createdAt: '2026-11-01T00:00:00.000Z',
          updatedAt: '2026-11-01T00:00:00.000Z',
          points: [
            {
              id: 'p1',
              due: '2026-03-01',
              condition: '条件',
              verdict: 'pending',
              judgedAt: null,
              verdictNote: '',
              history: [],
            },
          ],
        },
      ],
      updatedAt: '2026-11-01T00:00:00.000Z',
    })
    expect(parsed?.records[0].title).toBe('标题')
    expect(parsed?.records[0].points[0].due).toBe('2026-03-01')
  })
})
