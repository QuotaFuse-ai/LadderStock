import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { PanelRight } from 'lucide-react'

import { httpConversation, type SessionSummary } from '../../../api/conversation'
import { Drawer, IconButton, Segmented, type SegmentedOption } from '../../../components/ui'
import { cn } from '../../../lib/cn'
import { useHomeMode, type HomeMode } from '../../../lib/homeMode'
import { HomeModeSlotContext } from '../../../lib/homeModeSlot'
import { HomeAskFocusContext } from '../../../lib/homeAskFocus'
import { useIsMobile } from '../../../lib/mediaQuery'
import { researchHref } from '../../../lib/searchRoutes'
import { useAsync } from '../../../lib/useAsync'
import HomeScreen from '../../HomeScreen'
import {
  HomeTodoMoversContext,
  HomeTodoMoversGlobalBar,
  HomeTodoMoversShellMountedContext,
  type HomeTodoMoversData,
} from '../HomeTodoMoversBar'
import { ChatPane } from './ChatPane'
import { SessionList } from './SessionList'

/**
 * 首页双模式外壳（QUOT-582）。
 *
 * - 「对话」= 本单新增的对话主区（左侧会话列表 + 右侧聊天流，窄屏会话列表进抽屉）；
 * - 「研究台」= 既有 `HomeScreen` 原样嵌入（DEC-084 D6）。研究台不依赖 AI 通道也能用，
 *   所以它是「看数 / 核对」的落点，对话区是给人用大白话提问的前门。
 *
 * 模式只落 `/`：切到 `/research/:code` 时读回的是研究台（F4/F5 的同一份 `localStorage['home_mode']`）。
 * 顶栏切换在 ≥1024px 通过 `HomeModeSlotContext` portal 送进唯一那条 `h-topbar`；
 * 槽位缺失（独立渲染、旧外壳）或窄屏时退回页内切换器，不抛错、不多出第二条横栏。
 */

const MODE_OPTIONS: ReadonlyArray<SegmentedOption<HomeMode>> = [
  { value: 'chat', label: '对话' },
  { value: 'desk', label: '研究台' },
]

