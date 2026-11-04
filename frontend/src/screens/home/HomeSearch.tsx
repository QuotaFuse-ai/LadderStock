import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Telescope } from 'lucide-react'

import { Card, CardBody } from '../../components/ui'
import { HomeSearchTrigger } from '../../components/search/HomeSearchTrigger'
import { GUIDE_ROWS, SearchPanel } from '../../components/search/SearchPanel'
import { useHomeSearch } from './useHomeSearch'

/**
 * 首页 B 块「今天，想研究什么？」——既有聚合搜索的研究入口卡（QUOT-357 规格 §4–§5，
 * 版式口径见 SPEC-HOME-V2 §3）。
 *
 * **本块只做版式重排，不改数据面**：`useHomeSearch`（状态机）/ `SearchPanel`（呈现件）/
 * `lib/searchCore.ts` `searchIntent.ts` `searchRoutes.ts` 的入参与返回形状零改动。
 * 改动面只有两处：① `HomeSearchTrigger` 从 `PageHeader.actions` 移进本卡并满栏；
 * ② 三行映射复用 `SearchPanel` 导出的 `GUIDE_ROWS`，不复制字符串。
 *
 * 结果面板继续是既有的模态 overlay（`createPortal` + `useModalChrome`）：焦点陷阱 / Esc /
 * 滚动锁 / 关闭后焦点回入口都只有一份实现（§3.1，改内联等于把这套契约做两遍）。
 *
 * 文案红线（§3.2）：卡内不出现「AI」「接入」「联网搜索」字样或按钮——仓内没有问答通道，
 * 承诺「问」就是骗人的。副标题落在真实能力上：查数据、找证据、梳理思路。
 *
 * `?search=<关键词>` 是唯一的深链入口，承担两件事：
 * 1. 带着词进来就能看到结果（消费一次即从 URL 撤掉，刷新后面板不会自己弹回来）；
 * 2. 视觉巡检拍「展开 / 有结果 / 无结果」三态——`scripts/visual-shot.mjs` 的 `--query search=…`
 *    直接复用，不必为截图改脚本或加调试开关。
 */

/** 面板元素 id：入口的 `aria-controls` 与面板的 `id` 必须是同一个字符串常量。 */
const PANEL_ID = 'ldst-home-search-panel'

/** §3.1：卡内最近搜索最多 4 枚（面板里给全部，卡只留最常点的那几条）。 */
const RECENT_SLOTS = 4

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
    <Card>
      <CardBody className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Telescope className="size-4 shrink-0 text-accent" aria-hidden="true" />
          <h2 className="text-subtitle font-semibold text-text">今天，想研究什么？</h2>
        </div>
        <p className="text-caption text-text-secondary">查数据、找证据、梳理思路</p>

        <HomeSearchTrigger expanded={open} panelId={PANEL_ID} onOpen={openPanel} />

        <ul className="flex flex-col gap-1">
          {GUIDE_ROWS.map((row) => (
            <li key={row.how} className="text-caption text-text-secondary">
              <span className="text-text">{row.how}</span> → <span className="ldst-num">{row.to}</span>
            </li>
          ))}
        </ul>

        {/* 没有最近搜索时整行不渲染，不留一条空的胶囊轨道（§3.3） */}
        {model.recent.length > 0 && (
          <div className="flex flex-col gap-1">
            <span className="text-caption text-text-muted">最近搜索</span>
            <span className="flex flex-wrap gap-1">
              {model.recent.slice(0, RECENT_SLOTS).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => {
                    // 与面板内 chip 走同一条链路：填词 → 门限 + 节流 → 一轮取数（§3.1）
                    actions.onRecent(item)
                    openPanel()
                  }}
                  className="inline-flex h-5 max-w-full items-center truncate rounded-xs border border-border px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3"
                >
                  {item}
                </button>
              ))}
            </span>
          </div>
        )}
      </CardBody>
      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </Card>
  )
}
