import { usePersistenceStatus } from '../../persist/status'
import { Banner } from './ui'

/**
 * 持久化状态提示：恢复失败、迁移结果、索引上报是否可达。
 * 只提示、不阻塞——用户永远能继续写草稿（任务要求 2 / 3）。
 *
 * 最多同时出现两类：迁移/丢弃（warn）优先，恢复失败（danger）其次，
 * 迁移成功（info）最后；全部为 0 时整块不渲染。
 *
 * **同类最多 2 条**（UI-SPEC §2 提示条行）：分片可以有很多个，逐个铺开会把
 * 首屏正文挤出视口，所以第 3 条起收进一行「另有 N 条提示」，分片名逐个列出、不丢信息。
 */
const MAX_PER_TONE = 2

/** 「另有 N 条」收口行：计数与被折叠的分片名都要上屏，折叠不等于丢弃。 */
function OverflowNote({ items }: { items: string[] }) {
  return (
    <p className="ldst-num px-1 text-caption leading-body text-text-muted">
      另有 {items.length} 条提示：{items.join('、')}
    </p>
  )
}

export default function PersistenceBanner() {
  const hydration = usePersistenceStatus((state) => state.hydration)
  const errors = usePersistenceStatus((state) => state.errors)
  const migrationLog = usePersistenceStatus((state) => state.migrationLog)

  const failed = Object.entries(hydration).filter(([, value]) => value === 'error')
  const discarded = migrationLog.filter((entry) => entry.outcome === 'discarded')
  const migrated = migrationLog.filter((entry) => entry.outcome === 'migrated')

  if (failed.length === 0 && discarded.length === 0 && migrated.length === 0) return null

  const shownDiscards = discarded.slice(0, MAX_PER_TONE)
  const hiddenDiscards = discarded.slice(MAX_PER_TONE).map((entry) => entry.store)
  const shownFailures = failed.slice(0, MAX_PER_TONE)
  const hiddenFailures = failed.slice(MAX_PER_TONE).map(([storeId]) => storeId)

  return (
    <div className="flex flex-col gap-2 px-5 pt-5">
      {shownDiscards.map((entry) => (
        <Banner
          key={`discard-${entry.id}`}
          tone="warn"
          title={`分片 ${entry.store} 迁移失败（v${entry.from} → v${entry.to}）已丢弃`}
        >
          {entry.detail ? `${entry.detail}，相关内容需重新填写。` : '相关内容需重新填写。'}
        </Banner>
      ))}
      {hiddenDiscards.length > 0 && <OverflowNote items={hiddenDiscards} />}
      {shownFailures.map(([storeId]) => (
        <Banner key={`fail-${storeId}`} tone="danger" title={`分片 ${storeId} 恢复失败`}>
          原因：{errors[storeId] ?? '未知原因'}。已按空草稿启动，可以直接继续填写。
        </Banner>
      ))}
      {hiddenFailures.length > 0 && <OverflowNote items={hiddenFailures} />}
      {migrated.length > 0 && (
        <Banner tone="info">
          本机草稿已从旧版本迁移到当前版本（{migrated.length} 个分片）。
        </Banner>
      )}
    </div>
  )
}
