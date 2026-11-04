import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight, Search, X } from 'lucide-react'

import { cn } from '../../lib/cn'
import { MISSING } from '../../lib/format'
import type { SearchGroup, SearchGroupId, SearchHit } from '../../lib/searchCore'
import { Badge, Button, EmptyState, IconButton, Input, SkeletonRows, SourceBadges } from '../ui'
import type { SourceBadgeMeta } from '../ui'
import { useModalChrome } from '../ui/Overlay'
import { useEnter } from '../ui/useEnter'

/**
 * C-22 `SearchPanel`（规格 §5–§8）。
 *
 * 纯呈现件：数据与意图判定都来自 `screens/home/useHomeSearch`（那里接 `lib/searchCore`），
 * 本文件不发请求、不解析输入、不拼 URL。契约用 `model` / `actions` 两个袋子传，
 * 屏侧（`HomeSearch.tsx`）只做装配。
 *
 * 三条容易被「顺手改掉」的规格口径，改之前先读规格对应小节：
 * - 模态对话框：复用 `useModalChrome`（§5.4），焦点、Esc、滚动锁、关闭后回入口都在那里，
 *   **不允许在面板里再实现一份**；
 * - 居中模态（QUOT-398）：面板在视口水平居中、距顶 12vh，宽度 `min(86vw, 表宽上线)`，
 *   结果区吃掉剩余高度。这是 QUOT-357 v1.1 的「右上角 640px 浮层」被否掉后的 v1.2 口径；
 * - 类型标签一律 `Badge tone="flat"`（§7.4 第 1 条：`neutral` 的 `bg-flat-soft` 混合底在浅色
 *   主题下 12px 文字 4.45:1，不达 4.5）；
 * - 面板内不出现 `DemoStateBadge`、「演示」字样水印（§6.7 / QUOT-348 硬约束），
 *   组头徽标只用 `SourceBadges`，不自带第三份 label map。
 */

export type SearchPhase = 'idle' | 'short' | 'loading' | 'results' | 'none' | 'failed'

export interface SearchGroupView {
  group: SearchGroup
  /** 组头溯源徽标的入参（§6.7）：由取数轮次的事实组装，检索层没有 envelope */
  badge: SourceBadgeMeta
  /** 已按 §6.2 上限截断的行 */
  rows: SearchHit[]
  exit: { label: string; href: string } | null
  /** 个股命中多于面板上限：第一期只提示不分页（§14-B2） */
  overflowHint: boolean
  /** 该组还没回：出 3 行骨架，组头不出徽标（§8.4） */
  loading: boolean
}

export interface SearchPanelModel {
  open: boolean
  query: string
  phase: SearchPhase
  groupRows: SearchGroupView[]
  /** 门限没过、还没有任何一轮可展示时，三组的静态名（§8.4 骨架组头用） */
  pendingGroups: Array<{ id: SearchGroupId; label: string }>
  rowCount: number
  activeIndex: number
  /** §8.3：输入变了但新一轮没回，旧结果灰显而不是换骨架 */
  stale: boolean
  /** §8.4：超过 1.2s 未回，在底部提示条说明一句 */
  slow: boolean
  /** §8.3：输入不足时还差几个字符 */
  remaining: number
  recent: string[]
  /** §8.7：整体失败时取数据层给的第一条源错误结论，不自己编文案 */
  failureTitle: string | null
  /** 本轮还在飞：`aria-busy` 与骨架的判据 */
  pending: boolean
}

export interface SearchPanelActions {
  onQueryChange(value: string): void
  onMove(delta: number): void
  onRowActivate(index: number): void
  onSubmit(): void
  onSelectHit(hit: SearchHit): void
  onRecent(value: string): void
  onExit(href: string): void
  onClear(): void
  onRetry(): void
  onClose(): void
  onComposing(value: boolean): void
}

export interface SearchPanelProps {
  panelId: string
  model: SearchPanelModel
  actions: SearchPanelActions
}

