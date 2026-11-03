/**
 * 聚合搜索逻辑层（QUOT-356）。
 *
 * 两组口径分开钉：
 * - `runSearch` + 注入的假源：只验聚合契约（分组、去重、降级、取消、竞态、空输入不请求）。
 * - `httpSearchSources` + `global.fetch` 夹具：验「打了哪个接口、参数怎么带、字段名对不对」，
 *   夹具字段名抄自 `src/api.ts` 与 `src/api/radar.ts` 的解析口径，改错一个键这里先红。
 *
 * 假源不是 mock 业务数据来骗测试：它们只把注入的行按同一套匹配规则过滤，
 * 真接口的数据形状由下面的 fetch 夹具负责。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api'
import {
  createSearchRunner,
  httpSearchSources,
  runSearch,
  SEARCH_GROUP_ORDER,
  type Candidate,
  type SearchContext,
  type SearchGroup,
  type SearchSource,
} from '../src/lib/searchCore'
import { classifyIntent, matchScore, normalizeQuery } from '../src/lib/searchIntent'
import { industryHref, isKnownRouteHref, newsHref, researchHref } from '../src/lib/searchRoutes'

const DATE = '2026-10-30'

// ---------------------------------------------------------------- 假源

const calls: SearchContext[] = []

function stockSource(rows: Array<{ code: string; name: string | null }>, id = 'test_stocks', label = '测试个股源'): SearchSource {
  return {
    id,
    label,
    group: 'stocks',
    async load(context) {
      calls.push(context)
      const out: Candidate[] = []
      for (const row of rows) {
        const exact = context.query.code === row.code
        const score = exact ? 100 : matchScore(context.query.text, row.name)
        if (!score) continue
        out.push({ key: row.code, tag: '个股', code: row.code, name: row.name, detail: null, score })
      }
      return out
    },
  }
}

function themeSource(names: string[], id = 'test_industries', label = '测试行业源'): SearchSource {
  return {
    id,
    label,
    group: 'themes',
    async load(context) {
      calls.push(context)
      const out: Candidate[] = []
      for (const name of names) {
        const score = matchScore(context.query.text, name)
        if (!score) continue
        out.push({ key: name, tag: '行业', code: null, name, detail: null, score })
      }
      return out
    },
  }
}

function newsSource(
  rows: Array<{ id: string; title: string; code: string | null }>,
  id = 'test_news',
  label = '测试资讯源',
): SearchSource {
  return {
    id,
    label,
    group: 'news',
    async load(context) {
      calls.push(context)
      // 代码意图：服务端已按 code 过滤，假源直接放行；其余按标题包含匹配
      if (context.intent === 'code') {
        return rows.map((row) => ({ key: row.id, tag: '资讯' as const, code: row.code, name: row.title, detail: null, score: 100 }))
      }
      const out: Candidate[] = []
      for (const row of rows) {
        const score = matchScore(context.query.text, row.title)
        if (!score) continue
        out.push({ key: row.id, tag: '资讯', code: row.code, name: row.title, detail: null, score })
      }
      return out
    },
  }
}

function group(result: { groups: SearchGroup[] }, id: SearchGroup['id']): SearchGroup {
  const found = result.groups.find((item) => item.id === id)
  if (!found) throw new Error(`没有 ${id} 组`)
  return found
}

/** 去掉实测耗时：两次调用的 `elapsedMs` 不保证相等，其余字段才是聚合契约。 */
function withoutTiming(groups: SearchGroup[]): SearchGroup[] {
  return groups.map((item) => ({
    ...item,
    elapsedMs: 0,
    sources: item.sources.map((source) => ({ ...source, elapsedMs: 0 })),
  }))
}

// ---------------------------------------------------------------- 归一化与意图

