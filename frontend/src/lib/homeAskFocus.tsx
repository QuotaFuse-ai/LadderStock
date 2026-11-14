import { createContext } from 'react'

/**
 * 首页 ask 聚焦信号（QUOT-590）。
 *
 * 顶栏「问 AI」在 `/` 不再开抽屉（home 已有 chat composer 与 desk 研究面两颗 ask 面，
 * 抽屉会是同一颗 `HomeResearchChat` 的第二实例），改为广播「聚焦既有输入框」的信号：
 * `seq` 每请求一次自增，`HomeShell` 读到变化后按当前模式聚焦（desk → 研究页签 +
 * 底部输入框；chat → composer）。只聚焦、不自动发送。
 *
 * 与 `homeModeSlot` / `pageToolsSlot` 同构：信号挂在 `Layout`，单独成模块避免
 * `Layout ↔ HomeShell` 反向依赖。非 home 页读到 `null`，抽屉行为不变。
 */
export interface HomeAskFocusSignal {
  seq: number
}

export const HomeAskFocusContext = createContext<HomeAskFocusSignal | null>(null)