/**
 * §7.2 第 3 条：option 的 id 必须稳定，`aria-activedescendant` 才指得准。
 */
const optionId = (panelId: string, index: number) => `${panelId}-opt-${index}`

/**
 * §8.2 空态的三条引导：与 §3.2 的四类输入映射同源，写在这里给第一次使用的人看。
 *
 * 首页 B 块（`screens/home/HomeSearch.tsx`）的三行映射**导出复用这一份**：
 * 同一个「输入 → 落到哪一屏」的说明如果在两处各写一遍字符串，改了搜索路由口径时
 * 一定只改到其中一处（SPEC-HOME-V2 §3.1 / §1-7「一份实现」）。
 */
export const GUIDE_ROWS: Array<{ how: string; to: string }> = [
  { how: '6 位代码或个股名称', to: '个股深挖（/research/:code）' },
  { how: '行业 / 题材完整名称', to: '行业详情（/industry/:name）' },
  { how: '任意关键词', to: '资讯雷达（/news?q=…）' },
]

/**
 * QUOT-398 居中模态的几何口径（内联 style，不走 class——`check:ui` 规则 3 禁方括号任意值）：
 * 宽 = min(920px, 视口 − 48px)，1440 下 920，窄屏两侧各留 24px；
 * 高 = 视口 − 顶 12vh − 底 15vh，900 高下 ≈ 657px；结果区吃掉输入区与提示条之外的全部高度，
 * 满命中不再被 420px 的固定档截断。
 */
const PANEL_STYLE: React.CSSProperties = {
  left: '50%',
  top: '12vh',
  transform: 'translateX(-50%)',
  width: 'min(920px, calc(100vw - 48px))',
  maxHeight: '73vh',
}