describe('输入归一化与意图判定', () => {
  it('纯 6 位代码 → code 意图', () => {
    const query = normalizeQuery('600000')
    expect(query.code).toBe('600000')
    expect(classifyIntent(query)).toBe('code')
  })

  it('代码带空格 / 带市场前后缀 → 同一代码', () => {
    for (const raw of ['  600000 ', '600 000', 'sh600000', '600000.SH', '６０００００']) {
      expect(normalizeQuery(raw).code).toBe('600000')
    }
  })

  it('中文名 → name 意图（个股名还是行业名交给数据判定）', () => {
    const query = normalizeQuery('机器人')
    expect(query.code).toBeNull()
    expect(classifyIntent(query)).toBe('name')
  })

  it('混排输入也能抓出串内代码', () => {
    const query = normalizeQuery('600000 浦发')
    expect(query.codes).toEqual(['600000'])
    expect(query.code).toBeNull()
  })

  it('英文关键词 → keyword 意图；空白输入 → empty 意图', () => {
    expect(classifyIntent(normalizeQuery('TCL'))).toBe('keyword')
    expect(classifyIntent(normalizeQuery('   '))).toBe('empty')
    expect(normalizeQuery(undefined as unknown as string).empty).toBe(true)
  })

  it('打分：全等 > 前缀 > 包含 > 不匹配', () => {
    expect(matchScore('机器人', '机器人')).toBe(100)
    expect(matchScore('机器人', '人形机器人组件')).toBe(60)
    expect(matchScore('机器人', '银行')).toBe(0)
  })
})

// ---------------------------------------------------------------- 路由映射

describe('跳转映射', () => {
  it('三类命中的 href 与 URL 编码', () => {
    expect(researchHref('600000')).toBe('/research/600000')
    expect(industryHref('机器人')).toBe('/industry/%E6%9C%BA%E5%99%A8%E4%BA%BA')
    expect(newsHref({ q: '机器人' })).toBe('/news?q=%E6%9C%BA%E5%99%A8%E4%BA%BA')
    expect(newsHref({ code: '600000' })).toBe('/news?code=600000')
    expect(newsHref({})).toBe('/news')
    for (const href of [researchHref('600000'), industryHref('TMT'), newsHref({ q: 'a b' })]) {
      expect(isKnownRouteHref(href)).toBe(true)
    }
    expect(isKnownRouteHref('/nope')).toBe(false)
  })
})

// ---------------------------------------------------------------- 聚合行为

