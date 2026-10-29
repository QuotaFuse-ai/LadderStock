/**
 * 分片 schema 版本与迁移（文档 §6.4「schema 版本与迁移」）。
 *
 * 规则：
 * - 每个分片落库时带 `version`（Zustand persist 自己写入）；
 * - 版本落后时按 v1→v2→…逐级迁移，成功后写迁移日志；
 * - 迁移失败 / 版本高于当前前端时：**丢弃该分片**并写日志，返回初始态，
 *   绝不让一个坏分片阻塞整个看板启动。
 */

import { KV_STORE, META_STORE, idbDelete, idbGet, idbSet } from './indexeddb'

/** 当前前端分片 schema 版本；必须与 `config/state.yaml`、`stores/manifest.json` 一致。 */
export const SCHEMA_VERSION = 2

export type MigrationOutcome = 'migrated' | 'discarded'

export interface MigrationLogEntry {
  id: string
  store: string
  from: number
  to: number
  at: string
  outcome: MigrationOutcome
  detail?: string
}

type MutableState = Record<string, unknown>
type MigrationFn = (state: MutableState, at: string) => MutableState

const LOG_KEY = 'migration-log'
const LOG_LIMIT = 20

// ---------------------------------------------------------------- v1 数据结构

/** v1 的复盘草稿：单块文本框 + 交易日。 */
interface ReviewDraftV1 {
  date?: string | null
  text?: string
}

/** v1 的验证草稿：条件没有 confirmed 字段、也没有稳定 id。 */
interface ValidationConditionV1 {
  code?: string
  trigger?: string
  note?: string
  confirmed?: boolean
  id?: string
}

/** v1 的盯盘快照：没有 fetchedAt，只有日期与行数据。 */
interface WatchSnapshotV1 {
  date?: string | null
  rows?: unknown
  sourceDetail?: string | null
}

const REVIEW_SECTION_KEYS = ['sentiment', 'capital', 'theme', 'dragonTiger', 'leader'] as const

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function migrateReviewDraftV1ToV2(state: MutableState, at: string): MutableState {
  const legacy = state as ReviewDraftV1
  const text = asString(legacy.text)
  const tradeDate = typeof legacy.date === 'string' ? legacy.date : null
  // v1 只有一个大文本框；v2 拆成五段，正文落到「情绪」段，其余留空由用户续写。
  return {
    tradeDate,
    sections: {
      sentiment: text,
      capital: '',
      theme: '',
      dragonTiger: '',
      leader: '',
    },
    activeSection: REVIEW_SECTION_KEYS[0],
    updatedAt: tradeDate ? `${tradeDate}T00:00:00.000Z` : at,
  }
}

function migrateValidationDraftV1ToV2(state: MutableState, at: string): MutableState {
  const legacy = state as { date?: unknown; conditions?: unknown }
  const rawConditions = Array.isArray(legacy.conditions) ? legacy.conditions : []
  const conditions = rawConditions.map((item, index) => {
    const condition = (item ?? {}) as ValidationConditionV1
    return {
      id: asString(condition.id) || `v1-${index}`,
      code: asString(condition.code),
      trigger: asString(condition.trigger),
      note: asString(condition.note),
      confirmed: condition.confirmed === true,
    }
  })
  return {
    tradeDate: typeof legacy.date === 'string' ? legacy.date : null,
    conditions,
    updatedAt: at,
  }
}

function migrateWatchSnapshotV1ToV2(state: MutableState, at: string): MutableState {
  const legacy = state as WatchSnapshotV1
  return {
    tradeDate: typeof legacy.date === 'string' ? legacy.date : null,
    // v1 没有 fetchedAt，无法判断 TTL：置空 → 下次启动明确标记「需重取」，不信任旧值。
    fetchedAt: null,
    sourceDetail: typeof legacy.sourceDetail === 'string' ? legacy.sourceDetail : null,
    rows: Array.isArray(legacy.rows) ? legacy.rows : [],
    updatedAt: at,
  }
}

