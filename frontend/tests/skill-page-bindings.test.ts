import { describe, expect, it } from 'vitest'

import { NAV_SECTIONS, PINNED_NAV } from '../src/components/Layout'
import { ROUTE_PATHS } from '../src/routes'
import {
  EXPLICIT_NULL_PREFIXES,
  bindingDecision,
  resolveBinding,
} from '../src/lib/skills/pageBindings'

/**
 * 绑定表覆盖率（QUOT-474 / DEC-065）。
 *
 * 「下一步建议 3」自己提的那条：`pageBindings.ts` 按路由前缀匹配，新增页面时最容易漏绑。
 * 这里正向断言「每条已注册页面路由都有绑定或显式声明 `null`」，把漏绑变成红灯，而不是
 * 静默退回无 skill 的抽屉。
 *
 * 覆盖集合从**路由注册表**（`routes.ts`）反推，不从侧栏反推：新页面只加进路由表、忘了配绑定，
 * 也必须在这里变红。只排除两条本来就不渲染页面的路径：404 兜底 `*` 与静态入口 `/index.html`。
 * 旧地址重定向（`/watchlist` / `/positions`）与由页面链入的深链仍在集合内——它们都落到真实页面。
 *
 * 两类合法缺省：
 * - 绑定表里写了规则 → `bound`；
 * - 在 `EXPLICIT_NULL_PREFIXES` 里显式声明不绑（首页 / 目录 / 设置这类开放域）→ `explicit-null`。
 * 其余一律是 `unbound`（漏绑），断言失败。
 */

/** 侧栏项里的 `?tab=` 只用于高亮，覆盖检查只看 path。 */
function pathOf(to: string): string {
  return to.split('?')[0]
}

const NAV_PATHS = [
  ...NAV_SECTIONS.flatMap((section) => section.pages.map((page) => page.to)),
  ...PINNED_NAV.map((page) => page.to),
].map(pathOf)

/** 带参路由换成具体值再判绑定：`:code` / `:name` / `:id` 三处占位。 */
function withSampleParams(path: string): string {
  return path.replace(/:code|:name|:id/g, 'sample')
}

/** 不渲染页面的路径：404 兜底与静态入口。 */
const NON_PAGE: readonly string[] = ['*', '/index.html']

/** 全部已注册、需要一次绑定决策的页面路由（含重定向旧地址与由页面链入的带参深链）。 */
const REGISTERED_PATHS: string[] = ROUTE_PATHS.filter(
  (path) => !NON_PAGE.includes(path),
).map(withSampleParams)

describe('页面 ↔ skill 绑定覆盖率（QUOT-474 / DEC-065）', () => {
  it('覆盖集合来自路由注册表，且侧栏每一项都在其中', () => {
    const registered = new Set(REGISTERED_PATHS)
    for (const path of NAV_PATHS) {
      expect(registered.has(path), `侧栏 ${path} 未在 routes.ts 注册`).toBe(true)
    }
    // 深链没有侧栏入口，但仍是页面，必须进覆盖集合
    expect(registered.has('/research/sample')).toBe(true)
    expect(registered.has('/research/sample/report')).toBe(true)
    expect(registered.has('/industry/sample')).toBe(true)
    expect(registered.has('/skills/sample')).toBe(true)
    expect(registered.has('/skills/pkg/sample')).toBe(true)
  })

  it('绑定表里出现的包名都在 `index.json` 里真实存在', () => {
    for (const path of REGISTERED_PATHS) {
      const binding = resolveBinding(path)
      if (binding) {
        expect(binding.skill.name, `${path} 绑定了不存在的包`).toBeTruthy()
      }
    }
  })

  it.each(REGISTERED_PATHS)(
    '`%s` 要么绑定 skill，要么显式声明 `null`，不留漏绑',
    (path) => {
      const decision = bindingDecision(path)
      expect(
        decision.kind,
        `${path} 未绑定任何 skill，也未在 EXPLICIT_NULL_PREFIXES 里声明`,
      ).not.toBe('unbound')
    },
  )

  it('显式 `null` 只有首页、`/skills`、`/settings` 三处（其余页面必须绑包）', () => {
    expect(bindingDecision('/').kind).toBe('explicit-null')
    expect(EXPLICIT_NULL_PREFIXES).toEqual(['/', '/skills', '/settings'])
  })

  it('`/research/:code` 深链绑定 `a-share-dossier`（旧用法回归）', () => {
    const binding = resolveBinding('/research/600519')
    expect(binding?.skill.name).toBe('a-share-dossier')
    expect(binding?.pageLabel).toBe('个股深挖')
  })

  it('`/skills` 与 `/skills/:id` 显式声明不绑包（上下文由页内按落点决定）', () => {
    // 需求：475 的绑定表对 `/skills` 给 `pkg: null`，由页内决定上下文（步骤级「问 AI」）。
    // 因此这两条是 `explicit-null`，不是漏绑。
    expect(bindingDecision('/skills').kind).toBe('explicit-null')
    expect(bindingDecision('/skills/2').kind).toBe('explicit-null')
    expect(bindingDecision('/skills/pkg/a-share-dossier').kind).toBe('explicit-null')
  })
})
