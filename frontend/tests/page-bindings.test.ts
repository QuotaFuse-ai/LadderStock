import { describe, expect, it } from 'vitest'

import { PAGE_SKILL_BINDINGS, bindingFor } from '../src/lib/skills/pageBindings'

/**
 * QUOT-475：路由 → 技能包绑定表。
 *
 * 口径必须与顶栏面包屑 `navTitleFor()` 一致：查询串不参与匹配、子路径归到**最长**
 * 前缀、`/` 只认首页本身。绑定表是唯一维护点，这里逐个前缀扫一遍，防止
 * 「加了页面忘了绑技能」。
 */

describe('bindingFor 覆盖全部前缀', () => {
  it('表里每个前缀都能命中自己', () => {
    for (const binding of PAGE_SKILL_BINDINGS) {
      const found = bindingFor(binding.prefix)
      expect(found, binding.prefix).not.toBeNull()
      expect(found?.prefix, binding.prefix).toBe(binding.prefix)
      expect(found?.pkg, binding.prefix).toBe(binding.pkg)
    }
  })

  it('长前缀优先：/review/ai 归复盘而不是首页', () => {
    const found = bindingFor('/review/ai')
    expect(found?.prefix).toBe('/review')
    expect(found?.pkg).toBe('review-journal')
    expect(found?.label).toBe('复盘')
  })

  it('/research/600519 归个股深挖（不是首页）', () => {
    const found = bindingFor('/research/600519')
    expect(found?.prefix).toBe('/research')
    expect(found?.pkg).toBe('a-share-dossier')
    expect(found?.label).toBe('个股深挖')
  })

  it('/industry/电子 归产业研究', () => {
    const found = bindingFor('/industry/电子')
    expect(found?.prefix).toBe('/industry')
    expect(found?.pkg).toBe('sector-rotation')
    expect(found?.label).toBe('产业研究')
  })

  it('查询串不参与匹配', () => {
    expect(bindingFor('/market?tab=theme')?.pkg).toBe('a-share-tape')
    expect(bindingFor('/market?tab=theme')?.label).toBe('市场')
    expect(bindingFor('/review?date=2026-11-06')?.pkg).toBe('review-journal')
  })

  it('`/` 只认首页本身，不做 catch-all', () => {
    expect(bindingFor('/')?.pkg).toBeNull()
    expect(bindingFor('/')?.label).toBe('首页')
    expect(bindingFor('/nope')).toBeNull()
    expect(bindingFor('/positions/600519')).toBeNull()
  })

  it('开放域页面绑定 null 包但不丢页面名', () => {
    expect(bindingFor('/skills')?.pkg).toBeNull()
    expect(bindingFor('/settings')?.pkg).toBeNull()
    // `navTitleFor()` 只认 NAV_SECTIONS；设置是 PINNED_NAV，兜底到「工作空间」
    expect(bindingFor('/settings')?.label).toBe('工作空间')
  })
})