/** 逐级迁移表：键是「起始版本」，值把该版本升到下一版。 */
const MIGRATIONS: Record<string, Record<number, MigrationFn>> = {
  watchSnapshot: { 1: migrateWatchSnapshotV1ToV2 },
  reviewDraft: { 1: migrateReviewDraftV1ToV2 },
  validationDraft: { 1: migrateValidationDraftV1ToV2 },
}

// ---------------------------------------------------------------- 迁移日志

const pendingEntries: MigrationLogEntry[] = []
let flushChain: Promise<void> = Promise.resolve()

export async function readMigrationLog(): Promise<MigrationLogEntry[]> {
  try {
    const stored = await idbGet<MigrationLogEntry[]>(META_STORE, LOG_KEY)
    return Array.isArray(stored) ? stored : []
  } catch {
    return []
  }
}

export async function flushMigrationLog(): Promise<void> {
  if (pendingEntries.length === 0) return
  const existing = await readMigrationLog()
  const merged = [...existing, ...pendingEntries.splice(0, pendingEntries.length)].slice(-LOG_LIMIT)
  await idbSet(META_STORE, LOG_KEY, merged)
}

export function scheduleMigrationLogFlush(): Promise<void> {
  flushChain = flushChain.then(flushMigrationLog).catch(() => undefined)
  return flushChain
}

export async function clearMigrationLog(): Promise<void> {
  pendingEntries.length = 0
  await idbSet(META_STORE, LOG_KEY, [])
}

function recordMigration(entry: Omit<MigrationLogEntry, 'id'>): void {
  pendingEntries.push({ id: `${entry.store}-${entry.at}-${pendingEntries.length}`, ...entry })
  void scheduleMigrationLogFlush()
}

// ---------------------------------------------------------------- 迁移入口

export interface MigrationRequest {
  storeId: string
  persistKey: string
  fromVersion: number
  persisted: unknown
  /** 分片被丢弃时使用的初始态（由 store 工厂传入）。 */
  fallback: unknown
}

/**
 * Zustand persist 的 `migrate` 实现。
 * 任何失败都被吸收成「丢弃分片 + 日志」，返回初始态，保证启动不被阻塞。
 */
export function migratePersistedState({
  storeId,
  persistKey,
  fromVersion,
  persisted,
  fallback,
}: MigrationRequest): unknown {
  const at = new Date().toISOString()

  if (fromVersion === SCHEMA_VERSION) return persisted

  if (typeof persisted !== 'object' || persisted === null || Array.isArray(persisted)) {
    recordMigration({
      store: storeId,
      from: fromVersion,
      to: SCHEMA_VERSION,
      at,
      outcome: 'discarded',
      detail: '持久化内容不是对象，已丢弃分片',
    })
    void idbDelete(KV_STORE, persistKey).catch(() => undefined)
    return fallback
  }

  if (fromVersion > SCHEMA_VERSION) {
    recordMigration({
      store: storeId,
      from: fromVersion,
      to: SCHEMA_VERSION,
      at,
      outcome: 'discarded',
      detail: `落库版本 v${fromVersion} 高于当前前端 v${SCHEMA_VERSION}，已丢弃分片避免误读`,
    })
    void idbDelete(KV_STORE, persistKey).catch(() => undefined)
    return fallback
  }

  let state = { ...(persisted as MutableState) }
  try {
    for (let version = fromVersion; version < SCHEMA_VERSION; version += 1) {
      const step = MIGRATIONS[storeId]?.[version]
      if (!step) throw new Error(`分片 ${storeId} 缺少 v${version} → v${version + 1} 的迁移`)
      state = step(state, at)
    }
    recordMigration({ store: storeId, from: fromVersion, to: SCHEMA_VERSION, at, outcome: 'migrated' })
    return state
  } catch (error) {
    recordMigration({
      store: storeId,
      from: fromVersion,
      to: SCHEMA_VERSION,
      at,
      outcome: 'discarded',
      detail: error instanceof Error ? error.message : String(error),
    })
    void idbDelete(KV_STORE, persistKey).catch(() => undefined)
    return fallback
  }
}
