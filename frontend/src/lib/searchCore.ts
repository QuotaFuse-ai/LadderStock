/**
 * 聚合搜索逻辑层（QUOT-356）。
 *
 * 一条链路：`输入串 → 归一化 + 意图 → 并发打若干现有只读接口 → 分组命中 + 跳转目标`。
 * 这一层不碰 DOM、不碰 uPlot / matchMedia，`vitest` 注入假源就能跑完整逻辑。
 *
 * 三条硬口径：
 * 1. **只用已有接口**。本期不新增后端端点，所以「个股」名称匹配只覆盖本机当天数据集里
 *    带代码 + 名称的那部分（连板天梯 + 候选池）；全市场按名称模糊检索是缺口，已写进交付评论。
 * 2. **单源失败不影响其它组**。每个源独立 try/catch，失败只落到该源的
 *    `SourceStat.error`；整组全挂才把组的 `status` 打成 `error`，UI 据此显示「该组暂不可用」。
 * 3. **没有数据就说没有**。`name: null` 表示名称未接入（跳转仍可用），空结果返回
 *    `status: 'empty'` 的空结构而不是 `null`，也不兜底演示数据。
 */

import { api } from '../api'
import { httpIndustrySource } from '../api/industry'
import { describeRadarError, httpRadarSource, type NewsQuery } from '../api/radar'
import { localDateIso } from './format'
import {
  classifyIntent,
  matchScore,
  normalizeQuery,
  type NormalizedQuery,
  type SearchIntent,
} from './searchIntent'
import { industryHref, newsHref, researchHref } from './searchRoutes'

// ---------------------------------------------------------------- 类型

export type SearchGroupId = 'stocks' | 'themes' | 'news'

export type SearchTag = '个股' | '行业' | '题材' | '资讯'

/** 分组固定顺序：个股 → 行业 / 题材 → 资讯；UI 按这个顺序渲染。 */
export const SEARCH_GROUP_ORDER: readonly SearchGroupId[] = ['stocks', 'themes', 'news']

const GROUP_LABELS: Record<SearchGroupId, string> = {
  stocks: '个股',
  themes: '行业 / 题材',
  news: '资讯',
}

export interface SearchError {
  title: string
  detail: string
  code: string | null
}

/** 一条可跳转的命中记录。`route` 一律由 `searchRoutes` 生成，源自己不许拼 URL。 */
export interface SearchHit {
  /** 组内去重键：代码 / 名称 / 资讯 id */
  key: string
  group: SearchGroupId
  tag: SearchTag
  code: string | null
  /** `null` = 本机数据源没给名称，展示「未接入」，不许拿代码顶替 */
  name: string | null
  detail: string | null
  /** 跳转目标；`''` = 该记录缺关键字段无法跳转，UI 需禁用 */
  route: string
  /** 该命中来自哪几个源 id，UI 的来源标注直接用 */
  sources: string[]
  /** 排序分（同源重复取最高分） */
  score: number
  /** `false` = 只由代码格式推出，标的名称与存在性未经数据源核实 */
  verified: boolean
}

export type GroupStatus = 'ok' | 'empty' | 'partial' | 'error' | 'skipped'

export type SourceOutcome = 'ok' | 'empty' | 'error'

/** 每个源的独立结果；单接口延迟就记在这里（接线后跑一次就有真实口径）。 */
export interface SourceStat {
  id: string
  label: string
  outcome: SourceOutcome
  hits: number
  elapsedMs: number
  error: SearchError | null
}

export interface SearchGroup {
  id: SearchGroupId
  label: string
  status: GroupStatus
  hits: SearchHit[]
  sources: SourceStat[]
  elapsedMs: number
  /** 整组不可用（所有源都失败）时的文案；`partial` 的逐源原因在 `sources[].error` */
  error: SearchError | null
}

export interface SearchResult {
  /** 归一化前的原始输入，UI 回填输入框时用 */
  input: string
  query: NormalizedQuery
  intent: SearchIntent
  /** 本次取数使用的数据日（个股 / 题材 / 资讯三类源都按它拉） */
  date: string
  groups: SearchGroup[]
  aborted: boolean
  generation: number
  /** 本轮已被更新的一轮取代或被取消：调用方必须整体丢弃，不能让旧命中覆盖新命中 */
  superseded: boolean
}

/** 源交给聚合层的候选记录；路由仍由聚合层统一映射。 */
export interface Candidate {
  key: string
  tag: SearchTag
  code: string | null
  name: string | null
  detail: string | null
  score: number
  /** 省略即 `true` */
  verified?: boolean
}

