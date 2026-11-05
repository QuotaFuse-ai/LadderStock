import { createContext } from 'react'

/**
 * 顶栏工具槽（QUOT-457 / DEC-063）。
 *
 * 外壳把 `header.workspace-topbar` 右侧的某个真实节点放进 context，
 * 页面骨架件 `PageHeader` 用 portal 把「刷新」送进去。
 * 单独放在这里而不是 `PageShell.tsx`，是为了避免 `Layout ↔ PageShell` 已有依赖再反向引一次。
 */
export const PageToolsSlotContext = createContext<HTMLElement | null>(null)
