import {
  existingPageTarget,
  type CatalogItem,
  type CatalogStatus,
  type SkillCatalog,
} from '../../lib/skills'

/**
 * 首页 B 块「研究工具快捷入口」的抽样逻辑（QUOT-531）。
 *
 * 数据只来自 `content/skills/catalog.json`（`readCatalog()`），这里不写第二份清单、
 * 不编演示数据：能进快捷入口的条目必须是点一下就有落点的——
 * - `ready` / `partial` / `unconnected` → `/skills/:id`（详情页看步骤与提示词）；
 * - `existing_page` → `existingPageTarget(item)`（如 `/limit-up`、`/watch`）。
 * `planned` / `not_supported` 没有落点，不参与抽样。
 *
 * 抽样口径：一组最多出一条，先按组覆盖，组内优先 `ready` / `existing_page`；
 * 随机源可注入，页面每次挂载与「换一批」都会重新抽。
 */

export const HOME_SHORTCUT_COUNT = 4

/** 直接可用的状态；组内优先从这一档取。 */
const PREFERRED_STATUSES: readonly CatalogStatus[] = ['ready', 'existing_page']

/** 条目点一下去哪：`/skills/:id` 或已承载页面；没有落点返回 `null`。 */
export function shortcutHref(item: CatalogItem): string | null {
  if (item.status === 'existing_page') return existingPageTarget(item)
  if (item.status !== 'ready' && item.status !== 'partial' && item.status !== 'unconnected') {
    return null
  }
  return item.package ? `/skills/${item.id}` : null
}

export interface PickHomeShortcutsOptions {
  count?: number
  /** 注入随机源（测试用）；默认 `Math.random`。 */
  random?: () => number
}

function pickOne<T>(list: readonly T[], random: () => number): T {
  // random() 理论上是 [0, 1)，夹一下避免注入的随机源越界
  const index = Math.min(list.length - 1, Math.floor(random() * list.length))
  return list[index]
}

function shuffle<T>(list: readonly T[], random: () => number): T[] {
  const next = [...list]
  for (let i = next.length - 1; i > 0; i -= 1) {
    const j = Math.min(i, Math.floor(random() * (i + 1)))
    ;[next[i], next[j]] = [next[j], next[i]]
  }
  return next
}

interface GroupBucket {
  preferred: CatalogItem[]
  fallback: CatalogItem[]
}

/**
 * 从目录里按组随机抽 `count` 条（默认 4）：一组最多一条，组内优先 `ready` /
 * `existing_page`；有可优先条目的组排在前面。组数不够时才从剩余可点条目补齐。
 */
export function pickHomeShortcuts(
  catalog: SkillCatalog,
  { count = HOME_SHORTCUT_COUNT, random = Math.random }: PickHomeShortcutsOptions = {},
): CatalogItem[] {
  if (count <= 0) return []

  const eligible = catalog.items.filter((item) => shortcutHref(item) !== null)
  if (eligible.length === 0) return []

  const buckets = new Map<string, GroupBucket>()
  for (const item of eligible) {
    const bucket = buckets.get(item.group) ?? { preferred: [], fallback: [] }
    if (PREFERRED_STATUSES.includes(item.status)) bucket.preferred.push(item)
    else bucket.fallback.push(item)
    buckets.set(item.group, bucket)
  }

  // 先随机打散分组，再把「有 ready / existing_page」的组稳定排到前面
  const orderedGroups = shuffle([...buckets.entries()], random).sort(
    ([, a], [, b]) => (a.preferred.length > 0 ? 0 : 1) - (b.preferred.length > 0 ? 0 : 1),
  )

  const picked: CatalogItem[] = []
  for (const [, bucket] of orderedGroups) {
    if (picked.length >= count) break
    picked.push(pickOne(bucket.preferred.length > 0 ? bucket.preferred : bucket.fallback, random))
  }

  if (picked.length < count) {
    const used = new Set(picked.map((item) => item.id))
    const leftovers = shuffle(
      eligible.filter((item) => !used.has(item.id)),
      random,
    )
    // 补齐时同样先走 ready / existing_page，再退到 partial / unconnected
    const orderedLeftovers = [
      ...leftovers.filter((item) => PREFERRED_STATUSES.includes(item.status)),
      ...leftovers.filter((item) => !PREFERRED_STATUSES.includes(item.status)),
    ]
    for (const item of orderedLeftovers) {
      if (picked.length >= count) break
      picked.push(item)
    }
  }

  return picked
}
