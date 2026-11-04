/**
 * 首页搜索入口的状态机（QUOT-357 / 规格 `docs/specs/home-search.md` §8）。
 *
 * 职责边界：这里只做「输入 → 取数 → 分组序列 → 键盘意图」的编排。取数全部交给
 * QUOT-356 的 `lib/searchCore`（只通过 `sources` / `hitLimit` 这两个既有入参注入，
 * 不改它的接口形状），呈现交给 `components/search/SearchPanel`，
 * `HomeScreen.tsx` 只挂一个 `<HomeSearch />`。
 *
 * 三条规格口径写死在本模块，组件不许各写一份：
 * - 门限（§8.3）：含数字 ≥3 位、纯中文/字母 ≥2 字符才发请求；
 * - 节流（§8.3）：停止输入 250ms 后发请求，输入法组合期间不发；
 * - 竞态（§8.3）：只接受最后一轮，`superseded` 的返回整体丢弃，旧结果不清空。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { sourceLabel } from '../../api/radar'
import {
  createSearchRunner,
  httpSearchSources,
  SEARCH_GROUP_ORDER,
  type SearchGroup,
  type SearchGroupId,
  type SearchHit,
  type SearchSource,
  type SearchResult,
} from '../../lib/searchCore'
import { normalizeQuery } from '../../lib/searchIntent'
import { INDUSTRY_ROUTE, newsHref } from '../../lib/searchRoutes'
import type {
  SearchGroupView,
  SearchPanelActions,
  SearchPanelModel,
  SearchPhase,
} from '../../components/search/SearchPanel'

/** §8.3 节流：250ms——中文输入法逐字确认的节奏下不会连发三轮。 */
const DEBOUNCE_MS = 250
/** §8.4 慢响应提示的判定线；只是说明，不是超时中断。 */
const SLOW_MS = 1200
/** §14-D4：搜索历史只存本机、最多 8 条，不进 IndexedDB 迁移链。 */
const RECENT_KEY = 'ldst.home-search.recent'
const RECENT_MAX = 8
/** §6.2 面板内每组上限；取数按 20 条拿，组头才显示得出「截断前总数」。 */
const HIT_LIMIT = 20
const GROUP_ROW_LIMIT: Record<SearchGroupId, number> = {
  stocks: 5,
  themes: 4,
  news: 4,
}

/**
 * 组头骨架用的静态组名（§8.4）。
 *
 * `searchCore` 的 `GROUP_LABELS` 是模块私有（未 export），而骨架期还没有任何一组返回，
 * 拿不到 `group.label`，所以这里留一份**唯一**的本地副本；已回的组一律用 `group.label`。
 * 不导出 `GROUP_LABELS` 是为了不改 QUOT-356 的接口形状（触发条硬约束）。
 */
const PENDING_GROUPS: Array<{ id: SearchGroupId; label: string }> = [
  { id: 'stocks', label: '个股' },
  { id: 'themes', label: '行业 / 题材' },
  { id: 'news', label: '资讯' },
]

/** §8.3 请求门限：含数字走数字门限（`600 浦发` 这种混排串同样按数字算），否则按字符数。 */
export function searchGate(text: string): { allowed: boolean; remaining: number } {
  const digits = (text.match(/\d/g) ?? []).length
  const need = digits > 0 ? 3 : 2
  const have = digits > 0 ? digits : text.length
  return { allowed: have >= need, remaining: Math.max(0, need - have) }
}

function readRecent(): string[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      .map((item) => item.trim())
      .slice(0, RECENT_MAX)
  } catch {
    // 隐私模式 / 配额满时 localStorage 会抛：搜索历史读不到就当没有，不影响跳转
    return []
  }
}

function writeRecent(list: string[]): void {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* 写不进去不拦跳转 */
  }
}

/**
 * 组头溯源徽标的入参（§6.7）。
 *
 * 检索层没有 envelope，只有本轮各组自己的成败事实：`freshness` 与 `data_as_of`
 * 一律给 `null`（QUOT-331：后端没给这一档就显「未知」，不许顶替），
 * `source_status` 只表达本层确实观察到的事——整组失败 `missing`、部分源失败 `partial`、
 * 其余 `ok`。等 §9.3 的 `GET /api/search` 上线，这里换成真信封。
 */