export interface SearchContext {
  query: NormalizedQuery
  intent: SearchIntent
  date: string
  /** 资讯 / 候选池单源取数上限 */
  sourceLimit: number
  signal?: AbortSignal
}

export interface SearchSource {
  id: string
  group: SearchGroupId
  label: string
  /** 该源只在列出的意图下才值得打；省略 = 全意图都打 */
  intents?: readonly SearchIntent[]
  load(context: SearchContext): Promise<Candidate[]>
}

export interface SearchOptions {
  sources?: readonly SearchSource[]
  date?: string
  /** 每组最多返回几条命中 */
  hitLimit?: number
  sourceLimit?: number
  signal?: AbortSignal
  /** 时间源，测试注入用 */
  now?: () => number
}

// ---------------------------------------------------------------- 打分

/** 名称 / 代码的统一匹配：代码意图走精确代码，其余走包含匹配。 */
function scoreText(query: NormalizedQuery, name: string | null, code: string | null): number {
  if (query.code && code === query.code) return 100
  const byName = matchScore(query.text, name)
  if (byName) return byName
  // `6000` 这种非 6 位片段：允许按代码前缀找
  if (code && query.text && code.toLowerCase().startsWith(query.text.toLowerCase())) return 70
  return 0
}

// ---------------------------------------------------------------- 默认源

const DEFAULT_SOURCE_LIMIT = 200

/**
 * 全部是只读 `GET`。每个源只做「取回来 → 投影成候选」，
 * 匹配与打分统一交给 `scoreText`，不让某个源偷偷改规则。
 */
export const httpSearchSources: readonly SearchSource[] = [
  {
    id: 'code_direct',
    group: 'stocks',
    label: '代码直达',
    intents: ['code'],
    /**
     * 代码输入即使不在本机数据集里也要给出一跳：`/research/:code` 的入参就是代码本身
     * （同 `ResearchEntryScreen` 口径）。这条记录 `verified: false` 且名称为 `null`，
     * UI 必须标「未核实」，不能假装知道这只股票叫什么。
     */
    load: async ({ query, intent }) => {
      if (intent !== 'code' || !query.code) return []
      return [
        {
          key: query.code,
          tag: '个股',
          code: query.code,
          name: null,
          detail: '名称未在本机数据源接入',
          score: 100,
          verified: false,
        },
      ]
    },
  },
  {
    id: 'limit_ladder',
    group: 'stocks',
    label: '连板天梯',
    async load({ query, date, signal }) {
      const envelope = await api.ladder(date, signal)
      const ladder = envelope.payload.ladder
      if (!ladder.available) return []
      const rows = [...ladder.ladder.flatMap((level) => level.stocks), ...ladder.leader_candidates]
      const out: Candidate[] = []
      for (const row of rows) {
        const score = scoreText(query, row.name || null, row.code)
        if (!score) continue
        out.push({
          key: row.code,
          tag: '个股',
          code: row.code,
          name: row.name || null,
          detail: null,
          score,
        })
      }
      return out
    },
  },
  {
    id: 'candidate_pool',
    group: 'stocks',
    label: '候选池',
    async load({ query, date, sourceLimit, signal }) {
      const payload = await httpRadarSource.pool({ date, limit: sourceLimit }, signal)
      if (!payload.available) return []
      const out: Candidate[] = []
      for (const row of payload.candidates) {
        // `parsePoolCandidate` 在缺名称时会把代码塞进 `name`（`radar.ts:724`），
        // 这里必须还原成 `null`，否则搜索结果会把代码当股票名展示
        const name = row.name && row.name !== row.code ? row.name : null
        const score = scoreText(query, name, row.code)
        if (!score) continue
        out.push({
          key: row.code,
          tag: '个股',
          code: row.code,
          name,
          detail: row.raw.theme_tags.length ? row.raw.theme_tags.join('、') : null,
          score,
        })
      }
      return out
    },
  },
  {
    id: 'industry_list',
    group: 'themes',
    label: '行业清单',
    intents: ['name', 'keyword'],
    async load({ query, signal }) {
      const payload = await httpIndustrySource.list(signal)
      if (!payload.available) return []
      const out: Candidate[] = []
      for (const row of payload.industries) {
        const score = Math.max(matchScore(query.text, row.name), matchScore(query.text, row.l1_code))
        if (!score) continue
        out.push({
          key: row.name,
          tag: '行业',
          code: null,
          name: row.name,
          detail: row.l1_code ? `L1 ${row.l1_code}` : null,
          score,
        })
      }
      return out
    },
  },
  {
    id: 'market_theme',
    group: 'themes',
    label: '题材强度',
    intents: ['name', 'keyword'],
    async load({ query, date, signal }) {
      const envelope = await api.marketTheme(date, signal)
      const payload = envelope.payload
      if (!payload.available) return []
      const out: Candidate[] = []
      for (const row of payload.themes) {
        const score = matchScore(query.text, row.theme)
        if (!score) continue
        out.push({
          key: row.theme,
          tag: '题材',
          code: null,
          name: row.theme,
          detail: row.leader ? `龙头 ${row.leader}` : null,
          score,
        })
      }
      return out
    },
  },
  {
    id: 'news_stream',
    group: 'news',
    label: '资讯雷达',
    async load({ query, intent, date, sourceLimit, signal }) {
      // 代码意图交给服务端 `code=`（`/api/news` 真支持这个参数）；关键词意图服务端
      // 没有全文检索参数，只能在当前窗口内做正文包含匹配——这是本期的已知取数缺口。
      const codes = intent === 'code' ? query.codes : null
      const request: NewsQuery = { date, limit: sourceLimit, codes }
      const payload = await httpRadarSource.news(request, signal)
      const term = query.text
      const out: Candidate[] = []
      for (const item of payload.items) {
        const indexed = `${item.title} ${item.summary} ${item.industries.join(' ')} ${item.related_codes.join(' ')}`
        const score = codes ? 100 : matchScore(term, indexed)
        if (!score) continue
        out.push({
          key: item.id,
          tag: '资讯',
          code: item.related_codes[0] ?? null,
          // 资讯的展示主行是标题，`name` 复用为标题
          name: item.title || null,
          detail: item.source || null,
          score,
        })
      }
      return out
    },
  },
]

