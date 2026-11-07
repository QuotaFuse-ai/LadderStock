import { createContext, useContext, useEffect } from 'react'

import type { ResearchOverview } from '../../api/research'
import type { AssistantObject, DockView } from '../skills/dock'

/**
 * 「问 AI」抽屉的宿主契约（QUOT-475）。
 *
 * 抽屉本身只挂载一次（`Layout` 的 `AssistantDockHost`），页面与顶栏都通过
 * 这个 context 请求打开它——不允许页面再渲染第二份抽屉。
 *
 * `AssistantObjectContext` 是反向的：页内知道当前标的（例如 `/research/:code` 的
 * 名称、研究日），把它登记给宿主，顶栏「问 AI」打开时就能带上完整对象。
 */

export interface AssistantOpenInput {
  /** 打开时所在的路由；缺省由宿主取当前 `location.pathname` */
  pageKey?: string
  object?: AssistantObject
  /** 显式指定绑定包；缺省按 `pageBindings` 推断 */
  boundSkill?: string | null
  initialDraft?: string
  offlineNotice?: string | null
  /** 页面已取到的概览，避免抽屉为上下文重复取一次 */
  overview?: ResearchOverview | null
}

export interface AssistantShell {
  open: (input?: AssistantOpenInput) => void
}

export interface AssistantDockState {
  view: DockView
  overview: ResearchOverview | null
}

export const AssistantShellContext = createContext<AssistantShell | null>(null)

export function useAssistantShell(): AssistantShell | null {
  return useContext(AssistantShellContext)
}

export const AssistantObjectContext = createContext<(object: AssistantObject | null) => void>(
  () => {},
)

/**
 * 页内登记当前对象。只在字段真的变化时重新登记，卸载或换标的时清空，
 * 避免顶栏拿着上一页的股票名去拼开场白。
 */
export function useRegisterAssistantObject(object: AssistantObject | null): void {
  const register = useContext(AssistantObjectContext)
  const code = object?.code ?? null
  const name = object?.name ?? null
  const sector = object?.sector ?? null
  const date = object?.date ?? null
  const tab = object?.tab ?? null

  useEffect(() => {
    const filled = code || name || sector || date || tab
    register(filled ? { code, name, sector, date, tab } : null)
    return () => register(null)
  }, [register, code, name, sector, date, tab])
}