describe('runSearch 分组与跳转', () => {
  const rows = [
    { code: '600000', name: '浦发银行' },
    { code: '601111', name: '中国铁建' },
    { code: '000001', name: null },
  ]

  it('输入 600000 → 个股组命中，跳转 /research/:code', async () => {
    calls.length = 0
    const result = await runSearch('600000', {
      date: DATE,
      sources: [stockSource(rows), themeSource(['机器人']), newsSource([{ id: 'n1', title: '浦发公告', code: '600000' }])],
    })
    const stocks = group(result, 'stocks')
    expect(stocks.status).toBe('ok')
    expect(stocks.hits).toHaveLength(1)
    expect(stocks.hits[0]).toMatchObject({
      key: '600000',
      tag: '个股',
      name: '浦发银行',
      route: '/research/600000',
      sources: ['test_stocks'],
      verified: true,
    })
    expect(result.intent).toBe('code')
    // 资讯组按代码走服务端过滤，不做关键词匹配
    expect(calls.some((item) => item.intent === 'code' && item.query.codes.join() === '600000')).toBe(true)
  })

  it('代码带空格与纯代码结果一致', async () => {
    const sources = [stockSource(rows), themeSource(['机器人']), newsSource([])]
    const plain = await runSearch('600000', { date: DATE, sources })
    const padded = await runSearch('  600 000 ', { date: DATE, sources })
    // `elapsedMs` 是实测耗时，两次调用天然不相等；比较语义内容时归零。
    expect(withoutTiming(padded.groups)).toEqual(withoutTiming(plain.groups))
    expect(padded.query.code).toBe('600000')
  })

  it('输入中文个股名 → 个股组命中，行业组为空但不是 null', async () => {
    const result = await runSearch('浦发', {
      date: DATE,
      sources: [stockSource(rows), themeSource(['机器人']), newsSource([])],
    })
    expect(group(result, 'stocks').hits.map((hit) => hit.key)).toEqual(['600000'])
    expect(group(result, 'themes').status).toBe('empty')
    expect(group(result, 'themes').hits).toEqual([])
    expect(group(result, 'news').hits).toEqual([])
    expect(result.intent).toBe('name')
  })

  it('输入题材名 → 行业 / 题材组命中，跳转 /industry/:name', async () => {
    const result = await runSearch('机器人', {
      date: DATE,
      sources: [
        stockSource(rows),
        themeSource(['人形机器人', '机器人']),
        {
          id: 'test_themes',
          label: '测试题材源',
          group: 'themes',
          load: async () => [{ key: '机器人', tag: '题材', code: null, name: '机器人', detail: '龙头 埃斯顿', score: 100 }],
        },
        newsSource([{ id: 'n2', title: '机器人产业链', code: null }]),
      ],
    })
    const themes = group(result, 'themes')
    expect(themes.status).toBe('ok')
    // 同名题材跨源合并成一条，来源标注保留两个源
    expect(themes.hits.map((hit) => hit.key)).toEqual(['机器人', '人形机器人'])
    expect(themes.hits[0]).toMatchObject({
      tag: '行业',
      route: '/industry/%E6%9C%BA%E5%99%A8%E4%BA%BA',
      sources: ['test_industries', 'test_themes'],
    })
    // 资讯关键词跳转带 query
    expect(group(result, 'news').hits[0]?.route).toBe('/news?q=%E6%9C%BA%E5%99%A8%E4%BA%BA')
    for (const hit of result.groups.flatMap((item) => item.hits)) {
      expect(isKnownRouteHref(hit.route)).toBe(true)
    }
  })

  it('输入资讯关键词 → 资讯组命中，跳转 /news?q=<关键词>', async () => {
    const result = await runSearch('降准', {
      date: DATE,
      sources: [stockSource(rows), themeSource(['机器人']), newsSource([{ id: 'n3', title: '央行降准', code: null }])],
    })
    const news = group(result, 'news')
    expect(news.hits[0]).toMatchObject({ key: 'n3', tag: '资讯', route: '/news?q=%E9%99%8D%E5%87%86' })
    expect(group(result, 'stocks').status).toBe('empty')
  })

  it('无结果 → 三组都是明确的空结构', async () => {
    const result = await runSearch('不存在的东西', {
      date: DATE,
      sources: [stockSource(rows), themeSource(['机器人']), newsSource([])],
    })
    expect(result.groups.map((item) => item.id)).toEqual([...SEARCH_GROUP_ORDER])
    for (const item of result.groups) {
      expect(item.hits).toEqual([])
      expect(item.status).toBe('empty')
      expect(item.error).toBeNull()
    }
  })

  it('空输入：一个请求都不发，三组 skipped', async () => {
    calls.length = 0
    const result = await runSearch('   ', {
      date: DATE,
      sources: [stockSource(rows), themeSource(['机器人']), newsSource([])],
    })
    expect(calls).toHaveLength(0)
    for (const item of result.groups) {
      expect(item.status).toBe('skipped')
      expect(item.hits).toEqual([])
    }
    expect(result.intent).toBe('empty')
  })

  it('每组命中数有上限，避免把整份数据集甩给 UI', async () => {
    const many = Array.from({ length: 30 }, (_, index) => ({
      code: `6000${String(index).padStart(2, '0')}`,
      name: `机器人 ${index}`,
    }))
    const result = await runSearch('机器人', { date: DATE, sources: [stockSource(many)] })
    expect(group(result, 'stocks').hits).toHaveLength(8)
    const limited = await runSearch('机器人', { date: DATE, hitLimit: 2, sources: [stockSource(many)] })
    expect(group(limited, 'stocks').hits).toHaveLength(2)
  })
})

// ---------------------------------------------------------------- 降级

