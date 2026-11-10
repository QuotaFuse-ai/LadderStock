import { describe, expect, it } from 'vitest'

import {
  HOME_SHORTCUT_COUNT,
  pickHomeShortcuts,
  shortcutHref,
} from '../src/screens/home/homeShortcuts'
import {
  existingPageTarget,
  readCatalog,
  type CatalogItem,
  type CatalogStatus,
  type SkillCatalog,
} from '../src/lib/skills'

/**
 * QUOT-531 首页研究工具快捷入口的抽样口径。
 *
 * 钉的都是可证的行为：4 条、跨组、只出可点条目、组内优先 ready / existing_page、
 * 换一批重抽；数据口径与 `/skills` 列表完全一致（只读 `catalog.json`）。
 */

function makeItem(
  id: number,
  group: string,
  status: CatalogStatus,
  packageName = 'pkg',
): CatalogItem {
  return {
    id,
    slug: `skill-${id}`,
    label: `条目 ${id}`,
    group,
    priority: 'P2',
    package: packageName,
    steps: packageName ? [1] : [],
    status,
    note: '',
  }
}

function catalogOf(items: CatalogItem[]): SkillCatalog {
  const groups = new Map<string, number>()
  for (const item of items) groups.set(item.group, (groups.get(item.group) ?? 0) + 1)
  return {
    version: 2,
    updated: '2026-11-10',
    total: items.length,
    groups: [...groups.entries()].map(([key, count]) => ({ key, label: key, count })),
    items,
  }
}

/** 确定性随机源：同一 seed 同一序列。 */
function makeRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

describe('pickHomeShortcuts（QUOT-531）', () => {
  it('真实目录：抽 4 条、分组互不相同、全部可点', () => {
    const picks = pickHomeShortcuts(readCatalog(), { random: makeRng(7) })
    expect(picks).toHaveLength(HOME_SHORTCUT_COUNT)
    expect(new Set(picks.map((item) => item.group)).size).toBe(HOME_SHORTCUT_COUNT)
    for (const item of picks) {
      expect(shortcutHref(item)).not.toBeNull()
      expect(['ready', 'partial', 'unconnected', 'existing_page']).toContain(item.status)
    }
  })

  it('组内优先 ready / existing_page：同组有可用条目时不落 unconnected', () => {
    const catalog = catalogOf([
      makeItem(1, '甲', 'ready'),
      makeItem(2, '甲', 'unconnected'),
      makeItem(3, '乙', 'partial'),
      makeItem(4, '乙', 'ready'),
      makeItem(5, '丙', 'ready'),
      makeItem(6, '丙', 'unconnected'),
      makeItem(7, '丁', 'existing_page'),
      makeItem(8, '丁', 'ready'),
    ])
    for (let seed = 1; seed <= 20; seed += 1) {
      const picks = pickHomeShortcuts(catalog, { random: makeRng(seed) })
      expect(picks).toHaveLength(4)
      expect(new Set(picks.map((item) => item.group)).size).toBe(4)
      expect(picks.every((item) => item.status === 'ready' || item.status === 'existing_page')).toBe(
        true,
      )
    }
  })

  it('按组覆盖：组数够时每个分组最多一条', () => {
    const catalog = catalogOf([
      makeItem(1, '甲', 'ready'),
      makeItem(2, '甲', 'ready'),
      makeItem(3, '乙', 'ready'),
      makeItem(4, '丙', 'ready'),
      makeItem(5, '丁', 'ready'),
      makeItem(6, '戊', 'ready'),
    ])
    for (let seed = 1; seed <= 20; seed += 1) {
      const picks = pickHomeShortcuts(catalog, { random: makeRng(seed) })
      expect(picks).toHaveLength(4)
      expect(new Set(picks.map((item) => item.group)).size).toBe(4)
    }
  })

  it('planned / not_supported 没有落点，不参与抽样', () => {
    const catalog = catalogOf([
      makeItem(1, '甲', 'ready'),
      makeItem(2, '乙', 'planned'),
      makeItem(3, '丙', 'not_supported'),
    ])
    const picks = pickHomeShortcuts(catalog, { random: makeRng(3), count: 4 })
    expect(picks.map((item) => item.id)).toEqual([1])
  })

  it('组数不足 4 时从剩余可点条目补齐，且不重复', () => {
    const catalog = catalogOf([
      makeItem(1, '甲', 'ready'),
      makeItem(2, '甲', 'ready'),
      makeItem(3, '乙', 'partial'),
    ])
    const picks = pickHomeShortcuts(catalog, { random: makeRng(11) })
    expect(picks).toHaveLength(3)
    expect(new Set(picks.map((item) => item.id)).size).toBe(3)
  })

  it('同 seed 结果稳定，换一批（新随机序列）会重抽', () => {
    const catalog = readCatalog()
    const first = pickHomeShortcuts(catalog, { random: makeRng(42) })
    const again = pickHomeShortcuts(catalog, { random: makeRng(42) })
    expect(again.map((item) => item.id)).toEqual(first.map((item) => item.id))

    const rng = makeRng(42)
    const batch1 = pickHomeShortcuts(catalog, { random: rng })
    const batch2 = pickHomeShortcuts(catalog, { random: rng })
    expect(batch2.map((item) => item.id)).not.toEqual(batch1.map((item) => item.id))
  })

  it('落点：existing_page 走承载页面，其余可点条目走 /skills/:id', () => {
    const catalog = readCatalog()
    const existing = catalog.items.filter((item) => item.status === 'existing_page')
    expect(existing.length).toBeGreaterThan(0)
    for (const item of existing) {
      expect(shortcutHref(item)).toBe(existingPageTarget(item))
    }
    const ready = catalog.items.find((item) => item.status === 'ready' && item.package)
    expect(ready).toBeTruthy()
    expect(shortcutHref(ready!)).toBe(`/skills/${ready!.id}`)
  })
})