function groupBadgeMeta(group: SearchGroup) {
  const failed = group.sources.filter((item) => item.outcome === 'error')
  return {
    freshness: null,
    source_status: group.status === 'error' ? 'missing' : group.status === 'partial' ? 'partial' : 'ok',
    source_detail:
      failed.map((item) => `${item.label}：${item.error?.detail ?? item.error?.title ?? '未返回'}`).join('；') || null,
    data_as_of: null,
  }
}

export interface HomeSearchOptions {
  /** `?search=` 深链：非 `null` 即带着这个词打开面板（也是视觉巡检拍「展开 / 有结果 / 无结果」的入口）；
   *  撤 URL 由装配件 `HomeSearch.tsx` 负责，本模块不碰路由参数 */
  initialQuery?: string | null
  /** 注入取数源：单测走这里；产品路径默认 `httpSearchSources`，本模块不含第二份判定 */
  sources?: readonly SearchSource[]
  /** 跳转由屏侧执行（`useNavigate`），控制器不关心路由实现 */
  onNavigate?: (href: string) => void
}

export function useHomeSearch(options: HomeSearchOptions = {}) {
  const { initialQuery = null, sources = httpSearchSources, onNavigate } = options

  const [open, setOpen] = useState(false)
  const [query, setQueryState] = useState('')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [pending, setPending] = useState(false)
  const [slow, setSlow] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [recent, setRecent] = useState<string[]>(() => readRecent())

  const composingRef = useRef(false)
  /** 已经发出去的那一轮查询词：用来判断当前文本有没有对应结果，避免同词重复请求 */
  const searchedRef = useRef<string | null>(null)
  const runner = useMemo(() => createSearchRunner({ sources, hitLimit: HIT_LIMIT }), [sources])

  const normalized = useMemo(() => normalizeQuery(query), [query])
  const gate = useMemo(() => searchGate(normalized.text), [normalized.text])

  const groups = useMemo<SearchGroup[]>(() => {
    if (!result) return []
    // §6.1 顺序恒定：数据层的顺序就是渲染顺序，这里不再排第二次
    return SEARCH_GROUP_ORDER.map((id) => result.groups.find((group) => group.id === id)).filter(
      (group): group is SearchGroup => !!group,
    )
  }, [result])

  /** §6.2：面板内截断，组头计数用截断前的总数 */
  const rows = useMemo(() => groups.flatMap((group) => group.hits.slice(0, GROUP_ROW_LIMIT[group.id])), [groups])

  /**
   * 指针按下时入口还没拿到焦点，`useModalChrome` 记录的「关闭后回哪去」会退化成 body。
   * 按下即开的同时先把焦点交给入口，Esc 关闭后焦点才回得来（键盘 Enter/Space 原生就会先聚焦）。
   */
  const openPanelFromPointer = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    event.currentTarget.focus({ preventScroll: true })
    setOpen(true)
  }, [])

  const openPanel = useCallback(() => setOpen(true), [])

  const closePanel = useCallback(() => {
    runner.cancel()
    searchedRef.current = null
    setPending(false)
    setSlow(false)
    setOpen(false)
    setQueryState('')
    setResult(null)
    setActiveIndex(0)
  }, [runner])

  // 卸载时取消在飞的一轮，避免关闭后回写状态
  useEffect(() => () => runner.cancel(), [runner])

  // `?search=` 深链：参数在就带着它打开面板（撤 URL 在 `HomeSearch.tsx`）
  useEffect(() => {
    if (initialQuery === null) return
    setOpen(true)
    setQueryState(initialQuery)
  }, [initialQuery])

  const phase = useMemo<SearchPhase>(() => {
    if (normalized.empty) return 'idle'
    if (!gate.allowed) return 'short'
    if (!result) return 'loading'
    if (rows.length > 0) return 'results'
    const attempted = result.groups.filter((group) => group.sources.length > 0)
    if (attempted.length > 0 && attempted.every((group) => group.status === 'error')) return 'failed'
    return 'none'
  }, [gate.allowed, normalized.empty, result, rows.length])

  // 节流取数：越过门限且文本确实变了才发；输入法组合期间不发（拼音未确认就扫全表是白跑一轮）
  useEffect(() => {
    if (!open) return
    if (composingRef.current) return
    if (normalized.empty || !gate.allowed) {
      searchedRef.current = null
      setResult(null)
      setPending(false)
      setSlow(false)
      return
    }
    if (searchedRef.current === normalized.text) return
    setPending(true)
    const timer = window.setTimeout(() => {
      searchedRef.current = normalized.text
      void runner
        .search(normalized.text)
        .then((next) => {
          // §8.3 竞态：晚到的旧结果整体丢弃，已显示的结果保持不动
          if (next.superseded || next.aborted) return
          setResult(next)
          setPending(false)
          setSlow(false)
        })
        .catch(() => {
          // `runSearch` 设计上不抛错；真抛了也不能让面板停在只有转圈的状态
          setPending(false)
          setSlow(false)
        })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [open, normalized.empty, normalized.text, gate.allowed, runner])

  // §8.4 慢响应：1.2s 没回就在底部提示条说明一句，不中断请求
  useEffect(() => {
    if (!pending) {
      setSlow(false)
      return
    }
    const timer = window.setTimeout(() => setSlow(true), SLOW_MS)
    return () => window.clearTimeout(timer)
  }, [pending])

  // §7.2 第 4 条：结果序列变化后高亮必须指向真实存在的行
  useEffect(() => {
    setActiveIndex((current) => {
      if (rows.length === 0) return -1
      if (current < 0) return 0
      return Math.min(current, rows.length - 1)
    })
  }, [rows.length])

  /** §7.1 全局 `/`：只在面板未开且焦点不在输入态时劫持 */
  useEffect(() => {
    if (open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) return
      event.preventDefault()
      setOpen(true)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open])

  const remember = useCallback((term: string) => {
    const text = term.trim()
    if (!text) return
    setRecent((current) => {
      const next = [text, ...current.filter((item) => item !== text)].slice(0, RECENT_MAX)
      writeRecent(next)
      return next
    })
  }, [])

  const navigateTo = useCallback(
    (href: string, term: string) => {
      if (!href) return
      remember(term)
      closePanel()
      onNavigate?.(href)
    },
    [closePanel, onNavigate, remember],
  )

  /**
   * §14-D4：历史记的是**用户输入的关键词**，不是命中条的名称——
   * 记成「浦发银行」的话，下次点 chip 搜出来的东西和上次不一样了。
   */
  const selectHit = useCallback(
    (hit: SearchHit) => navigateTo(hit.route, normalized.text),
    [navigateTo, normalized.text],
  )

  /** §3.4 / §8.5：兜底出口带关键词；代码意图带 `?code=`（NewsScreen 今天就读得懂的那条） */
  const openNews = useCallback(() => {
    const text = normalized.text
    if (!text) return
    navigateTo(normalized.code ? newsHref({ code: normalized.code }) : newsHref({ q: text }), text)
  }, [navigateTo, normalized.code, normalized.text])

  /** Enter：有高亮行走高亮，没有高亮走第一项；一组命中都没有走 §8.5 的出口 */
  const submit = useCallback(() => {
    if (phase === 'idle' || phase === 'short') return
    const active = rows[activeIndex] ?? rows[0]
    if (active) {
      selectHit(active)
      return
    }
    if (phase === 'none' || phase === 'failed') openNews()
  }, [activeIndex, openNews, phase, rows, selectHit])

  const moveActive = useCallback(
    (delta: number) => {
      if (rows.length === 0) return
      // §7.1：到头即停，不循环
      setActiveIndex((current) => Math.max(0, Math.min(rows.length - 1, (current < 0 ? -1 : current) + delta)))
    },
    [rows.length],
  )

  const clearQuery = useCallback(() => {
    setQueryState('')
    searchedRef.current = null
    setResult(null)
    setPending(false)
    setActiveIndex(0)
  }, [])

  /**
   * 单组重试 / 整体重试：数据层没有按组重取的入口（一轮 `runSearch` 打全部源），
   * 所以两条都落到「同一关键词再跑一轮」。其余组的命中会原样回来，面板不闪空。
   */
  const retry = useCallback(() => {
    const text = normalized.text
    if (!text || !gate.allowed) return
    searchedRef.current = null
    setPending(true)
    void runner.search(text).then((next) => {
      if (next.superseded || next.aborted) return
      setResult(next)
      setPending(false)
    })
  }, [gate.allowed, normalized.text, runner])

  const groupRows = useMemo<SearchGroupView[]>(
    () =>
      groups
        .filter((group) => group.status !== 'skipped')
        .map((group) => ({
          group,
          badge: groupBadgeMeta(group),
          rows: group.hits
            .slice(0, GROUP_ROW_LIMIT[group.id])
            // 资讯行的 `detail` 是数据集名（`flash_cls`），雷达页同一栏目显示的是中文名：
            // 复用 `api/radar` 里唯一那份 label map，不在面板侧再抄第二份，也不改检索层形状
            .map((hit) => (group.id === 'news' && hit.detail ? { ...hit, detail: sourceLabel(hit.detail) } : hit)),
          /** §6.2 组尾出口：行业看全表、资讯带关键词；个股第一期不分页（§14-B2） */
          exit:
            group.id === 'themes' && group.hits.length > 0
              ? { label: '看全部行业 →', href: INDUSTRY_ROUTE }
              : group.id === 'news' && group.hits.length > 0
                ? {
                    label: `查看全部 ${group.hits.length} 条 →`,
                    href: newsHref(normalized.code ? { code: normalized.code } : { q: normalized.text }),
                  }
                : null,
          overflowHint: group.id === 'stocks' && group.hits.length > GROUP_ROW_LIMIT.stocks,
          loading: pending && group.hits.length === 0,
        })),
    // newsHref / GROUP_ROW_LIMIT 是模块常量级依赖，不参与 memo
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, normalized.code, normalized.text, pending],
  )

  /** §8.7：整体失败时把数据层给的第一条源错误结论抬上去，面板不自己编文案 */
  const failureTitle = useMemo(() => {
    const failed = groups.find((group) => group.status === 'error')
    return failed?.error?.title ?? null
  }, [groups])

  const setQuery = useCallback((next: string) => {
    setQueryState(next)
    // §7.2 第 4 条：输入一变高亮就回第一项，否则 `aria-activedescendant` 指向旧序列
    setActiveIndex(0)
  }, [])

  const model = useMemo<SearchPanelModel>(
    () => ({
      open,
      query,
      phase,
      groupRows,
      pendingGroups: PENDING_GROUPS,
      rowCount: rows.length,
      activeIndex,
      stale: pending && result !== null,
      slow,
      pending,
      remaining: gate.remaining,
      recent,
      failureTitle,
    }),
    // §8.3：`stale` 判据「有旧结果 + 新一轮在飞」
    [activeIndex, failureTitle, gate.remaining, groupRows, open, pending, phase, query, recent, result, rows.length, slow],
  )

  const actions = useMemo<SearchPanelActions>(
    () => ({
      onQueryChange: setQuery,
      onMove: moveActive,
      onRowActivate: setActiveIndex,
      onSubmit: submit,
      onSelectHit: selectHit,
      // §8.2 最近搜索 chip：填回输入框走同一条门限 + 节流链路，不另开一条取数路径
      onRecent: (value: string) => setQuery(value),
      onExit: (href: string) => navigateTo(href, normalized.text),
      onClear: clearQuery,
      onRetry: retry,
      onClose: closePanel,
      onComposing: (value: boolean) => {
        composingRef.current = value
      },
    }),
    [clearQuery, closePanel, moveActive, navigateTo, normalized.text, retry, selectHit, setQuery, submit],
  )

  return { open, model, actions, openPanel, openPanelFromPointer }
}
