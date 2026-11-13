import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { History, Sparkles, Wrench } from 'lucide-react'

import { Card, CardHeader, EmptyState, PrivacyBadge, Tabs, Textarea } from '../../components/ui'
import { SearchPanel } from '../../components/search/SearchPanel'
import { cn } from '../../lib/cn'
import { HomeResearchChat } from './HomeResearchChat'
import { HomeResearchShortcuts } from './HomeResearchShortcuts'
import { routeHomeQuery } from './homeQueryRoute'
import { useHomeSearch } from './useHomeSearch'
import type { HomeAssistantBundle } from './homeAssistant'

/**
 * 首页「研究」面板（QUOT-552：三个搜索面合并成一个）。
 *
 * 版式：卡头（标题 + 隐私徽标）→ 三页签（快捷入口 / 研究股票 / 搜索历史）→
 * **底部唯一输入框**。回车自动路由（`homeQueryRoute.ts`，意图判定复用
 * `lib/searchIntent`，不写第二份）：
 * - 问句 → 「研究股票」页签的 AI 对话（`incoming` 按 seq 消费一次）；
 * - 6 位代码 → `actions.onExit(researchHref(code))`：记历史 + 跳 `/research/:code`；
 * - 名称 / 关键词 → 打开既有聚合搜索面板（词已在控制器里，门限 + 节流原样走）。
 *
 * 能力零回退：`useHomeSearch` / `SearchPanel` / `lib/searchCore.ts` / `searchIntent.ts` /
 * `searchRoutes.ts` 入参与返回形状零改动；结果面板仍是那个模态 overlay
 * （`createPortal` + `useModalChrome`），不内联第二套。底部输入框直接绑
 * `model.query` / `actions.onQueryChange`——面板关着时控制器的取数 effect 不跑，
 * 所以在卡里打字不会白发请求；`?search=` 仍是唯一搜索深链入口（消费一次即撤）。
 *
 * 三个页签的内容**常驻挂载**（`hidden` 切换）：对话的 turns / 在途请求不因切页签丢失。
 * 快捷入口仍是 catalog 按组随机 4 条 + 换一批；搜索历史仍复用 `model.recent`。
 */

/** 面板元素 id：入口的 `aria-controls` 与面板的 `id` 必须是同一个字符串常量。 */
const PANEL_ID = 'ldst-home-search-panel'

type HomeTab = 'shortcuts' | 'chat' | 'history'

const TABS: Array<{ value: HomeTab; label: string; icon: ReactNode }> = [
  { value: 'shortcuts', label: '快捷入口', icon: <Wrench className="size-3.5" aria-hidden="true" /> },
  { value: 'chat', label: '研究股票', icon: <Sparkles className="size-3.5" aria-hidden="true" /> },
  { value: 'history', label: '搜索历史', icon: <History className="size-3.5" aria-hidden="true" /> },
]

/** 研究卡隐私口径（QUOT-472 上下文白名单的通俗版）：hover 与弹层共用这一份字符串。 */
const RESEARCH_PRIVACY =
  '私有数据不出本机：持仓的成本、数量、盈亏一律不进 AI 上下文。对话只携带首页已展示的公开行情（指数 / 自选 / 盯盘 / 验证条件计数），不写 URL、日志或本机存储。回答只是解读，不构成投资建议。'

export interface HomeSearchProps {
  /** 首页四块已取到的数据：对话上下文与「本轮来源」都从这一份派生 */
  assistant: HomeAssistantBundle
  date: string
  /**
   * 从对话区「问研究台」带过来的一问（QUOT-582 / F4）：非 `null` 即切到「研究股票」页签、
   * 填进底部唯一输入框并聚焦；只填不发，与起步问题同一口径。读一次即回调 `onQuestionConsumed`。
   */
  incomingQuestion?: string | null
  onQuestionConsumed?: () => void
  className?: string
}

