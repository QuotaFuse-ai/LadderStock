/**
 * 持久化往返：写入 → 落 IndexedDB → 下次 rehydrate 原样恢复；
 * 快照信任规则；分片体积上限。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  KV_STORE,
  MAX_SHARD_BYTES,
  META_STORE,
  closeStateDatabase,
  createIdbJSONStorage,
  idbDelete,
  idbGet,
  idbSet,
} from '../persist/indexeddb'
import { readMigrationLog } from '../persist/migrate'
import { EMPTY_SECTIONS, selectSlot, useReviewDraft } from '../stores/reviewDraft'
import { useValidationDraft } from '../stores/validationDraft'
import { selectFreshness, selectTrustedRows, useWatchSnapshot } from '../stores/watchSnapshot'
import { localDateIso } from '../persist/freshness'

interface Persisted<T> {
  state: T
  version: number
}

async function waitFlush(ms = 60): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

async function readShard<T>(key: string): Promise<Persisted<T> | null> {
  const raw = await idbGet<string>(KV_STORE, key)
  return typeof raw === 'string' ? (JSON.parse(raw) as Persisted<T>) : null
}

beforeEach(async () => {
  await closeStateDatabase()
  for (const key of ['review_draft', 'validation_draft', 'watch_snapshot']) {
    await idbDelete(KV_STORE, key)
  }
  await idbDelete(META_STORE, 'migration-log')
})

describe('草稿分片', () => {
  it('输入即落库：版本号与按日分槽的正文都在分片里，分片不再存交易日', async () => {
    useReviewDraft.getState().setSection('2026-10-28', 'sentiment', '情绪修复，量能不足。')
    await waitFlush()

    const shard = await readShard<{ slots: Record<string, { sections: Record<string, string> }> }>(
      'review_draft',
    )
    expect(shard?.version).toBe(2)
    expect(shard?.state.slots['2026-10-28'].sections.sentiment).toBe('情绪修复，量能不足。')
    // QUOT-354 BUG-B：日期是 URL 的事，写进分片会让 `?date=` 深链被本机状态覆盖
    expect('tradeDate' in (shard?.state as Record<string, unknown>)).toBe(false)
  })

  it('草稿按交易日分槽：切日期不覆盖另一天的笔记（QUOT-354 BUG-C）', async () => {
    const draft = useReviewDraft.getState()
    draft.setSection('2026-11-02', 'sentiment', '11-02：情绪修复，量能不足。')
    draft.setSection('2026-11-03', 'sentiment', '11-03：晋级率回升，断层补平。')

    let state = useReviewDraft.getState()
    expect(selectSlot(state, '2026-11-02').sections.sentiment).toBe('11-02：情绪修复，量能不足。')
    expect(selectSlot(state, '2026-11-03').sections.sentiment).toBe('11-03：晋级率回升，断层补平。')

    // 清空只作用于当天那一份
    useReviewDraft.getState().clearDate('2026-11-03')
    state = useReviewDraft.getState()
    expect(selectSlot(state, '2026-11-03').sections.sentiment).toBe('')
    expect(selectSlot(state, '2026-11-02').sections.sentiment).toBe('11-02：情绪修复，量能不足。')
  })

  it('新会话 rehydrate 能把上次的草稿整段恢复（不需迁移）', async () => {
    const stored: Persisted<Record<string, unknown>> = {
      version: 2,
      state: {
        slots: {
          '2026-10-28': {
            sections: { ...EMPTY_SECTIONS, capital: '主力净流入 +3.2 亿' },
            updatedAt: '2026-10-28T07:00:00.000Z',
          },
        },
        activeSection: 'capital',
        updatedAt: '2026-10-28T07:00:00.000Z',
      },
    }
    await idbSet(KV_STORE, 'review_draft', JSON.stringify(stored))

    await useReviewDraft.persist.rehydrate()

    const state = useReviewDraft.getState()
    expect(selectSlot(state, '2026-10-28').sections.capital).toBe('主力净流入 +3.2 亿')
    expect(selectSlot(state, '2026-10-28').updatedAt).toBe('2026-10-28T07:00:00.000Z')
    expect(state.activeSection).toBe('capital')
    expect(await readMigrationLog()).toHaveLength(0)
  })

  it('旧版单槽分片（tradeDate + sections）恢复时归到它自己的交易日下', async () => {
    await idbSet(
      KV_STORE,
      'review_draft',
      JSON.stringify({
        version: 2,
        state: {
          tradeDate: '2026-11-02',
          sections: { ...EMPTY_SECTIONS, theme: '主线：算力' },
          activeSection: 'theme',
          updatedAt: '2026-11-02T07:39:00.000Z',
        },
      }),
    )

    await useReviewDraft.persist.rehydrate()

    const state = useReviewDraft.getState()
    expect(selectSlot(state, '2026-11-02').sections.theme).toBe('主线：算力')
    // 其它日期不再是这一份——BUG-C 的串日正是由这个共用槽造成
    expect(selectSlot(state, '2026-11-03').sections.theme).toBe('')
    expect(state).not.toHaveProperty('sections')
  })

  it('验证条件连同 id 与勾选状态一起恢复', async () => {
    await idbSet(
      KV_STORE,
      'validation_draft',
      JSON.stringify({
        version: 2,
        state: {
          tradeDate: '2026-10-28',
          conditions: [{ id: 'c-1', code: '000001', trigger: '高开 3%', note: '', confirmed: true }],
          updatedAt: '2026-10-28T07:00:00.000Z',
        },
      }),
    )

    await useValidationDraft.persist.rehydrate()

    expect(useValidationDraft.getState().conditions).toEqual([
      { id: 'c-1', code: '000001', trigger: '高开 3%', note: '', confirmed: true },
    ])
  })

  it('清空当天草稿后分片里不再留这一天的正文', async () => {
    useReviewDraft.getState().setSection('2026-10-29', 'leader', '空间板：5 进 6')
    await waitFlush()
    useReviewDraft.getState().clearDate('2026-10-29')
    await waitFlush()

    const shard = await readShard<{ slots: Record<string, unknown> }>('review_draft')
    expect(shard?.state.slots['2026-10-29']).toBeUndefined()
  })
})

describe('盯盘快照', () => {
  const NOW = new Date('2026-10-28T10:00:00+08:00')

  it('新鲜快照才渲染行数据', () => {
    useWatchSnapshot.getState().setSnapshot({
      tradeDate: localDateIso(NOW),
      fetchedAt: new Date(NOW.getTime() - 30_000).toISOString(),
      rows: [{ code: '000001', name: '平安银行', limitUpCount: 2, changePct: 9.98, status: '连板' }],
    })

    const state = useWatchSnapshot.getState()
    expect(selectFreshness(state, NOW).needsRefetch).toBe(false)
    expect(selectTrustedRows(state, NOW)).toHaveLength(1)
  })

  it('过期快照保留数据但不再信任（不回显旧值）', () => {
    useWatchSnapshot.getState().setSnapshot({
      tradeDate: localDateIso(NOW),
      fetchedAt: new Date(NOW.getTime() - 400_000).toISOString(),
      rows: [{ code: '000001', name: '平安银行', limitUpCount: 2, changePct: 9.98, status: '连板' }],
    })

    const state = useWatchSnapshot.getState()
    expect(state.rows).toHaveLength(1)
    expect(selectFreshness(state, NOW)).toMatchObject({ needsRefetch: true, reason: 'ttl' })
    expect(selectTrustedRows(state, NOW)).toEqual([])
  })

  it('跨交易日快照一律不可信', () => {
    useWatchSnapshot.getState().setSnapshot({
      tradeDate: '2026-10-27',
      fetchedAt: new Date(NOW.getTime() - 10_000).toISOString(),
      rows: [{ code: '000001', name: '平安银行', limitUpCount: 2, changePct: 9.98, status: '连板' }],
    })

    expect(selectFreshness(useWatchSnapshot.getState(), NOW)).toMatchObject({
      needsRefetch: true,
      reason: 'trade-date-changed',
    })
  })
})

describe('分片体积上限', () => {
  it('超过 1MB 的写入被拒绝，避免拖垮看板', async () => {
    const storage = createIdbJSONStorage()
    const tooBig = 'x'.repeat(MAX_SHARD_BYTES + 1)
    await expect(storage.setItem('big', tooBig)).rejects.toThrow(/超过上限/)
    await expect(storage.getItem('big')).resolves.toBeNull()
  })
})
