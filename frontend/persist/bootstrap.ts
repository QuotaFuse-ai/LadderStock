/**
 * 启动顺序（main.tsx 渲染前 await 一次）：
 * 1. 读 `data/state/frontend-index.json` 的 `pending_reset`，先清掉被 `ldst state reset`
 *    登记的分片，并回执；
 * 2. 逐个 rehydrate（迁移在 rehydrate 内部发生，失败只丢分片不抛错）；
 * 3. 汇总迁移日志 + 上报一次索引。
 *
 * 整个流程即使 IndexedDB / 开发服务器不可用也只降级，不阻塞看板启动。
 */

import '../stores'
import { readMigrationLog, scheduleMigrationLogFlush } from './migrate'
import { registeredStores, scheduleIndexReport, syncPendingResets } from './indexReporter'
import { usePersistenceStatus } from './status'

export interface BootstrapResult {
  resetShards: string[]
  hydrated: string[]
  failed: string[]
}

export async function bootstrapPersistence(): Promise<BootstrapResult> {
  const status = usePersistenceStatus.getState()
  const resetShards = await syncPendingResets().catch(() => [] as string[])

  const hydrated: string[] = []
  const failed: string[] = []

  for (const store of registeredStores()) {
    status.markHydration(store.meta.id, 'pending')
    try {
      await Promise.resolve(store.rehydrate())
      status.markHydration(store.meta.id, 'ready')
      hydrated.push(store.meta.id)
    } catch (error) {
      failed.push(store.meta.id)
      status.markHydration(
        store.meta.id,
        'error',
        error instanceof Error ? error.message : String(error),
      )
    }
  }

  await scheduleMigrationLogFlush()
  status.setMigrationLog(await readMigrationLog())
  scheduleIndexReport(0)

  return { resetShards, hydrated, failed }
}
