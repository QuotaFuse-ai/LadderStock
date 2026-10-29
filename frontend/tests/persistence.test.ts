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
import { EMPTY_SECTIONS, useReviewDraft } from '../stores/reviewDraft'
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
  it('输入即落库：版本号与正文都在分片里', async () => {
    useReviewDraft.getState().setTradeDate('2026-10-28')
    useReviewDraft.getState().setSection('sentiment', '情绪修复，量能不足。')
    await waitFlush()

    const shard = await readShard<{ sections: Record<string, string>; tradeDate: string }>(
      'review_draft',
    )
    expect(shard?.version).toBe(2)
    expect(shard?.state.tradeDate).toBe('2026-10-28')
    expect(shard?.state.sections.sentiment).toBe('情绪修复，量能不足。')
  })

  it('新会话 rehydrate 能把上次的草稿整段恢复（不需迁移）', async () => {
    const stored: Persisted<Record<string, unknown>> = {
      version: 2,
      state: {
        tradeDate: '2026-10-28',
        sections: { ...EMPTY_SECTIONS, capital: '主力净流入 +3.2 亿' },
        activeSection: 'capital',
        updatedAt: '2026-10-28T07:00:00.000Z',
      },
    }
    await idbSet(KV_STORE, 'review_draft', JSON.stringify(stored))

    await useReviewDraft.persist.rehydrate()

    const state = useReviewDraft.getState()
    expect(state.sections.capital).toBe('主力净流入 +3.2 亿')
    expect(state.activeSection).toBe('capital')
    expect(await readMigrationLog()).toHaveLength(0)
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

  it('清空草稿后分片里不再有正文', async () => {
    useReviewDraft.getState().setSection('leader', '空间板：5 进 6')
    await waitFlush()
    useReviewDraft.getState().reset()
    await waitFlush()

    const shard = await readShard<{ sections: Record<string, string> }>('review_draft')
    expect(Object.values(shard?.state.sections ?? {}).join('')).toBe('')
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
