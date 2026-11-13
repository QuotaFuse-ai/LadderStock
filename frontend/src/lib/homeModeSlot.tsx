import { createContext } from 'react'

/**
 * 顶栏模式切换槽（QUOT-582 / 任务书 §5.6）。
 *
 * 与 `PageToolsSlotContext` 同构：外壳把 `header.workspace-topbar` 中间的一个真实空节点
 * 放进 context，首页 `HomeShell` 用 portal 把「对话 / 研究台」切换送进去。
 * 只有首页挂 `HomeShell`，其余 15 屏读到 `null`，顶栏中间因此不留任何空白。
 * 单独成模块而不是放进 `Layout.tsx`，避免 `Layout ↔ 页` 反向依赖。
 */
export const HomeModeSlotContext = createContext<HTMLElement | null>(null)