// ---------------------------------------------------------------- 路由映射

function routeForHit(group: SearchGroupId, hit: SearchHit, query: NormalizedQuery): string {
  if (group === 'stocks') return hit.code ? researchHref(hit.code) : ''
  if (group === 'themes') return hit.name ? industryHref(hit.name) : ''
  // 代码意图用 NewsScreen 今天就读得懂的 `?code=`；其余把用户输入的关键词放进 `?q=`
  //（不是命中条的标题——标题是结果，不是查询条件）
  return query.code ? newsHref({ code: query.code }) : newsHref({ q: query.text })
}

// ---------------------------------------------------------------- 执行

function toSearchError(error: unknown, label: string): SearchError {
  // ApiError → 面向用户文案只有一处真源（`describeRadarError`），这里不复制第二份
  const described = describeRadarError(error, label)
  return { title: described.title, detail: described.detail, code: described.code }
}

function groupStatus(sources: readonly SourceStat[], hits: readonly SearchHit[]): GroupStatus {
  // 该意图下这组没有任何源可打（代码意图不打行业 / 题材）
  if (sources.length === 0) return 'skipped'
  const failed = sources.filter((item) => item.outcome === 'error')
  if (hits.length > 0) return failed.length === 0 ? 'ok' : 'partial'
  if (failed.length === sources.length) return 'error'
  return failed.length === 0 ? 'empty' : 'partial'
}

function toHits(candidates: readonly Candidate[], group: SearchGroupId, limit: number, sourceOf: (key: string) => string[]): SearchHit[] {
  const merged = new Map<string, SearchHit>()
  for (const item of candidates) {
    const existing = merged.get(item.key)
    if (existing) {
      // 同一标的在多个源里出现：分数取最高，任一源核实过即算核实，
      // 缺失的名称 / 副行用有值的那条补上
      existing.score = Math.max(existing.score, item.score)
      existing.verified = existing.verified || (item.verified ?? true)
      if (item.name && !existing.name) {
        existing.name = item.name
        // 名称一旦有真实来源，代码直达那条「未接入」占位副行就要换成数据源的副行
        existing.detail = item.detail
      } else if (item.detail && !existing.detail) {
        existing.detail = item.detail
      }
      continue
    }
    merged.set(item.key, {
      key: item.key,
      group,
      tag: item.tag,
      code: item.code,
      name: item.name,
      detail: item.detail,
      route: '',
      sources: sourceOf(item.key),
      score: item.score,
      verified: item.verified ?? true,
    })
  }
  return [...merged.values()]
    .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key, 'zh-Hans-CN'))
    .slice(0, limit)
}

