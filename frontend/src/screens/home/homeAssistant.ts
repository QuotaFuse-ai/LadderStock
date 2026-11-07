import type {
  LadderEnvelope,
  ValidationPayload,
  WatchSnapshotPayload,
  WatchSnapshotRow,
} from '../../api'
import type { IndexCell, IndicesPayload, WatchlistItem, WatchlistPayload } from '../../api/radar'
import { formatTime } from '../../lib/format'
import type { BlockView } from './useHomeSource'

/**
 * 首页研究对话的**上下文白名单**与「本轮来源」清单（QUOT-472）。
 *
 * 与 QUOT-454 的页级上下文同一套口径：只允许**已经渲染在首页上**、且不属于私有的字段。
 * 首页四块里 C（自选）与 F（盯盘快照）是公开行情，A（指数）是公开指数；
 * **D（持仓）整块不进上下文**——成本、数量、盈亏都是私有字段，助手不需要它们。
 *
 * 「本轮来源」由同一份输入派发，所以「页面上写了什么」与「这一轮实际发了什么」不会各写一份。
 */

/** 送进模型的上下文体积上限参考：后端 `MAX_CONTEXT_CHARS = 12000`，这里按条数先截断。 */
const MAX_WATCHLIST_ROWS = 20
const MAX_WATCH_ROWS = 10

export interface HomeAssistantContext {
  page: '首页'
  date: string
  market: {
    /** 该批读数里最晚的行情时间；非交易时段就是上一交易日收盘时刻 */
    asOf: string | null
    indices: Array<{ code: string; name: string | null; price: number | null; changePct: number | null }>
  } | null
  watchlist: {
    asOf: string | null
    total: number
    rows: Array<{ code: string; name: string | null; price: number | null; changePct: number | null }>
  } | null
  watch: {
    tradeDate: string | null
    total: number
    rows: Array<{ code: string; name: string | null; changePct: number | null; status: string | null }>
  } | null
  validation: { conditions: number } | null
  /** 首页确实没取到的块（数据源原因，不含传输错误） */
  unavailable: string[]
}

export interface HomeContextSource {
  id: 'indices' | 'watchlist' | 'watch' | 'validation'
  label: string
  /** 「取数时间」：能拿到 `as_of` 就用它，拿不到就写交易日 / 条数 */
  detail: string
  /** 同一条数据在站内的落点；指数块只存在于首页，所以为 `null` */
  href: string | null
  location: string
  count: number | null
}

export interface HomeAssistantViews {
  indices: BlockView<IndicesPayload>
  watchlist: BlockView<WatchlistPayload>
  watch: BlockView<LadderEnvelope<WatchSnapshotPayload>>
  validation: BlockView<LadderEnvelope<ValidationPayload>>
}

export interface HomeAssistantBundle {
  context: HomeAssistantContext
  sources: HomeContextSource[]
}

function usable<T>(view: BlockView<T>): T | null {
  return view.state === 'normal' || view.state === 'partial' ? view.data : null
}

function latestAsOf(cells: IndexCell[]): string | null {
  const stamps = cells
    .map((cell) => cell.as_of)
    .filter((value): value is string => Boolean(value))
    .sort()
  return stamps.length > 0 ? stamps[stamps.length - 1] : null
}

function quoteTime(value: string | null | undefined): string {
  return value ? `取数 ${formatTime(value)}` : '取数时间未标注'
}

/**
 * 把首页四块视图拼成一份上下文与一份来源清单。
 *
 * 加载中 / 失败 / 空态的块一律 `null`，并写进 `unavailable`：模型据此说「这一项没取到」，
 * 前端据此在「本轮来源」里直说「本轮未取数」，两头都不许用一般知识补。
 */
export function buildHomeAssistant(date: string, views: HomeAssistantViews): HomeAssistantBundle {
  const unavailable: string[] = []
  const sources: HomeContextSource[] = []

  const indicesPayload = usable(views.indices)
  const cells = indicesPayload?.cells ?? []
  const readableCells = cells.filter((cell) => cell.price !== null)
  const indicesAsOf = latestAsOf(cells)
  let market: HomeAssistantContext['market'] = null
  if (readableCells.length > 0) {
    market = {
      asOf: indicesAsOf,
      indices: readableCells.map((cell) => ({
        code: cell.code,
        name: cell.name,
        price: cell.price,
        changePct: cell.change_pct,
      })),
    }
    sources.push({
      id: 'indices',
      label: '大盘与全球指数',
      detail: quoteTime(indicesAsOf),
      href: null,
      location: '首页 A 块',
      count: readableCells.length,
    })
  } else {
    unavailable.push('大盘指数：首页这一块没有可读点位')
  }

  const watchlistPayload = usable(views.watchlist)
  const watchItems = (watchlistPayload?.items ?? []).filter((item) => item.available)
  let watchlist: HomeAssistantContext['watchlist'] = null
  if (watchItems.length > 0) {
    watchlist = {
      asOf: watchlistPayload?.price_as_of ?? watchlistPayload?.data_as_of ?? null,
      total: watchItems.length,
      rows: watchItems.slice(0, MAX_WATCHLIST_ROWS).map((item: WatchlistItem) => ({
        code: item.code,
        name: item.name,
        price: item.latest_price,
        changePct: item.change_pct,
      })),
    }
    sources.push({
      id: 'watchlist',
      label: '我的自选',
      detail: quoteTime(watchlist.asOf),
      href: '/my-stocks?tab=watch',
      location: '我的股票 · 自选',
      count: watchItems.length,
    })
  } else {
    unavailable.push('我的自选：首页这一块没有可读报价')
  }

  const watchPayload = usable(views.watch)?.payload as WatchSnapshotPayload | undefined
  const watchRows = watchPayload?.rows ?? []
  let watch: HomeAssistantContext['watch'] = null
  if (watchRows.length > 0) {
    watch = {
      tradeDate: watchPayload?.trade_date ?? null,
      total: watchRows.length,
      rows: watchRows.slice(0, MAX_WATCH_ROWS).map((row: WatchSnapshotRow) => ({
        code: row.code,
        name: row.name,
        changePct: row.change_pct,
        status: row.status,
      })),
    }
    sources.push({
      id: 'watch',
      label: '盯盘快照',
      detail: watchPayload?.trade_date ? `交易日 ${watchPayload.trade_date}` : '交易日未标注',
      href: '/watch',
      location: '盯盘',
      count: watchRows.length,
    })
  } else {
    unavailable.push('盯盘快照：首页这一块没有可读行')
  }

  const validationPayload = usable(views.validation)?.payload as ValidationPayload | undefined
  const conditions = validationPayload?.conditions ?? []
  let validation: HomeAssistantContext['validation'] = null
  if (conditions.length > 0) {
    // 条件正文是用户自己写的盘前计划，属于私有内容：只给条数，不给文案
    validation = { conditions: conditions.length }
    sources.push({
      id: 'validation',
      label: '验证条件',
      detail: `${conditions.length} 条`,
      href: '/validate',
      location: '验证',
      count: conditions.length,
    })
  }

  return {
    context: { page: '首页', date, market, watchlist, watch, validation, unavailable },
    sources,
  }
}

/** 发给后端的就是这份 JSON；「本轮来源」读的是同一次构建产出的 `sources`。 */
export function serializeHomeAssistantContext(context: HomeAssistantContext): string {
  return JSON.stringify(context, null, 2)
}