describe('单源失败降级', () => {
  const failing: SearchSource = {
    id: 'broken_theme',
    label: '题材强度',
    group: 'themes',
    load: async () => {
      throw new ApiError('http', '本地接口 /market/theme 返回 HTTP 404', 404, 'Not Found')
    },
  }
  const healthy: SearchSource = {
    id: 'good_theme',
    label: '行业清单',
    group: 'themes',
    load: async () => [{ key: '机器人', tag: '行业', code: null, name: '机器人', detail: null, score: 100 }],
  }

  it('一个源挂了：其它组照常返回，该组标 partial 并带错误标记', async () => {
    const result = await runSearch('机器人', {
      date: DATE,
      sources: [
        failing,
        healthy,
        stockSource([{ code: '300024', name: '机器人' }]),
      ],
    })
    const themes = group(result, 'themes')
    expect(themes.status).toBe('partial')
    expect(themes.error).toBeNull()
    expect(themes.hits).toHaveLength(1)
    const broken = themes.sources.find((item) => item.id === 'broken_theme')
    expect(broken?.outcome).toBe('error')
    expect(broken?.error?.title).toContain('未接入')
    expect(broken?.error?.code).toBe('HTTP 404')
    // 其它组不受影响
    expect(group(result, 'stocks').status).toBe('ok')
    expect(group(result, 'news').status).toBe('skipped')
  })

  it('整组全挂：status=error 且给出「暂不可用」文案', async () => {
    const result = await runSearch('机器人', { date: DATE, sources: [failing] })
    const themes = group(result, 'themes')
    expect(themes.status).toBe('error')
    expect(themes.error?.title).toContain('题材强度')
    expect(themes.hits).toEqual([])
  })

  it('本机 API 没起（not-connected）说成「未接入」而不是「失败」', async () => {
    const offline: SearchSource = {
      id: 'offline',
      label: '连板天梯',
      group: 'stocks',
      load: async () => {
        throw new ApiError('not-connected', '本地接口不可达')
      },
    }
    const result = await runSearch('600000', { date: DATE, sources: [offline] })
    expect(group(result, 'stocks').error?.title).toBe('连板天梯接口未接入')
  })

  it('每个源单独计时，延迟数字留给 UI 与验收', async () => {
    let clock = 0
    const slow: SearchSource = {
      id: 'slow',
      label: '慢源',
      group: 'news',
      load: async () => {
        clock += 120
        return []
      },
    }
    const result = await runSearch('降准', { date: DATE, sources: [slow], now: () => clock })
    expect(group(result, 'news').sources[0]).toMatchObject({ elapsedMs: 120, outcome: 'empty' })
    expect(group(result, 'news').elapsedMs).toBe(120)
  })
})

// ---------------------------------------------------------------- 竞态与取消

describe('请求竞态与取消', () => {
  interface Gate {
    resolve: (items: Candidate[]) => void
  }

  /**
   * 门控源：`load` 挂住直到测试放行，用来手工排两个请求的先后。
   * `honoursSignal` 模拟两种真实源——`request()` 会在 abort 时reject，
   * 和「已经不监听 signal 的旧代码」。
   */
  function gatedSource(honoursSignal = true) {
    const gates = new Map<string, Gate>()
    const source: SearchSource = {
      id: 'gated',
      label: '门控源',
      group: 'stocks',
      load: (context) =>
        new Promise<Candidate[]>((resolve, reject) => {
          gates.set(context.query.text, { resolve })
          if (honoursSignal) {
            context.signal?.addEventListener('abort', () =>
              reject(new ApiError('timeout', `请求已取消：/api/ladder`)),
            )
          }
        }),
    }
    return { source, gates }
  }

  const hit = (code: string, name: string): Candidate => ({
    key: code,
    tag: '个股',
    code,
    name,
    detail: null,
    score: 100,
  })

  it('换输入即丢弃上一轮 in-flight，旧结果标 superseded', async () => {
    const { source, gates } = gatedSource()
    const runner = createSearchRunner({ date: DATE, sources: [source] })

    const first = runner.search('浦发')
    const second = runner.search('机器人')
    expect(runner.current()).toBe(2)

    gates.get('机器人')?.resolve([hit('300024', '机器人')])
    const slow = await first
    expect(slow.superseded).toBe(true)
    expect(slow.aborted).toBe(true)
    expect(group(slow, 'stocks').sources[0].error?.code).toBe('ABORTED')

    const fast = await second
    expect(fast.generation).toBe(2)
    expect(fast.superseded).toBe(false)
    expect(group(fast, 'stocks').hits.map((item) => item.key)).toEqual(['300024'])
  })

  it('源不响应 abort 时，generation 仍然挡住晚到的旧结果', async () => {
    const { source, gates } = gatedSource(false)
    const runner = createSearchRunner({ date: DATE, sources: [source] })

    const first = runner.search('浦发')
    const second = runner.search('机器人')
    // 旧请求最后才回来
    gates.get('浦发')?.resolve([hit('600000', '浦发银行')])
    gates.get('机器人')?.resolve([hit('300024', '机器人')])

    const slow = await first
    expect(slow.generation).toBe(1)
    expect(slow.superseded).toBe(true)
    const fast = await second
    expect(fast.superseded).toBe(false)
    expect(group(fast, 'stocks').hits.map((item) => item.key)).toEqual(['300024'])
  })

  it('cancel() 之后本轮结果整体作废', async () => {
    const { source, gates } = gatedSource()
    const runner = createSearchRunner({ date: DATE, sources: [source] })
    const pending = runner.search('浦发')
    runner.cancel()
    gates.get('浦发')?.resolve([hit('600000', '浦发银行')])
    const result = await pending
    expect(result.superseded).toBe(true)
    expect(result.aborted).toBe(true)
  })

  it('外部 AbortSignal 取消：失败原因标 ABORTED，不写成接口故障', async () => {
    const controller = new AbortController()
    const { source, gates } = gatedSource()
    const running = runSearch('浦发', { date: DATE, sources: [source], signal: controller.signal })
    controller.abort()
    gates.get('浦发')?.resolve([hit('600000', '浦发银行')])
    const result = await running
    expect(result.aborted).toBe(true)
    expect(group(result, 'stocks').status).toBe('error')
    expect(group(result, 'stocks').sources[0].error?.code).toBe('ABORTED')
  })
})

