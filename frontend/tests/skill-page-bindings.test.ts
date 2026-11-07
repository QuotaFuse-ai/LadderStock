import { describe, expect, it } from 'vitest'

import { NAV_SECTIONS, PINNED_NAV } from '../src/components/Layout'
import { ROUTE_PATHS } from '../src/routes'
import { bindingFor } from '../src/lib/skills/pageBindings'
import { getPackage } from '../src/lib/skills'

/**
 * 绑定表覆盖率（QUOT-474 / DEC-067，接在 QUOT-475 的 `pageBindings` 上）。
 *
 * `page-bindings.test.ts` 逐条扫前缀；这里补的是**方向相反**的一条：
 * 从 `routes.ts` 的注册表反推，断言每条已注册页面路由都在绑定表里被声明过
 * （`pkg` 为真实包名，或显式 `null` 的开放域）。加了页面忘了登记绑定就红灯，
 * 而不是运行期静默退回开放域。
 *
 * 只排除本来就不渲染页面的路径：404 兜底 `*`、静态入口 `/index.html`，
 * 以及只做重定向的旧地址 `/watchlist`、`/positions`。
 */

/** 侧栏项里的 `?tab=` 只用于高亮，覆盖检查只看 path。 */
function pathOf(to: string): string {
  return to.split('?')[0]
}

const NAV_PATHS = [
  ...NAV_SECTIONS.flatMap((section) => section.pages.map((page) => page.to)),
  ...PINNED_NAV.map((page) => page.to),
].map(pathOf)

/** 不渲染页面的路径：404 兜底、静态入口，以及只做重定向的旧地址。 */
const NON_PAGE: readonly string[] = [
  '*',
  '/index.html',
  // QUOT-431 旧地址：`<Navigate>` 到 `/my-stocks?tab=watch|hold`，本身不是页面
  '/watchlist',
  '/positions',
]

/** 带参路由换成具体值再判绑定：`:code` / `:name` / `:id` 三处占位。 */
function withSampleParams(path: string): string {
  return path.replace(/:code|:name|:id/g, 'sample')
}

const REGISTERED_PATHS: string[] = ROUTE_PATHS.filter(
  (path) => !NON_PAGE.includes(path),
).map(withSampleParams)

describe('页面 ↔ skill 绑定覆盖率（QUOT-474 / DEC-067）', () => {
  it('覆盖集合来自路由注册表，且侧栏每一项都在其中', () => {
    const registered = new Set(REGISTERED_PATHS)
    for (const path of NAV_PATHS) {
      expect(registered.has(path), `侧栏 ${path} 未在 routes.ts 注册`).toBe(true)
    }
    // 深链与旧地址没有侧栏入口，但仍是页面，必须进覆盖集合
    expect(registered.has('/research/sample')).toBe(true)
    expect(registered.has('/research/sample/report')).toBe(true)
    expect(registered.has('/industry/sample')).toBe(true)
    expect(registered.has('/skills/sample')).toBe(true)
    expect(registered.has('/skills/pkg/sample')).toBe(true)
    // 旧地址只做重定向，不进覆盖集合
    expect(ROUTE_PATHS).toContain('/watchlist')
    expect(registered.has('/watchlist')).toBe(false)
  })

  it.each(REGISTERED_PATHS)('`%s` 在绑定表里被声明过（绑定包或显式 null）', (path) => {
    expect(
      bindingFor(path),
      `${path} 未在 pageBindings.ts 里登记，运行期会静默退回开放域`,
    ).not.toBeNull()
  })

  it('绑定到的包名都在 `content/skills/index.json` 里真实存在', () => {
    for (const path of REGISTERED_PATHS) {
      const pkg = bindingFor(path)?.pkg
      if (pkg) expect(getPackage(pkg), `${path} 绑定了不存在的包 ${pkg}`).not.toBeNull()
    }
  })

  it('`/skills`、`/settings`、`/` 是显式开放域（pkg = null）', () => {
    expect(bindingFor('/')?.pkg).toBeNull()
    expect(bindingFor('/skills')?.pkg).toBeNull()
    expect(bindingFor('/skills/2')?.pkg).toBeNull()
    expect(bindingFor('/skills/pkg/a-share-dossier')?.pkg).toBeNull()
    expect(bindingFor('/settings')?.pkg).toBeNull()
  })

  it('`/research/:code` 深链仍绑 `a-share-dossier`（旧用法回归）', () => {
    expect(bindingFor('/research/600519')?.pkg).toBe('a-share-dossier')
  })
})