function OptionRow({
  hit,
  active,
  id,
  pos,
  set,
  onActivate,
  onSelect,
}: {
  hit: SearchHit
  active: boolean
  id: string
  pos: number
  set: number
  onActivate: () => void
  onSelect: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const scrollIntoView = ref.current?.scrollIntoView
  // §7.3：高亮行自己滚进视野，`block: 'nearest'` 不把整段结果推来推去。
  // 取函数引用再 `call` 而不是直接 `ref.current?.scrollIntoView(...)`：jsdom 没实现这个
  // 布局 API，直接调用会让每条碰到高亮行的用例抛 TypeError。滚动只在真浏览器里可观测，
  // 用例断言的是 DOM 契约（`aria-selected` / `aria-activedescendant`）。
  useEffect(() => {
    if (active) scrollIntoView?.call(ref.current, { block: 'nearest' })
  }, [active, scrollIntoView])

  const disabled = hit.route === ''
  const meta = hit.detail ?? MISSING

  return (
    <div
      ref={ref}
      id={id}
      role="option"
      aria-selected={active}
      aria-posinset={pos}
      aria-setsize={set}
      tabIndex={-1}
      // §6.6：鼠标移到哪行高亮就跟到哪行，避免 Enter 打开的是另一条
      onMouseEnter={onActivate}
      onMouseDown={(event) => {
        event.preventDefault()
        if (!disabled) onSelect()
      }}
      className={cn(
        'flex min-h-row cursor-pointer items-center gap-2 rounded border-l-2 px-2 transition-colors duration-fast ease-ldst',
        active ? 'border-accent bg-surface-3' : 'border-transparent hover:bg-surface-3',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      {hit.group === 'news' ? (
        // 资讯行两行结构（§6.5：47px 实测），主行标题 + 元信息行
        <span className="flex min-w-0 flex-1 flex-col items-start justify-center py-1">
          <span className="w-full truncate text-body leading-body text-text" title={hit.name ?? MISSING}>
            {hit.name ?? MISSING}
          </span>
          <span className="w-full truncate text-caption text-text-muted" title={meta}>
            {meta}
          </span>
        </span>
      ) : (
        <>
          {/* 代码列只给个股（原型 §6.3：行业 / 题材行没有代码，占位破折号会被读成「数据缺失」） */}
          {hit.code !== null && (
            <span className="ldst-num w-16 shrink-0 text-body text-text-secondary">{hit.code}</span>
          )}
          <span className="min-w-0 flex-1 truncate text-body text-text" title={hit.name ?? MISSING}>
            {/* 名称为 null 是「没接入」不是「没有这只股票」，不许拿代码顶替（§6.4） */}
            {hit.name ?? '名称未接入'}
          </span>
          {hit.detail !== null && (
            <span className="min-w-0 shrink-0 truncate text-caption text-text-muted" title={hit.detail}>
              {hit.detail}
            </span>
          )}
        </>
      )}
      <span className="flex shrink-0 items-center gap-1">
        {/* 代码直达项：只由代码格式推出，名称与存在性未经数据源核实（QUOT-356 兜底口径） */}
        {!hit.verified && <Badge tone="flat">未核实</Badge>}
        <Badge tone="flat">{hit.tag}</Badge>
        {/* 原型行尾箭头：可跳才给，禁用行不许承诺跳转 */}
        {!disabled && <ChevronRight className="size-3.5 text-text-muted" aria-hidden="true" />}
      </span>
    </div>
  )
}

function GroupHeader({ label, count, badge, degraded }: { label: string; count: number; badge: SourceBadgeMeta | null; degraded: boolean }) {
  return (
    <div className="sticky top-0 z-sticky flex min-h-control items-center justify-between gap-2 rounded-xs bg-surface-2 px-2">
      <span className="min-w-0 truncate text-caption text-text-secondary">
        <span className="ldst-num">{label}</span>
        {count > 0 && <span className="ldst-num"> · {count}</span>}
        {degraded && <span className="ml-1 text-danger">未取到</span>}
      </span>
      {/* §8.4：加载中的组没有信封可标，组头右侧留空，不临时造「未知」标签 */}
      {badge && <SourceBadges envelope={badge} />}
    </div>
  )
}

/** §8.6：单组失败只降级这一组，另两组照常可跳。 */
function GroupDegrade({ group, onRetry }: { group: SearchGroup; onRetry: () => void }) {
  const detail = group.error?.detail ?? group.sources.find((item) => item.error)?.error?.detail ?? null
  return (
    <div
      role="status"
      title={detail ?? undefined}
      className="flex min-h-control items-center justify-between gap-2 rounded-xs border border-danger bg-danger-soft px-2 text-caption text-danger"
    >
      <span>该组暂不可用（结果没取到）</span>
      <span className="sr-only">{group.label}结果获取失败，其他结果仍可用</span>
      <Button variant="ghost" size="md" className="text-caption" onClick={onRetry}>
        重试
      </Button>
    </div>
  )
}

export function SearchPanel({ panelId, model, actions }: SearchPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [guidesOpen, setGuidesOpen] = useState(false)
  const listId = `${panelId}-list`

  useModalChrome(model.open, actions.onClose, panelRef)
  const entered = useEnter(model.open)

  /**
   * §7.2 第 1 条「打开时焦点必须落进输入框」单独钉一次。
   *
   * `useModalChrome` 的「聚焦首个可聚焦元素」在这里恰好也是输入框，但它筛元素用的是
   * `offsetParent !== null`（遮罩下 `display:none` 的兄弟件不算），这一条只在有布局的
   * 环境里成立——搜索面板的核心承诺就是「打开即可打字」，不能挂在一个布局测量上，
   * 所以这里按 ref 显式点一次。两条都是幂等的 `focus()`，先后顺序不影响结果。
   */
  useEffect(() => {
    if (model.open) inputRef.current?.focus()
  }, [model.open])

  const flatRows = useMemo(() => model.groupRows.flatMap((item) => item.rows), [model.groupRows])
  const activeRowId = model.activeIndex >= 0 && flatRows[model.activeIndex] ? optionId(panelId, model.activeIndex) : undefined

  const counts = useMemo(
    () => model.groupRows.map((item) => `${item.group.label} ${item.rows.length}`).join('、'),
    [model.groupRows],
  )

  /**
   * 每组在全局高亮序列里的起始下标：`↑↓` 与 `aria-posinset` 都按「三组拼成一维序列」算
   * （§7.2），所以偏移必须在渲染前一次累加，不能拿 `indexOf` 在 map 里反查。
   */
  const spacedRows = useMemo(() => {
    let offset = 0
    return model.groupRows.map((view) => {
      const item = { view, offset }
      offset += view.rows.length
      return item
    })
  }, [model.groupRows])

  // §7.1：Esc 在输入法候选期间归输入法，不能让面板把它吞掉。
  // React 根容器上的这一层先于 `useModalChrome` 挂在 document 上的监听，stopPropagation 即可让面板不关。
  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      // 候选词期间的 Esc 归输入法（Safari 组合期间也会给出 `key === 'Escape'`），
      // 这里只拦事件继续冒泡到 `useModalChrome` 的 document 监听，不关面板
      if (event.nativeEvent.isComposing) {
        event.stopPropagation()
        return
      }
      actions.onClose()
      event.stopPropagation()
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      actions.onMove(1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      actions.onMove(-1)
      return
    }
    if (event.key === 'Enter') {
      // 候选词确认的那次 Enter 不算「提交搜索」
      if (event.nativeEvent.isComposing) return
      event.preventDefault()
      actions.onSubmit()
      return
    }
    // §7.1：←/→/Home/End 有意不劫持，留给光标移动——搜索框要能改字
  }

  if (!model.open) return null

  const showSkeleton = model.phase === 'loading' && model.groupRows.length === 0

  return createPortal(
    <div className="fixed inset-0 z-overlay">
      <div
        className={cn('absolute inset-0 bg-scrim transition-opacity duration-fast ease-ldst', entered ? 'opacity-100' : 'opacity-0')}
        aria-hidden="true"
        // 关闭以 pointerdown 为准：入口「按下即开」，鼠标点入口时面板在按下那一刻就挂上了，
        // 随后的 click 会由入口（按下目标）派发到这张新遮罩上。点外面的人按下时遮罩已经在，
        // target 就是遮罩本身；点入口的那次 target 是入口内部节点，据此分辨。
        // click 只兜键盘合成的一次（`detail === 0`、没有 pointerdown 阶段）。
        onPointerDown={(event) => {
          if (event.target === event.currentTarget) actions.onClose()
        }}
        onClick={(event) => {
          if (event.detail === 0 && event.target === event.currentTarget) actions.onClose()
        }}
      />
      <div
        ref={panelRef}
        id={panelId}
        role="dialog"
        aria-modal="true"
        aria-label="聚合搜索"
        className={cn(
          'absolute flex flex-col rounded border border-border bg-surface shadow-dialog',
          'transition-all duration-fast ease-ldst',
          entered ? 'scale-100 opacity-100' : 'scale-95 opacity-0',
        )}
        style={PANEL_STYLE}
      >
        <div className="relative p-2 pb-1">
          <Input
            ref={inputRef}
            value={model.query}
            // §7.2 第 2 条：占位文案不是可编程标签，`aria-label` 必须给
            aria-label="搜索个股、行业题材或资讯"
            aria-haspopup="listbox"
            role="combobox"
            aria-expanded={flatRows.length > 0}
            aria-controls={listId}
            aria-activedescendant={activeRowId}
            aria-autocomplete="list"
            type="search"
            inputMode="search"
            autoComplete="off"
            spellCheck={false}
            placeholder="搜索个股 / 行业 / 资讯"
            leadingIcon={<Search className="size-3.5" aria-hidden="true" />}
            // 40px = `--ldst-control-h-lg`（§4.2）。`cn()` 只做拼接不做 tailwind-merge，
            // 传 `h-control-lg` 会和 `Field` 内建的 `h-control` 撞在同一优先级上，
            // 所以这里用内联 CSS 变量写死一档，与 §5.2 的 `max-height` 同一写法。
            style={{ height: 'var(--ldst-control-h-lg)' }}
            className={cn('pr-9', model.stale && 'opacity-70')}
            onChange={(event) => actions.onQueryChange(event.target.value)}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => actions.onComposing(true)}
            onCompositionEnd={() => {
              actions.onComposing(false)
            }}
          />
          {/* §5.5：清除件挂输入框内右端，不挂面板右上角——那个位置在对话框惯例里是「关闭」 */}
          {model.query !== '' && (
            <IconButton
              label="清除输入"
              icon={<X className="size-4" aria-hidden="true" />}
              className="absolute right-2 top-1/2 -translate-y-1/2"
              onClick={() => {
                actions.onClear()
                inputRef.current?.focus()
              }}
            />
          )}
        </div>

        {/* §7.5：结果变化只播报一次汇总，不逐次念整段列表打断打字 */}
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          {model.phase === 'results'
            ? `找到 ${model.rowCount} 条：${counts}。第 ${model.activeIndex + 1} 条：${
                flatRows[model.activeIndex] ? `${flatRows[model.activeIndex].code ?? ''} ${flatRows[model.activeIndex].name ?? '名称未接入'}` : ''
              }`
            : model.phase === 'loading'
              ? '正在加载搜索结果'
              : model.phase === 'short'
                ? `继续输入 ${model.remaining} 个字符`
                : model.phase === 'none'
                  ? `今日数据里没有匹配「${model.query}」`
                  : model.phase === 'failed'
                    ? '本机服务未连接'
                    : ''}
        </p>

        <div
          className="min-h-0 flex-1 overflow-auto overscroll-contain px-2 pb-2"
          role="listbox"
          id={listId}
          aria-label="搜索结果"
          aria-busy={model.pending}
          // sticky 组头会压住顶部一行，滚动定位要让出这一档（§7.3）
          style={{ scrollPaddingTop: 'var(--ldst-control-h)' }}
        >
          {model.phase === 'idle' && (
            <div className="flex flex-col gap-3 py-3">
              {/* QUOT-398：大输入框在上，空态第一行先接住「可以搜什么」——参考实现的「想研究什么」提问卡 */}
              <p className="flex items-center gap-1 text-caption text-text-muted">
                <Search className="size-3.5 shrink-0" aria-hidden="true" />
                输入关键词开始搜索，或点下面最近搜索直接进入
              </p>
              {model.recent.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">最近搜索</span>
                  <span className="flex flex-wrap gap-1">
                    {model.recent.map((item) => (
                      <button
                        key={item}
                        type="button"
                        onClick={() => actions.onRecent(item)}
                        className="inline-flex h-5 max-w-full items-center truncate rounded-xs border border-border px-2 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:bg-surface-3"
                      >
                        {item}
                      </button>
                    ))}
                  </span>
                </div>
              )}
              <span className="text-caption text-text-muted">可以搜这三类</span>
              <ul className="flex flex-col gap-1">
                {GUIDE_ROWS.map((row) => (
                  <li key={row.how} className="text-caption text-text-secondary">
                    <span className="text-text">{row.how}</span> → <span className="ldst-num">{row.to}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {model.phase === 'short' && (
            <p role="status" className="px-1 py-3 text-caption text-text-muted">
              继续输入 {model.remaining} 个字再搜（代码满 3 位、中文满 2 字就开始取数）
            </p>
          )}

          {showSkeleton && (
            <div className="flex flex-col gap-2 py-1">
              {model.pendingGroups.map((item) => (
                <div key={item.id} className="flex flex-col gap-1">
                  <GroupHeader label={item.label} count={0} badge={null} degraded={false} />
                  <SkeletonRows rows={3} slots={['20%', '34%', '16%']} />
                </div>
              ))}
            </div>
          )}

          {(model.phase === 'results' || model.phase === 'none' || model.phase === 'failed') &&
            spacedRows.map(({ view, offset }) => {
              const exit = view.exit
              return (
                <div key={view.group.id} className="flex flex-col gap-1 py-1">
                  {(view.rows.length > 0 || view.group.status === 'error' || view.group.status === 'partial') && (
                    <GroupHeader
                      label={view.group.label}
                      count={view.group.hits.length}
                      badge={view.badge}
                      degraded={view.group.status === 'error'}
                    />
                  )}
                  {view.rows.map((hit, index) => {
                    const globalIndex = offset + index
                    return (
                      <OptionRow
                        key={hit.key}
                        hit={hit}
                        id={optionId(panelId, globalIndex)}
                        active={model.activeIndex === globalIndex}
                        pos={globalIndex + 1}
                        set={model.rowCount}
                        onActivate={() => actions.onRowActivate(globalIndex)}
                        onSelect={() => actions.onSelectHit(hit)}
                      />
                    )
                  })}
                  {view.group.status === 'error' && <GroupDegrade group={view.group} onRetry={actions.onRetry} />}
                  {view.overflowHint && (
                    <p className="px-1 text-caption text-text-muted">
                      命中 {view.group.hits.length} 条，面板内只显示前 {view.rows.length} 条；更多个股用资讯雷达按代码检索。
                    </p>
                  )}
                  {exit && (
                    <button
                      type="button"
                      onClick={() => actions.onExit(exit.href)}
                      className="px-1 text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:text-text"
                    >
                      {exit.label}
                    </button>
                  )}
                </div>
              )
            })}

          {model.phase === 'none' && (
            <EmptyState
              // §8.5：不加 icon（48px 大图标在 640px 面板里过重且无信息增益）
              title={`今日数据里没有匹配「${model.query}」`}
              description={
                <>
                  没有命中，试试 6 位代码 / 行业名：个股名称只匹配当日涨停池、盯盘与候选池里的标的；
                  行业 / 题材用完整名称（如「机器人」而非「机器」）；资讯关键词可在雷达里按日期窗口再找。
                  没匹配不等于不存在——这里搜的是当日数据并集。
                </>
              }
              action={
                <Button variant="secondary" onClick={actions.onSubmit}>
                  在资讯雷达中搜索「{model.query}」
                </Button>
              }
            />
          )}

          {model.phase === 'failed' && (
            <div className="py-2">
              <EmptyState
                title={model.failureTitle ?? '本机服务未连接'}
                description={
                  <>
                    搜索要读当日涨停池、行业字典与资讯流，都来自本机只读 API。先运行{' '}
                    <code className="ldst-num">ldst run</code> 启动服务（默认 127.0.0.1:5950），再重试。
                  </>
                }
                action={
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Button variant="secondary" onClick={actions.onRetry}>
                      重试全部
                    </Button>
                    <Button variant="ghost" onClick={() => actions.onExit('/settings')}>
                      打开设置页看数据源
                    </Button>
                  </div>
                }
              />
            </div>
          )}
        </div>

        <div className="flex min-h-control items-center justify-between gap-2 border-t border-border px-3 text-caption text-text-muted">
          <span>
            {model.slow ? '本机接口响应较慢，Esc 可先关闭' : '↑↓ 选择 · Enter 打开 · Esc 关闭'}
          </span>
          <button
            type="button"
            onClick={() => setGuidesOpen((current) => !current)}
            aria-expanded={guidesOpen}
            className="text-caption text-text-secondary transition-colors duration-fast ease-ldst hover:text-text"
          >
            可搜什么？
          </button>
        </div>
        {guidesOpen && (
          <ul className="flex flex-col gap-1 border-t border-border px-3 py-2 text-caption text-text-secondary">
            {GUIDE_ROWS.map((row) => (
              <li key={row.how}>
                {row.how} → <span className="ldst-num">{row.to}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body,
  )
}