function skippedGroups(): SearchGroup[] {
  return SEARCH_GROUP_ORDER.map<SearchGroup>((id) => ({
    id,
    label: GROUP_LABELS[id],
    status: 'skipped',
    hits: [],
    sources: [],
    elapsedMs: 0,
    error: null,
  }))
}

/**
 * 打一次聚合搜索。本函数不抛错：任何源的失败只体现在
 * `groups[].sources[].error` 与 `groups[].status` 上，调用方永远拿到三组完整结构。
 */
export async function runSearch(input: string, options: SearchOptions = {}): Promise<SearchResult> {
  const sources = options.sources ?? httpSearchSources
  const now = options.now ?? Date.now
  const query = normalizeQuery(input)
  const intent = classifyIntent(query)
  const date = options.date ?? localDateIso()
  const hitLimit = options.hitLimit ?? 8
  const sourceLimit = options.sourceLimit ?? DEFAULT_SOURCE_LIMIT
  const signal = options.signal

  // 空输入：一个请求都不发
  if (query.empty) {
    return {
      input,
      query,
      intent,
      date,
      groups: skippedGroups(),
      aborted: signal?.aborted === true,
      generation: 0,
      superseded: false,
    }
  }

  const context: SearchContext = { query, intent, date, sourceLimit, ...(signal ? { signal } : {}) }
  const active = sources.filter((spec) => !spec.intents || spec.intents.includes(intent))

  const settled = await Promise.all(
    active.map(async (spec): Promise<{ spec: SearchSource; stat: SourceStat; items: Candidate[] }> => {
      const from = now()
      try {
        const items = await spec.load(context)
        return {
          spec,
          items,
          stat: {
            id: spec.id,
            label: spec.label,
            outcome: items.length > 0 ? 'ok' : 'empty',
            hits: items.length,
            elapsedMs: Math.max(0, now() - from),
            error: null,
          },
        }
      } catch (error) {
        const cancelled = signal?.aborted === true
        return {
          spec,
          items: [],
          stat: {
            id: spec.id,
            label: spec.label,
            outcome: 'error',
            hits: 0,
            elapsedMs: Math.max(0, now() - from),
            error: cancelled
              ? { title: `${spec.label}已取消`, detail: '本轮请求已被更新的搜索取代。', code: 'ABORTED' }
              : toSearchError(error, spec.label),
          },
        }
      }
    }),
  )

  const groups = SEARCH_GROUP_ORDER.map<SearchGroup>((id) => {
    const rows = settled.filter((row) => row.spec.group === id)
    const stats = rows.map((row) => row.stat)
    const failed = stats.filter((item) => item.outcome === 'error')
    const hits = toHits(
      rows.flatMap((row) => row.items),
      id,
      hitLimit,
      (key) => rows.filter((row) => row.items.some((item) => item.key === key)).map((row) => row.spec.id),
    ).map((hit) => ({ ...hit, route: routeForHit(id, hit, query) }))
    return {
      id,
      label: GROUP_LABELS[id],
      status: groupStatus(stats, hits),
      hits,
      sources: stats,
      elapsedMs: stats.reduce((max, item) => Math.max(max, item.elapsedMs), 0),
      error: stats.length > 0 && failed.length === stats.length ? failed[0].error : null,
    }
  })

  return {
    input,
    query,
    intent,
    date,
    groups,
    aborted: signal?.aborted === true,
    generation: 0,
    superseded: false,
  }
}

export interface SearchRunner {
  /** 发起一轮；上一轮 in-flight 会被 abort，晚到的旧结果标 `superseded`。 */
  search(input: string): Promise<SearchResult>
  /** 主动取消当前一轮（面板关闭、Esc）。 */
  cancel(): void
  /** 当前轮次号，用于调试与「有没有还在飞的请求」的判断。 */
  current(): number
}

/**
 * 竞态包装：换输入时丢弃上一轮 in-flight，且**晚到的旧结果不会覆盖新结果**——
 * 调用方只要检查 `result.superseded`，为 `true` 就什么都不做。
 */
export function createSearchRunner(options: SearchOptions = {}): SearchRunner {
  let generation = 0
  let controller: AbortController | null = null

  return {
    current: () => generation,
    async search(input: string) {
      const mine = ++generation
      controller?.abort()
      const next = new AbortController()
      controller = next
      const result = await runSearch(input, { ...options, signal: next.signal })
      // `mine !== generation` = 期间有人发起了更新的一轮；`aborted` = 本轮被取消
      return { ...result, generation: mine, superseded: mine !== generation || next.signal.aborted }
    },
    cancel() {
      generation += 1
      controller?.abort()
      controller = null
    },
  }
}
