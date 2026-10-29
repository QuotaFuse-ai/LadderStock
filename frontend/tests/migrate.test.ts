/**
 * 分片迁移契约：v1 → v2 成功、失败丢弃、版本超前丢弃、非对象丢弃。
 *
 * 全部通过真实的 IndexedDB（fake-indexeddb）跑 persiSt 的 rehydrate 路径，
 * 避免只测纯函数而漏掉落库格式（`{ state, version }`）的差异。
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'

import { KV_STORE, META_STORE, closeStateDatabase, idbDelete, idbGet, idbSet } from '../persist/indexeddb'
import { clearMigrationLog, readMigrationLog } from '../persist/migrate'
import { EMPTY_SECTIONS, useReviewDraft } from '../stores/reviewDraft'
import { useWatchSnapshot } from '../stores/watchSnapshot'

interface Persisted<T> {
  state: T
  version: number
}

async function writeShard<T>(key: string, state: T, version: number): Promise<void> {
  const payload: Persisted<T> = { state, version }
  await idbSet(KV_STORE, key, JSON.stringify(payload))
}

async function readShard<T>(key: string): Promise<Persisted<T> | null> {
  const raw = await idbGet<string>(KV_STORE, key)
  if (typeof raw !== 'string') return null
  return JSON.parse(raw) as Persisted<T>
}

async function settle(): Promise<void> {
  // 迁移日志与删分片都是「有则写、失败不阻塞」，给它们两个宏任务落地
  await new Promise((resolve) => setTimeout(resolve, 20))
}

beforeEach(async () => {
  await closeStateDatabase()
  await idbDelete(META_STORE, 'migration-log')
  await clearMigrationLog()
  await idbDelete(KV_STORE, 'review_draft')
  await idbDelete(KV_STORE, 'watch_snapshot')
  await idbDelete(KV_STORE, 'validation_draft')
})

describe('reviewDraft v1 → v2', () => {
  it('把单块正文迁到「情绪」段，其余四段留空', async () => {
    await writeShard(
      'review_draft',
      { date: '2026-10-27', text: '情绪冰点，空间板 3 进 4 失败。' },
      1,
    )

    await useReviewDraft.persist.rehydrate()

    const state = useReviewDraft.getState()
    expect(state.tradeDate).toBe('2026-10-27')
    expect(state.sections.sentiment).toBe('情绪冰点，空间板 3 进 4 失败。')
    expect(state.sections.capital).toBe('')
    expect(state.sections.theme).toBe('')
    expect(state.sections.dragonTiger).toBe('')
    expect(state.sections.leader).toBe('')
    expect(state.activeSection).toBe('sentiment')

    const log = await readMigrationLog()
    expect(log.at(-1)).toMatchObject({ store: 'reviewDraft', from: 1, to: 2, outcome: 'migrated' })
  })

  it('迁移后再次落库带 v2 版本号', async () => {
    await writeShard('review_draft', { date: '2026-10-27', text: '旧正文' }, 1)
    await useReviewDraft.persist.rehydrate()
    await settle()
    // 触发一次写入，让 persist 用当前版本覆盖分片
    useReviewDraft.getState().setSection('theme', '主线：算力')
    await new Promise((resolve) => setTimeout(resolve, 50))

    const shard = await readShard<Record<string, unknown>>('review_draft')
    expect(shard?.version).toBe(2)
  })
})

describe('丢弃语义', () => {
  it('分片版本高于前端时丢弃，并回到空草稿，不阻塞启动', async () => {
    await writeShard('review_draft', { sections: { sentiment: '未来数据' } }, 99)

    await expect(useReviewDraft.persist.rehydrate()).resolves.toBeUndefined()
    await settle()

    expect(useReviewDraft.getState().sections).toEqual(EMPTY_SECTIONS)
    // 坏分片已被丢弃，落库内容被当前版本的空状态覆盖，旧正文不留残影
    const shard = await readShard<{ sections: Record<string, string> }>('review_draft')
    expect(shard?.version).toBe(2)
    expect(JSON.stringify(shard).includes('未来数据')).toBe(false)

    const log = await readMigrationLog()
    expect(log.at(-1)).toMatchObject({ store: 'reviewDraft', from: 99, outcome: 'discarded' })
    expect(log.at(-1)?.detail).toContain('高于当前前端')
  })

  it('分片内容不是对象时丢弃', async () => {
    await idbSet(KV_STORE, 'validation_draft', JSON.stringify({ state: [], version: 1 }))
    const { useValidationDraft } = await import('../stores/validationDraft')

    await expect(useValidationDraft.persist.rehydrate()).resolves.toBeUndefined()
    await settle()

    const entry = (await readMigrationLog()).find((item) => item.store === 'validationDraft')
    expect(entry).toMatchObject({ outcome: 'discarded' })
    expect(entry?.detail).toContain('不是对象')
  })

  it('缺少迁移步骤时丢弃而不是抛错', async () => {
    await writeShard('validation_draft', { date: '2026-10-01', conditions: [] }, 0)
    const { useValidationDraft } = await import('../stores/validationDraft')

    await expect(useValidationDraft.persist.rehydrate()).resolves.toBeUndefined()
    await settle()

    expect(useValidationDraft.getState().conditions).toEqual([])
    const log = await readMigrationLog()
    expect(log.at(-1)).toMatchObject({ store: 'validationDraft', outcome: 'discarded' })
  })
})

describe('watchSnapshot v1 → v2', () => {
  it('v1 没有 fetchedAt，迁移后标记为空快照（需重取），不信任旧行', async () => {
    await writeShard(
      'watch_snapshot',
      { date: '2026-10-27', rows: [{ code: '000001', name: '平安银行' }] },
      1,
    )

    await useWatchSnapshot.persist.rehydrate()

    const state = useWatchSnapshot.getState()
    expect(state.tradeDate).toBe('2026-10-27')
    expect(state.fetchedAt).toBeNull()
    expect(state.rows).toHaveLength(1)
  })
})
