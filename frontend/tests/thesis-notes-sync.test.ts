/**
 * 多窗口一致（QUOT-452 / DEC-037 同法）：本窗口写入广播一次，远端写入即时反映，
 * 非法内容整条丢弃，同内容不回推成回声。
 *
 * 假频道在建店前替换全局 BroadcastChannel，验证的是 store 的接线而不是浏览器实现。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []

  readonly name: string
  readonly posted: unknown[] = []
  private readonly listeners = new Set<(event: MessageEvent) => void>()

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (type === 'message') this.listeners.add(listener)
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (type === 'message') this.listeners.delete(listener)
  }

  postMessage(data: unknown): void {
    this.posted.push(data)
  }

  close(): void {
    this.listeners.clear()
  }

  emit(data: unknown): void {
    for (const listener of this.listeners) listener({ data } as MessageEvent)
  }
}

vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)

const { THESIS_NOTES_MESSAGE, applyRemoteThesisNotes, useThesisNotes } = await import(
  '../stores/thesisNotes'
)

function channel(): FakeBroadcastChannel {
  const first = FakeBroadcastChannel.instances[0]
  if (!first) throw new Error('thesisNotes 没有打开 BroadcastChannel')
  return first
}

const REMOTE_RECORD = {
  id: 't-remote',
  code: '600000',
  title: '另一个窗口写的论点',
  body: '正文',
  reason: '理由',
  createdAt: '2026-11-05T01:00:00.000Z',
  updatedAt: '2026-11-05T01:00:00.000Z',
  points: [
    {
      id: 'p-remote',
      due: '2026-12-01',
      condition: '毛利率 ≥ 30% 算成立',
      verdict: 'pending',
      judgedAt: null,
      verdictNote: '',
      history: [],
    },
  ],
}

beforeEach(() => {
  useThesisNotes.setState({ records: [], updatedAt: null })
  channel().posted.length = 0
})

describe('多窗口同步', () => {
  it('本窗口写入广播一次，且带上完整记录', () => {
    const result = useThesisNotes.getState().addThesis({
      code: '600000',
      title: '标题',
      body: '',
      reason: '',
      condition: '条件',
      due: '2026-12-01',
    })
    expect(result.ok).toBe(true)

    expect(channel().posted).toHaveLength(1)
    expect(channel().posted[0]).toMatchObject({
      type: THESIS_NOTES_MESSAGE,
      updatedAt: expect.any(String),
      records: [{ code: '600000', title: '标题' }],
    })
  })

  it('远端写入立即出现在本窗口，且不再回推（无回声）', () => {
    const applied = applyRemoteThesisNotes({
      type: THESIS_NOTES_MESSAGE,
      records: [REMOTE_RECORD],
      updatedAt: '2026-11-05T01:00:00.000Z',
    })
    expect(applied).toBe(true)
    expect(useThesisNotes.getState().records[0].title).toBe('另一个窗口写的论点')
    expect(channel().posted).toHaveLength(0)
  })

  it('同内容的远端消息不重复应用', () => {
    const payload = {
      type: THESIS_NOTES_MESSAGE,
      records: [REMOTE_RECORD],
      updatedAt: '2026-11-05T01:00:00.000Z',
    }
    expect(applyRemoteThesisNotes(payload)).toBe(true)
    expect(applyRemoteThesisNotes(payload)).toBe(false)
  })

  it('非法远端内容整条丢弃（空条件 / 非法到期日 / 错误消息类型）', () => {
    const emptyCondition = {
      ...REMOTE_RECORD,
      points: [{ ...REMOTE_RECORD.points[0], condition: '' }],
    }
    expect(
      applyRemoteThesisNotes({ type: THESIS_NOTES_MESSAGE, records: [emptyCondition], updatedAt: null }),
    ).toBe(false)
    expect(
      applyRemoteThesisNotes({
        type: THESIS_NOTES_MESSAGE,
        records: [
          { ...REMOTE_RECORD, points: [{ ...REMOTE_RECORD.points[0], due: '2026-02-30' }] },
        ],
        updatedAt: null,
      }),
    ).toBe(false)
    expect(applyRemoteThesisNotes({ type: 'watch_settings', records: [REMOTE_RECORD] })).toBe(false)
    expect(useThesisNotes.getState().records).toHaveLength(0)
  })
})
