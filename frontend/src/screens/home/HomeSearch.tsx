import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Sparkles } from 'lucide-react'

import { HomeSearchTrigger } from '../../components/search/HomeSearchTrigger'
import { GUIDE_ROWS, SearchPanel } from '../../components/search/SearchPanel'
import { Card, CardBody } from '../../components/ui'
import { useHomeSearch } from './useHomeSearch'

/**
 * 块 B：今天，想研究什么？（SPEC-HOME-V2 §3 / QUOT-357）。
 *
 * 这里仍是「入口 / 面板 / 状态机」的唯一装配点，只换挂载位置与宽度：
 * - 入口从 `PageHeader.actions` 移进本卡，触发条改为满栏（`fullWidth`）；
 * - 三行映射复用 `SearchPanel` 导出的 `GUIDE_ROWS`，不复制第二份字符串；
 * - 文案只落在真实能力上（查数据 / 找证据 / 梳理思路），不出现「AI / 接入 / 联网」承诺。
 *
 * `?search=<关键词>` 是唯一深链入口：消费一次即从 URL 撤掉，刷新后面板不会自己弹回来。
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
      <Card>
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <p className="flex items-center gap-2 text-subtitle font-semibold text-text">
              <Sparkles className="size-4 text-accent" aria-hidden="true" />
              今天，想研究什么？
            </p>
            <p className="text-caption text-text-secondary">查数据、找证据、梳理思路</p>
          </div>

          <HomeSearchTrigger expanded={open} panelId={PANEL_ID} onOpen={openPanel} fullWidth />

          <div className="flex flex-col gap-1 text-caption text-text-muted">
            {GUIDE_ROWS.map((row) => (
              <p key={row.how}>
                <span className="text-text-secondary">{row.how}</span> → {row.to}
              </p>
            ))}
          </div>

          {model.recent.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-caption text-text-muted">
              <span>最近搜索</span>
              {model.recent.slice(0, 4).map((term) => (
                <button
                  key={term}
                  type="button"
                  onClick={() => {
                    openPanel()
                    actions.onQueryChange(term)
                  }}
                  className="inline-flex h-5 items-center rounded-xs border border-border px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:border-border-strong hover:text-text"
                >
                  {term}
                </button>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </>
  )
}