export function HomeSearch({ assistant, date, incomingQuestion = null, onQuestionConsumed, className }: HomeSearchProps) {
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

  const { model, actions, openPanel } = useHomeSearch({
    initialQuery,
    onNavigate: (href) => navigate(href),
  })

  const [tab, setTab] = useState<HomeTab>('shortcuts')
  const [chatPending, setChatPending] = useState(false)
  const [incoming, setIncoming] = useState<{ text: string; seq: number } | null>(null)
  const incomingSeq = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const focusInput = () => inputRef.current?.focus()

  // 对话区「问研究台」带来的一问：切页签 + 填词 + 聚焦，不自动发（同起步问题的口径）
  const lastIncomingQuestion = useRef<string | null>(null)
  useEffect(() => {
    if (!incomingQuestion) {
      // 消费后外壳会把 prop 清回 null：守卫跟着清零，同一条问下次仍能重新预填
      lastIncomingQuestion.current = null
      return
    }
    if (incomingQuestion === lastIncomingQuestion.current) return
    lastIncomingQuestion.current = incomingQuestion
    setTab('chat')
    actions.onQueryChange(incomingQuestion)
    focusInput()
    onQuestionConsumed?.()
  }, [incomingQuestion, actions, onQuestionConsumed])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法候选期间的回车归输入法，不能当成路由；Shift+Enter 换行不提交
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    const route = routeHomeQuery(model.query)
    if (route.kind === 'empty') return
    if (route.kind === 'assistant') {
      setTab('chat')
      // 回答中不叠轮：切过去看进度，文本留在输入框，停完再发
      if (chatPending) return
      incomingSeq.current += 1
      setIncoming({ text: route.question, seq: incomingSeq.current })
      actions.onClear()
      return
    }
    if (route.kind === 'research') {
      // 与面板内选中代码行走同一条链路：记历史（用户输入的词）→ 关面板 → 跳转
      actions.onExit(route.href)
      return
    }
    openPanel()
  }

  return (
    <Card className={cn('flex flex-col', className)}>
      <CardHeader
        title="今天，想研究什么？"
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <PrivacyBadge detail={<p>{RESEARCH_PRIVACY}</p>} detailText={RESEARCH_PRIVACY} />
            <span>问句 / 代码 / 名称，一个入口</span>
          </span>
        }
      />

      <Tabs
        tabs={TABS.map((item) =>
          item.value === 'chat' && chatPending
            ? { ...item, badge: <span className="text-caption text-accent">回答中</span> }
            : item,
        )}
        value={tab}
        onChange={setTab}
        ariaLabel="研究面板页签"
        className="px-2"
        dense
      />

      {/* 固定高度：切页签 / 对话增长都不顶高卡片（一页无滚动的预算件）；行内被拉高时跟着长 */}
      <div className="grow" style={{ height: 160 }}>
        <div hidden={tab !== 'shortcuts'} className="h-full overflow-y-auto px-4 py-3">
          <HomeResearchShortcuts embedded />
        </div>
        <div hidden={tab !== 'chat'} className="h-full overflow-y-auto px-4 py-3">
          <HomeResearchChat
            embedded
            assistant={assistant}
            date={date}
            incoming={incoming}
            onPendingChange={setChatPending}
            onPickSuggestion={(text) => {
              // 起步问题只填不发（QUOT-551 口径不变），填进面板底部唯一输入框
              actions.onQueryChange(text)
              focusInput()
            }}
            onFocusInput={focusInput}
          />
        </div>
        <div hidden={tab !== 'history'} className="h-full overflow-y-auto px-4 py-3">
          {model.recent.length === 0 ? (
            <EmptyState
              compact
              title="还没有搜索历史"
              description="搜过的代码 / 名称 / 关键词会记在这里，只存在本机。"
            />
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-caption text-text-muted">点一条重新搜（最多记 8 条，只存本机）：</p>
              <span className="flex flex-wrap gap-1">
                {model.recent.map((item) => (
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
      </div>

      <div className="flex flex-col gap-1 border-t border-border px-4 py-2">
        <Textarea
          ref={inputRef}
          rows={4}
          value={model.query}
          onChange={(event) => actions.onQueryChange(event.target.value)}
          onKeyDown={onKeyDown}
          onCompositionStart={() => actions.onComposing(true)}
          onCompositionEnd={() => actions.onComposing(false)}
          aria-label="搜索或提问"
          placeholder="问一句，或输入代码 / 名称 · 回车直达，Shift+Enter 换行"
          className="min-h-[104px] resize-none"
          autoComplete="off"
        />
        <p className="truncate text-11px text-text-muted">
          {chatPending
            ? '回答中…到「研究股票」页签可停止，本轮答完再发'
            : '问句 → AI · 代码 → 行情 · 名称 / 关键词 → 搜索 · Shift+Enter 换行'}
        </p>
      </div>

      <SearchPanel panelId={PANEL_ID} model={model} actions={actions} />
    </Card>
  )
}