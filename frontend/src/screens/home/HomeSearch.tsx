import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { HomeSearchTrigger } from '../../components/search/HomeSearchTrigger'
import { SearchPanel } from '../../components/search/SearchPanel'
import { useHomeSearch } from './useHomeSearch'

/**
 * 首页搜索入口的装配件（QUOT-357 / 规格 §4–§5）。
 *
 * `HomeScreen.tsx` 只写一个 `<HomeSearch />`：入口 / 面板 / 状态机分别在这里凑齐，
 * 屏体不掺任何搜索逻辑（触发条硬约束「不要把面板逻辑写进 HomeScreen 主体」）。
 *
 * `?search=<关键词>` 是唯一的深链入口，承担两件事：
 * 1. 用户/分享带着词进来就能看到结果（消费一次即从 URL 撤掉，刷新后面板不会自己弹回来）；
 * 2. 视觉巡检拍「展开 / 有结果 / 无结果」三态——`scripts/visual-shot.mjs` 的 `--query search=…`
 *    直接复用，不必为截图改脚本或加调试开关。
 */

/** 面板元素 id：入口的 `aria-controls` 与面板的 `id` 必须是同一个字符串常量。 */
const PANEL_ID = 'ldst-home-search-panel'

export function HomeSearch() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const initialQuery = params.get('search')
  // `?search=`（值为空）也是有效深链：拍「面板打开 = 空态」用的正是这一种
  const hasDeepLink = params.has('search')

  // 只消费一次：撤参数之后 `initialQuery` 变 `null`，面板里的关键词不受影响
  const consumedRef = useRef(false)

  useEffect(() => {
    if (!hasDeepLink || consumedRef.current) return
    consumedRef.current = true
    const next = new URLSearchParams(params)
    next.delete('search')
    setParams(next, { replace: true })
  }, [hasDeepLink, params, setParams])

  const { open, model, actions, openPanel } = useHomeSearch({
    initialQuery,
    onNavigate: (href) => navigate(href),
  })

  return (
    <>
      <HomeSearchTrigger expanded={open} panelId={PANEL_ID} onOpen={openPanel} />
      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </>
  )
}