// ---------------------------------------------------------------- 默认源打的是哪些接口

describe('httpSearchSources 的真实端点', () => {
  const LADDER = {
    domain: 'sentiment',
    topic: 'limit_ladder',
    event_id: null,
    data_as_of: DATE,
    source: 'ladderstock',
    source_detail: 'local_state',
    fetched_at: `${DATE}T15:00:00+08:00`,
    freshness: 'today',
    confidence: 'high',
    source_status: 'ok',
    // `api.ladder` 的载荷是 `{ ladder: LadderResult }`（`api.ts:771`），层级表在 LadderResult.ladder 里
    payload: {
      ladder: {
        date: DATE,
        available: true,
        reason: null,
        source_detail: 'local_state',
        height: 3,
        count: 2,
        unknown_board: 0,
        gap: false,
        gap_levels: [],
        leader_candidates: [{ code: '600000', name: '浦发银行', boards: 1, first_seal_time: null, seal_time: null }],
        ladder: [
          {
            boards: 2,
            count: 1,
            stocks: [{ code: '300024', name: '机器人', boards: 2, first_seal_time: null, seal_time: null }],
          },
        ],
      },
    },
  }

  const POOL = {
    payload: {
      date: DATE,
      available: true,
      candidates: [
        { code: '601111', name: '中国铁建', score: 0.8, parts: {}, absent_parts: [], raw: { theme_tags: ['基建'] } },
        { code: '600001', name: '', score: 0.5, parts: {}, absent_parts: [], raw: {} },
      ],
    },
  }

  const INDUSTRY = {
    payload: {
      available: true,
      industries: [
        { name: '机器人', l1_code: '801730', bound: true, enabled: true, available: true, constituent_count: 12, data_source_status: 'ok', reason: null, dimensions_available: {}, metrics: {}, as_of: {} },
        { name: '银行', l1_code: '801780', bound: true, enabled: true, available: true, constituent_count: 42, data_source_status: 'ok', reason: null, dimensions_available: {}, metrics: {}, as_of: {} },
      ],
    },
  }

  const THEME = {
    domain: 'market',
    topic: 'theme',
    event_id: null,
    data_as_of: DATE,
    source: 'ladderstock',
    source_detail: 'wire',
    fetched_at: `${DATE}T15:00:00+08:00`,
    freshness: 'today',
    confidence: 'medium',
    source_status: 'partial',
    payload: {
      date: DATE,
      available: true,
      reason: null,
      themes: [{ theme: '人形机器人', strength: 88, limit_up_count: 5, leader: '埃斯顿', delta: 3 }],
    },
  }

  const NEWS = {
    payload: {
      window: { start: DATE, end: DATE },
      sources: ['cls'],
      codes: ['600000'],
      summary: {},
      sources_status: [],
      extraction: {},
      items: [
        {
          id: 'n-1',
          source: 'cls',
          title: '浦发银行发布三季报',
          url: 'https://example.com/a',
          published_at: `${DATE}T09:00:00+08:00`,
          related_codes: ['600000'],
          summary: '营收同比增长',
          content_hash: 'h1',
          origins: [],
          industries: [],
        },
      ],
      events: [],
      data_source_status: 'ok',
      reason: null,
      data_as_of: DATE,
    },
  }

  const TABLES: Record<string, unknown> = {
    '/api/ladder': LADDER,
    '/api/pool': POOL,
    '/api/industry': INDUSTRY,
    '/api/market/theme': THEME,
    '/api/news': NEWS,
  }

  let urls: string[] = []

  function stubFetch() {
    urls = []
    vi.stubGlobal('fetch', (input: string) => {
      const url = String(input)
      urls.push(url)
      const path = url.split('?')[0]
      const body = TABLES[path]
      if (body === undefined) return Promise.resolve(new Response('{"detail":"Not Found"}', { status: 404 }))
      return Promise.resolve(
        new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }),
      )
    })
  }

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('代码意图：个股 + 资讯按 code 过滤，不打行业清单', async () => {
    stubFetch()
    const result = await runSearch('600000', { date: DATE, sources: httpSearchSources })
    expect(urls.some((url) => url === `/api/ladder?date=${DATE}`)).toBe(true)
    expect(urls.some((url) => url === `/api/pool?date=${DATE}&limit=200`)).toBe(true)
    expect(urls.some((url) => url.includes('/api/news') && url.includes(`code=600000`))).toBe(true)
    expect(urls.filter((url) => url.startsWith('/api/industry')).length).toBe(0)
    expect(urls.filter((url) => url.startsWith('/api/market/theme')).length).toBe(0)

    const stocks = result.groups.find((item) => item.id === 'stocks')
    expect(stocks?.hits.map((hit) => hit.key)).toContain('600000')
    expect(stocks?.hits.find((hit) => hit.key === '600000')).toMatchObject({
      name: '浦发银行',
      route: '/research/600000',
      verified: true,
    })
    expect(stocks?.hits.find((hit) => hit.key === '600001')?.name).toBeUndefined()
    // 资讯命中的跳转用 NewsScreen 现在就读得懂的 ?code=
    const news = result.groups.find((item) => item.id === 'news')
    expect(news?.hits[0]).toMatchObject({ route: '/news?code=600000', name: '浦发银行发布三季报' })
  })

  it('题材意图：行业清单 + 题材强度都打，跳转 /industry/:name', async () => {
    stubFetch()
    const result = await runSearch('机器人', { date: DATE, sources: httpSearchSources })
    expect(urls).toContain('/api/industry')
    expect(urls.some((url) => url === `/api/market/theme?date=${DATE}`)).toBe(true)
    const themes = result.groups.find((item) => item.id === 'themes')
    expect(themes?.hits.map((hit) => hit.key)).toEqual(['机器人', '人形机器人'])
    expect(themes?.hits[0]).toMatchObject({ tag: '行业', route: '/industry/%E6%9C%BA%E5%99%A8%E4%BA%BA' })
    expect(themes?.hits[1]).toMatchObject({ tag: '题材', detail: '龙头 埃斯顿' })
    // 名称匹配用中文个股名同样能命中（300024 机器人 在天梯里）
    const stocks = result.groups.find((item) => item.id === 'stocks')
    expect(stocks?.hits.map((hit) => hit.key)).toContain('300024')
  })

  it('候选池把代码当名称回填的行不会伪装成有名称', async () => {
    stubFetch()
    const result = await runSearch('600001', { date: DATE, sources: httpSearchSources })
    const hit = result.groups.find((item) => item.id === 'stocks')?.hits.find((item) => item.key === '600001')
    expect(hit).toMatchObject({ name: null, route: '/research/600001' })
  })

  it('题材接口还没上（404）→ 行业命中照常返回，题材源标未接入', async () => {
    stubFetch()
    delete TABLES['/api/market/theme']
    try {
      const result = await runSearch('机器人', { date: DATE, sources: httpSearchSources })
      const themes = result.groups.find((item) => item.id === 'themes')
      expect(themes?.status).toBe('partial')
      expect(themes?.hits.map((hit) => hit.key)).toEqual(['机器人'])
      expect(themes?.sources.find((item) => item.id === 'market_theme')?.error?.code).toBe('HTTP 404')
    } finally {
      TABLES['/api/market/theme'] = THEME
    }
  })
})