export default function HomeShell() {
  const [mode, setMode] = useHomeMode()
  const slot = useContext(HomeModeSlotContext)
  const isMobile = useIsMobile()
  const navigate = useNavigate()

  const [sessionsDirty, setSessionsDirty] = useState(0)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  // 空态两段式（QUOT-587）：冷启动只有居中输入框；首问发出（或选中一条会话）后列表才出现
  const [chatStarted, setChatStarted] = useState(false)
  // 研究台要接着用的那一问：切过去时填进研究面板输入框，`HomeSearch` 取走即回写 `null`（F4）
  const [deskQuestion, setDeskQuestion] = useState<string | null>(null)
  // QUOT-588：待办异动数据由 `HomeScreen` 回调上行（context 只向下流动），供给全局入口
  const [todoMoversData, setTodoMoversData] = useState<HomeTodoMoversData | null>(null)
  // 「问 AI」聚焦请求（QUOT-590）：顶栏在 home 不开抽屉，只广播聚焦既有输入框。
  // 两个计数分别驱动 desk（切研究页签 + 聚焦）与 chat（聚焦 composer），只聚焦不自动发送。
  const askFocus = useContext(HomeAskFocusContext)
  const [deskFocusRequest, setDeskFocusRequest] = useState(0)
  const [chatFocusRequest, setChatFocusRequest] = useState(0)
  const consumedAskFocus = useRef(0)

  useEffect(() => {
    const seq = askFocus?.seq ?? 0
    if (seq <= consumedAskFocus.current) return
    consumedAskFocus.current = seq
    if (mode === 'desk') setDeskFocusRequest((request) => request + 1)
    else setChatFocusRequest((request) => request + 1)
  }, [askFocus, mode])

  const sessions = useAsync<SessionSummary[]>(
    () => httpConversation.listSessions(),
    [sessionsDirty],
  )

  // 对话区发过一轮：列表里的标题 / 轮数变了，回写一次让列表重取
  const refreshSessions = useCallback(() => setSessionsDirty((count) => count + 1), [])
  const selectSession = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId)
    setChatStarted(true)
    setDrawerOpen(false)
  }, [])
  const newSession = useCallback(() => {
    setActiveSessionId(null)
    setChatStarted(false)
    setDrawerOpen(false)
  }, [])
  const startChat = useCallback(() => setChatStarted(true), [])
  // QUOT-588：首问落地，服务端建了新会话——activeSessionId 落到新 id，第二问续同一会话
  const onSessionCreated = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId)
    setChatStarted(true)
  }, [])

  const openResearch = useCallback(
    (code: string) => {
      setMode('desk')
      navigate(researchHref(code))
    },
    [navigate, setMode],
  )

  // 对话里的这一问带到研究台：让研究面板取走（切页签 + 预填 + 聚焦，不自动发）
  const askDesk = useCallback(
    (question: string) => {
      setDeskQuestion(question)
      setMode('desk')
    },
    [setMode],
  )

  const consumeDeskQuestion = useCallback(() => setDeskQuestion(null), [])

  // `<1024px` 顶栏只放得下一条「首页 / 研究台」标题，切换器进页内（F6）
  const desktopSegment = slot !== null && !isMobile
  const switcher = (
    <Segmented options={MODE_OPTIONS} value={mode} onChange={setMode} ariaLabel="首页模式" size="sm" />
  )

  const listProps = {
    sessions: sessions.data ?? [],
    status: sessions.status,
    activeId: activeSessionId,
    onSelect: selectSession,
    onNew: newSession,
  }

  return (
    <HomeTodoMoversShellMountedContext.Provider value>
      <HomeTodoMoversContext.Provider value={todoMoversData}>
      <div className="flex min-h-0 flex-col gap-3 desk:h-full">
        {!desktopSegment && (
          <div className="flex items-center justify-between gap-2">
            {switcher}
            {isMobile && (
              <IconButton
                label="打开对话列表"
                icon={<PanelRight className="size-4" aria-hidden="true" />}
                onClick={() => setDrawerOpen(true)}
              />
            )}
          </div>
        )}

        {desktopSegment && slot && createPortal(switcher, slot)}

        {/* 待办异动入口（QUOT-588）：shell 层显式全局挂载——chat / desk 两模式都显示，
            且独立于下面两个 hidden 容器（不再依赖 portal 无视 hidden 的泄漏行为）。
            数据由内部 `HomeScreen` 的同一份 `useHomeBlocks` 经 `HomeTodoMoversContext` 供给，
            这里不二次取数 / 不重复轮询；无顶栏槽（单测）时退回内容区原卡。 */}
        <div hidden={mode !== 'chat' && mode !== 'desk'} className="shrink-0">
          <HomeTodoMoversGlobalBar />
        </div>

        {/* 两种模式常驻挂载、只切 `hidden`：对话里的在途请求与滚动位置不因切模式丢失 */}
        <div
          hidden={mode !== 'chat'}
          className={cn(
            'min-h-0 flex-1 flex-col gap-3 lg:flex-row',
            mode === 'chat' ? 'flex' : 'hidden',
          )}
        >
          {chatStarted && (
            <SessionList
              {...listProps}
              className="hidden lg:flex lg:w-sidebar lg:shrink-0 lg:border-r lg:border-border lg:pr-2"
            />
          )}
          <ChatPane
            activeSessionId={activeSessionId}
            onOpenResearch={openResearch}
            onAskDesk={askDesk}
            onRoundComplete={refreshSessions}
            onFirstRoundStarted={startChat}
            onSessionCreated={onSessionCreated}
            focusRequest={chatFocusRequest}
          />
        </div>

        <div hidden={mode !== 'desk'} className="min-h-0 flex-1">
          <HomeScreen
            initialQuestion={deskQuestion}
            onQuestionConsumed={consumeDeskQuestion}
            onTodoMoversData={setTodoMoversData}
            focusRequest={deskFocusRequest}
          />
        </div>

        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          title="对话"
          subtitle="选一条继续，或开新对话"
          fullScreenNarrow
        >
          <SessionList {...listProps} />
        </Drawer>
      </div>
      </HomeTodoMoversContext.Provider>
    </HomeTodoMoversShellMountedContext.Provider>
  )
}
