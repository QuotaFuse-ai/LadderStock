import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'

import { Card, CardBody } from '../../components/ui'
import { HomeSearchTrigger } from '../../components/search/HomeSearchTrigger'
import { GUIDE_ROWS, SearchPanel } from '../../components/search/SearchPanel'
import { HomePathLabel } from './HomePathLabel'
import { HomeResearchChat } from './HomeResearchChat'
import { HomeResearchShortcuts } from './HomeResearchShortcuts'
import { useHomeSearch } from './useHomeSearch'
import type { HomeAssistantBundle } from './homeAssistant'

/**
 * 首页 B 块「今天，想研究什么？」（QUOT-472 起为 AI 研究对话入口）。
 *
 * QUOT-551 起三条研究路径用统一序号标签（`HomePathLabel`）摆明主次：
 * ① 问 AI（自由提问，主体）／② 开现成工具（点一下直达，为辅）／③ 直接搜（不经过 AI，再次）。
 * 起步问题改为问句 pill（暗示「填入输入框」），与工具卡（带箭头直达）不再同款；
 * 只改呈现 / 文案 / 视觉区分，三条路径的行为口径零回退。
 *
 * 上半是对话：起步问题 / 多行输入 / Enter 发送 / 停止 / 清空 / 「本轮来源」，
 * 通道路由与降级复用 QUOT-454 的 `POST /api/research/{scope}/assistant` 与
 * `describeAssistantError`（见 `HomeResearchChat.tsx`）。
 *
 * 对话下方是「研究工具快捷入口」（QUOT-531）：从 `content/skills/catalog.json`
 * 按组随机抽 4 条可点条目，点一下直达 `/skills/:id` 或已承载页面；与起步问题并存。
 *
 * 下半保留**原有聚合搜索**，一个能力都没少：`useHomeSearch`（状态机）/
 * `SearchPanel`（呈现件）/ `lib/searchCore.ts` `searchIntent.ts` `searchRoutes.ts`
 * 的入参与返回形状零改动，只是入口从「卡片主体」降为「对话下方的直接搜」。
 *
 * 结果面板继续是既有的模态 overlay（`createPortal` + `useModalChrome`）：焦点陷阱 / Esc /
 * 滚动锁 / 关闭后焦点回入口都只有一份实现，不内联第二套。
 *
 * `?search=<关键词>` 仍是唯一的搜索深链入口，承担两件事：
 * 1. 带着词进来就能看到结果（消费一次即从 URL 撤掉，刷新后面板不会自己弹回来）；
 * 2. 视觉巡检拍「展开 / 有结果 / 无结果」三态——`scripts/visual-shot.mjs` 的 `--query search=…`
 *    直接复用，不必为截图改脚本或加调试开关。
 */

/** 面板元素 id：入口的 `aria-controls` 与面板的 `id` 必须是同一个字符串常量。 */
const PANEL_ID = 'ldst-home-search-panel'

/** 卡内最近搜索最多 4 枚（面板里给全部，卡只留最常点的那几条）。 */
const RECENT_SLOTS = 4

export interface HomeSearchProps {
  /** 首页四块已取到的数据：对话上下文与「本轮来源」都从这一份派生 */
  assistant: HomeAssistantBundle
  date: string
}

export function HomeSearch({ assistant, date }: HomeSearchProps) {
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

  const { open, model, actions, openPanel, openPanelFromPointer } = useHomeSearch({
    initialQuery,
    onNavigate: (href) => navigate(href),
  })

  return (
    <Card>
      <CardBody className="flex flex-col gap-4">
        <HomeResearchChat assistant={assistant} date={date} />

        <HomeResearchShortcuts />

        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <div className="flex items-center gap-2">
            <Search className="size-3.5 shrink-0 text-text-muted" aria-hidden="true" />
            <HomePathLabel step="3" tone="tertiary">
              直接搜 · 不经过 AI
            </HomePathLabel>
          </div>

          <HomeSearchTrigger
            expanded={open}
            panelId={PANEL_ID}
            onOpen={openPanel}
            onPointerOpen={openPanelFromPointer}
          />

          <ul className="flex flex-col gap-1">
            {GUIDE_ROWS.map((row) => (
              <li key={row.how} className="text-caption text-text-secondary">
                <span className="text-text">{row.how}</span> → <span className="ldst-num">{row.to}</span>
              </li>
            ))}
          </ul>

          {/* 没有最近搜索时整行不渲染，不留一条空的胶囊轨道（SPEC-HOME-V2 §3.3） */}
          {model.recent.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-caption text-text-muted">最近搜索</span>
              <span className="flex flex-wrap gap-1">
                {model.recent.slice(0, RECENT_SLOTS).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => {
                      // 与面板内 chip 走同一条链路：填词 → 门限 + 节流 → 一轮取数
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
        </div>
      </CardBody>
      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </Card>
  )
}
