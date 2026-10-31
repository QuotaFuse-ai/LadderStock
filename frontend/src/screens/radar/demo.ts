/**
 * R10 四页演示数据（`?demo=1` 显式开启，页面带「演示态」水印）。
 *
 * 形状与 `docs/specs/SPEC-RADAR-PAGES.md` §3–§6 逐键对齐，用途只有两个：
 * 后端接口上线前验证页面结构与五态；截图取证。
 *
 * **绝不在真实通道里回落到这份数据。** 演示源里的持仓增删改会就地重算估值，
 * 那只发生在夹具生成器里——页面本身仍然只渲染接口给的数字，不重算任何指标。
 */

import { ApiError } from '../../api'
import {
  CALENDAR_SECTION_LABEL,
  CALENDAR_SECTION_ORDER,
  type CalendarEntry,
  type CalendarSection,
  type CalendarExtractedEvent,
  type CandidatePoolPayload,
  type EventCalendarPayload,
  type ExtractedEvent,
  type NewsItem,
  type NewsSourceStatus,
  type NewsStreamPayload,
  type PoolCandidate,
  type PositionItem,
  type PositionsPayload,
  type RadarSource,
  type RadarStatus,
} from '../../api/radar'

export type DemoVariant = 'full' | 'partial' | 'empty'

export const DEMO_NEWS_DATE = '2026-10-30'
export const DEMO_MONTH = '2026-10'
export const DEMO_POOL_DATE = '2026-10-30'

// ---------------------------------------------------------------- 资讯流

const NEWS_ITEMS: NewsItem[] = [
  {
    id: 'demo-news-1',
    source: 'flash_cls',
    title: '央行开展 5000 亿元 MLF 操作，中标利率维持不变',
    url: 'https://www.cls.cn/detail/demo-1',
    published_at: '2026-10-30 09:12:00',
    related_codes: [],
    summary:
      '央行公告称，为维护银行体系流动性合理充裕，当日开展 5000 亿元中期借贷便利（MLF）操作，期限 1 年，中标利率维持不变。本月 MLF 到期 3000 亿元，净投放 2000 亿元。',
    content_hash: 'demo-hash-1',
    origins: [
      {
        dataset: 'flash_wallstreetcn',
        title: '央行加量续作 MLF，净投放 2000 亿元',
        url: 'https://wallstreetcn.com/livenews/demo-1',
      },
    ],
    industries: ['银行', '非银金融'],
  },
  {
    id: 'demo-news-2',
    source: 'announcement_cninfo',
    title: '贵州茅台：2026 年第三季度报告',
    url: 'https://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=demo-2',
    published_at: '2026-10-30 08:05:00',
    related_codes: ['600519'],
    summary:
      '公司前三季度实现营业总收入 1234.56 亿元，同比增长 14.2%；归属于上市公司股东的净利润 621.34 亿元，同比增长 15.6%。',
    content_hash: 'demo-hash-2',
    origins: [],
    industries: ['食品饮料'],
  },
  {
    id: 'demo-news-3',
    source: 'announcement_cninfo',
    title: '宁德时代：关于签订重大供货框架协议的公告',
    url: 'https://www.cninfo.com.cn/new/disclosure/detail?stockCode=300750&announcementId=demo-3',
    published_at: '2026-10-30 07:40:00',
    related_codes: ['300750'],
    summary:
      '公司与某海外整车企业签订动力电池供货框架协议，约定 2027–2029 年供货规模不低于 120GWh，具体价格与交付节奏以年度订单为准。',
    content_hash: 'demo-hash-3',
    origins: [],
    industries: ['电力设备'],
  },
  {
    id: 'demo-news-4',
    source: 'news_cctv',
    title: '新闻联播：加快培育新质生产力，推动制造业高质量发展',
    url: 'https://tv.cctv.com/2026/10/29/demo.shtml',
    published_at: '2026-10-29 19:20:00',
    related_codes: [],
    summary:
      '会议指出，要加快推进新型工业化，强化企业创新主体地位，推动传统产业转型升级，培育壮大新兴产业和未来产业。',
    content_hash: 'demo-hash-4',
    origins: [],
    industries: ['机械设备', '电子'],
  },
  {
    id: 'demo-news-5',
    source: 'research_report',
    title: '中芯国际（688981）：先进制程扩产，维持「增持」评级',
    url: 'https://www.eastmoney.com/report/demo-5',
    published_at: '2026-10-29 16:30:00',
    related_codes: ['688981'],
    summary:
      '研报认为公司成熟制程产能利用率回升，先进制程扩产节奏清晰；预计 2027 年资本开支同比持平，维持「增持」评级。',
    content_hash: 'demo-hash-5',
    origins: [],
    industries: ['电子'],
  },
  {
    id: 'demo-news-6',
    source: 'flash_cls',
    title: '沪深两市成交额连续第 12 个交易日突破 1.5 万亿元',
    url: 'https://www.cls.cn/detail/demo-6',
    published_at: '2026-10-30 14:55:00',
    related_codes: [],
    summary: '截至收盘，沪市成交 6821 亿元，深市成交 8347 亿元，两市合计 1.52 万亿元。',
    content_hash: 'demo-hash-6',
    origins: [],
    industries: [],
  },
]

const NEWS_EVENTS: ExtractedEvent[] = [
  {
    id: 'demo-event-1',
    type: 'policy',
    label: '政策发布',
    event_date: '2026-10-30',
    title: '央行加量续作 MLF，净投放 2000 亿元',
    codes: [],
    evidence: {
      snippet: '当日开展 5000 亿元中期借贷便利（MLF）操作……净投放 2000 亿元。',
      url: 'https://www.cls.cn/detail/demo-1',
      matched: ['央行', 'MLF'],
      dataset: 'flash_cls',
    },
    item_id: 'demo-news-1',
    date_basis: 'published_at',
    extractor: 'rule:v1',
    ai_note: null,
  },
  {
    id: 'demo-event-2',
    type: 'contract_award',
    label: '重大合同',
    event_date: '2026-10-30',
    title: '宁德时代签订动力电池供货框架协议',
    codes: ['300750'],
    evidence: {
      snippet: '签订动力电池供货框架协议，约定 2027–2029 年供货规模不低于 120GWh。',
      url: 'https://www.cninfo.com.cn/new/disclosure/detail?stockCode=300750&announcementId=demo-3',
      matched: ['供货框架协议', '120GWh'],
      dataset: 'announcement_cninfo',
    },
    item_id: 'demo-news-3',
    date_basis: 'published_at',
    extractor: 'rule:v1',
    ai_note: null,
  },
]

function newsSources(variant: DemoVariant): NewsSourceStatus[] {
  const rows: NewsSourceStatus[] = [
    {
      dataset: 'announcement_cninfo',
      tool: 'tickhub_cn.announcement_cninfo',
      status: 'ok',
      items: 2,
      detail: '',
      source: 'tickhub',
      source_detail: '巨潮资讯 · 当日公告',
      freshness: 'realtime',
      confidence: 'high',
      degraded: false,
    },
    {
      dataset: 'flash_cls',
      tool: 'quotaftuse_wire.flash_cls',
      status: 'ok',
      items: 2,
      detail: '',
      source: 'wire',
      source_detail: '财联社电报',
      freshness: 'realtime',
      confidence: 'high',
      degraded: false,
    },
    {
      dataset: 'news_cctv',
      tool: 'tickhub_cn.news_cctv',
      status: 'ok',
      items: 1,
      detail: '',
      source: 'tickhub',
      source_detail: '新闻联播文字稿',
      freshness: 'daily',
      confidence: 'high',
      degraded: false,
    },
    {
      dataset: 'research_report',
      tool: 'tickhub_cn.research_report',
      status: 'ok',
      items: 1,
      detail: '',
      source: 'tickhub',
      source_detail: '东财研报',
      freshness: 'daily',
      confidence: 'medium',
      degraded: false,
    },
  ]
  if (variant === 'full') return rows
  return rows.map((row) =>
    row.dataset === 'research_report'
      ? {
          ...row,
          status: 'error',
          items: 0,
          detail: '研报上游 502，已跳过本栏目',
          degraded: true,
        }
      : row,
  )
}

export function demoNews(
  variant: DemoVariant,
  query: { date?: string; until?: string | null; codes?: string[] | null } = {},
): NewsStreamPayload {
  const codes = (query.codes ?? []).filter(Boolean)
  const start = query.date ?? DEMO_NEWS_DATE
  const end = query.until ?? start
  const dayOf = (item: NewsItem) => (item.published_at ?? '').slice(0, 10)
  const items =
    variant === 'empty'
      ? []
      : NEWS_ITEMS.filter((item) => {
          if (codes.length > 0 && !item.related_codes.some((code) => codes.includes(code))) return false
          const day = dayOf(item)
          return day !== '' && day >= start && day <= end
        })
  const itemIds = new Set(items.map((item) => item.id))
  const events = variant === 'empty' ? [] : NEWS_EVENTS.filter((event) => itemIds.has(event.item_id))
  const status: RadarStatus =
    variant === 'empty' ? 'missing' : variant === 'partial' ? 'partial' : 'ok'

  return {
    window: { start, end },
    sources: ['announcement_cninfo', 'flash_cls', 'news_cctv', 'research_report'],
    codes,
    summary: {
      items: items.length,
      fetched: 16,
      new_items: items.length,
      known_items: 0,
      merged_duplicates: 3,
      dropped_undated: 0,
      outside_window: 0,
      stored: items.length,
      events: events.length,
      persisted: true,
    },
    sources_status: variant === 'empty' ? newsSources('partial').map((row) => ({ ...row, status: 'error', items: 0, detail: '未连接上游' })) : newsSources(variant),
    extraction: {
      events: events.length,
      by_type: events.reduce<Record<string, number>>((acc, event) => {
        acc[event.type] = (acc[event.type] ?? 0) + 1
        return acc
      }, {}),
      skipped_no_url: 0,
      skipped_excluded_source: 0,
      suppressed_by_negation: 0,
    },
    items,
    events,
    data_source_status: status,
    reason:
      status === 'missing'
        ? '所有资讯栏目都不可用：上游未连接'
        : status === 'partial'
          ? '研究报告：研报上游 502，已跳过本栏目'
          : null,
    data_as_of: DEMO_NEWS_DATE,
  }
}

// ---------------------------------------------------------------- 事件日历

const CALENDAR_ENTRIES: Record<string, CalendarEntry[]> = {
  lockup_expiry: [
    {
      id: 'demo-lockup-1',
      date: '2026-10-20',
      code: '600519',
      section: 'lockup_expiry',
      label: '限售解禁：首发原股东限售股份',
      detail: '可流通 120 万股；占总股本 0.09%',
      source: 'lockup_calendar',
      provenance: { dataset: 'lockup_calendar', as_of: '2026-10-19' },
    },
    {
      id: 'demo-lockup-2',
      date: '2026-10-28',
      code: '300750',
      section: 'lockup_expiry',
      label: '限售解禁：定增机构配售股份',
      detail: '可流通 860 万股；占总股本 0.20%',
      source: 'lockup_calendar',
      provenance: { dataset: 'lockup_calendar', as_of: '2026-10-27' },
    },
  ],
  earnings_preview: [
    {
      id: 'demo-preview-1',
      date: '2026-10-15',
      code: '688981',
      section: 'earnings_preview',
      label: '业绩预告：预增',
      detail: '预计前三季度净利润同比增长 45%–60%',
      source: 'earnings_preview',
      provenance: { dataset: 'earnings_preview', as_of: '2026-10-14' },
    },
  ],
}

const CALENDAR_EXTRACTED: CalendarExtractedEvent[] = [
  {
    id: 'demo-calendar-event-1',
    type: 'shareholder_meeting',
    label: '股东大会',
    event_date: '2026-10-28',
    date: '2026-10-28',
    title: '贵州茅台：2026 年第一次临时股东大会决议公告',
    codes: ['600519'],
    evidence: {
      snippet: '公司 2026 年第一次临时股东大会于 2026 年 10 月 28 日以现场结合网络投票方式召开……',
      url: 'https://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=demo-meeting',
      matched: ['股东大会'],
      dataset: 'announcement_cninfo',
    },
    item_id: '',
    date_basis: 'evidence_text',
    extractor: 'rule:v1',
    ai_note: null,
    basis: 'rule_extracted',
    note: '规则从公告正文抽取，非日历数据源；日期以正文表述为准。',
  },
  {
    id: 'demo-calendar-event-2',
    type: 'policy',
    label: '政策发布',
    event_date: '2026-10-15',
    date: '2026-10-15',
    title: '央行：下调支农支小再贷款利率 0.25 个百分点',
    codes: [],
    evidence: {
      snippet: '自 2026 年 10 月 15 日起，下调支农支小再贷款利率 0.25 个百分点。',
      url: 'https://www.cls.cn/detail/demo-policy',
      matched: ['再贷款', '下调'],
      dataset: 'flash_cls',
    },
    item_id: '',
    date_basis: 'evidence_text',
    extractor: 'rule:v1',
    ai_note: null,
    basis: 'rule_extracted',
    note: '规则从正文抽取生效日期，非日历数据源。',
  },
]

const NO_SOURCE_REASON: Record<string, { reason: string; ask: string }> = {
  earnings_disclosure: {
    reason: '上游没有「财报预约披露时间表」工具，无法给出确切披露日期。',
    ask: '上游需求：TickHub-CN 增加 disclosure_schedule 数据集（预约披露日 + 变更标记）。',
  },
  shareholder_meeting: {
    reason: '公告正文里的股东大会日期没有结构化字段，无法稳定进入日历。',
    ask: '上游需求：公告抽取增加 meeting_date 字段（含临时/年度区分）。',
  },
  policy_window: {
    reason: '政策发布没有固定日历，只有事件发生后才能确认。',
    ask: '上游需求：QuotaFuse Wire 提供 policy_calendar 数据集（会议/窗口期）。',
  },
  lockup_expiry: {
    reason: '本机没有解禁日历缓存，本轮不估算日期。',
    ask: '上游需求：TickHub-CN lockup_calendar 数据集落地。',
  },
  earnings_preview: {
    reason: '本机没有业绩预告缓存，本轮不估算日期。',
    ask: '上游需求：TickHub-CN earnings_preview 数据集落地。',
  },
}

function buildSections(variant: DemoVariant): CalendarSection[] {
  return CALENDAR_SECTION_ORDER.map((key) => {
    const sourced = variant !== 'empty' && (key === 'lockup_expiry' || key === 'earnings_preview')
    if (!sourced) {
      return {
        key,
        label: CALENDAR_SECTION_LABEL[key] ?? key,
        status: 'no_source' as const,
        entries: [],
        note: '暂无数据源',
        data_source: '',
        tool: '',
        degraded: false,
        reason: NO_SOURCE_REASON[key]?.reason ?? '暂无数据源。',
        ask: NO_SOURCE_REASON[key]?.ask ?? '需要上游提供结构化日历数据集。',
      }
    }
    return {
      key,
      label: CALENDAR_SECTION_LABEL[key] ?? key,
      status: 'sourced' as const,
      entries: CALENDAR_ENTRIES[key] ?? [],
      note: '',
      data_source: key,
      tool: `tickhub_cn.${key}`,
      degraded: variant === 'partial' && key === 'lockup_expiry',
      reason: '',
      ask: '',
    }
  })
}

function monthWindow(month: string): { start: string; end: string } {
  const [year, index] = month.split('-').map(Number)
  if (!year || !index) return { start: `${month}-01`, end: `${month}-31` }
  const last = new Date(year, index, 0).getDate()
  return { start: `${month}-01`, end: `${month}-${String(last).padStart(2, '0')}` }
}

export function demoCalendar(
  variant: DemoVariant,
  query: { month?: string; codes?: string[] | null } = {},
): EventCalendarPayload {
  const month = query.month ?? DEMO_MONTH
  const codes = (query.codes ?? []).filter(Boolean)
  const keep = (code: string) => codes.length === 0 || codes.includes(code)
  const inMonth = (date: string) => date.startsWith(month)
  const sections = buildSections(variant).map((section) => ({
    ...section,
    entries: section.entries.filter((entry) => keep(entry.code) && inMonth(entry.date)),
  }))
  const extracted =
    variant === 'empty'
      ? []
      : CALENDAR_EXTRACTED.filter(
          (event) => (codes.length === 0 || event.codes.some(keep)) && inMonth(event.date),
        )
  const status: RadarStatus =
    variant === 'empty' ? 'missing' : variant === 'partial' ? 'partial' : 'ok'

  return {
    month,
    window: monthWindow(month),
    codes,
    sections,
    extracted_events: extracted,
    data_source_status: status,
    reason:
      status === 'partial'
        ? '解禁日历缓存 2 天前，可能不含最新公告'
        : status === 'missing'
          ? '五个日历小节都没有数据源'
          : null,
    data_as_of: `${month}-01`,
  }
}

// ---------------------------------------------------------------- 候选池

const POOL_CANDIDATES: PoolCandidate[] = [
  {
    code: '600001',
    name: '甲科技',
    score: 91.0,
    parts: { strength: 83.3, standing: 100.0, capital: 100.0, theme: 100.0, turnover: 60.0 },
    absent_parts: [],
    raw: {
      boards: 5,
      turnover_rate: 12.0,
      height: 5,
      net_inflow: 30_000_000,
      theme_tags: ['机器人'],
      theme_board_change_pct: 8.0,
    },
  },
  {
    code: '600002',
    name: '乙材料',
    score: 84.2,
    parts: { strength: 66.7, standing: 100.0, capital: 100.0, theme: 0.0, turnover: 80.0 },
    absent_parts: [],
    raw: {
      boards: 4,
      turnover_rate: 16.0,
      height: 4,
      net_inflow: 26_000_000,
      theme_tags: ['锂电'],
      theme_board_change_pct: -1.2,
    },
  },
  {
    code: '600003',
    name: '丙智造',
    score: 72.5,
    parts: { strength: 50.0, standing: 50.0, capital: 75.0, theme: 50.0, turnover: null },
    absent_parts: ['turnover'],
    raw: {
      boards: 3,
      turnover_rate: null,
      height: 3,
      net_inflow: 12_000_000,
      theme_tags: ['机器人'],
      theme_board_change_pct: 8.0,
    },
  },
  {
    code: '600004',
    name: '丁电子',
    score: 61.0,
    parts: { strength: 33.3, standing: 50.0, capital: 50.0, theme: 66.7, turnover: 40.0 },
    absent_parts: [],
    raw: {
      boards: 2,
      turnover_rate: 8.0,
      height: 2,
      net_inflow: 8_000_000,
      theme_tags: ['消费电子'],
      theme_board_change_pct: 3.1,
    },
  },
  {
    code: '600005',
    name: '戊环保',
    score: 55.5,
    parts: { strength: 16.7, standing: 0.0, capital: 50.0, theme: 66.7, turnover: 20.0 },
    absent_parts: [],
    raw: {
      boards: 1,
      turnover_rate: 4.0,
      height: 1,
      net_inflow: 5_000_000,
      theme_tags: ['环保'],
      theme_board_change_pct: 2.4,
    },
  },
]

export function demoPool(variant: DemoVariant, query: { date?: string } = {}): CandidatePoolPayload {
  const date = query.date ?? DEMO_POOL_DATE
  if (variant !== 'empty' && date !== DEMO_POOL_DATE) {
    return {
      date,
      available: false,
      reason: `本机没有 ${date} 的涨停池快照，候选池无法生成。`,
      source_detail: '',
      height: null,
      candidate_count: 0,
      candidates: [],
      weights: {},
      formula: '',
      counts: {
        limit_pool_rows: 0,
        dropped_duplicates: 0,
        unknown_code: 0,
        theme_boards: 0,
        fund_flow_rows: 0,
      },
      missing: ['limit_pool'],
      data_source_status: 'missing',
      data_as_of: date,
    }
  }
  if (variant === 'empty') {
    return {
      date,
      available: false,
      reason: '本机没有 2026-10-30 的涨停池快照，候选池无法生成。',
      source_detail: '',
      height: null,
      candidate_count: 0,
      candidates: [],
      weights: {},
      formula: '',
      counts: {
        limit_pool_rows: 0,
        dropped_duplicates: 0,
        unknown_code: 0,
        theme_boards: 0,
        fund_flow_rows: 0,
      },
      missing: ['limit_pool'],
      data_source_status: 'missing',
      data_as_of: date,
    }
  }
  return {
    date,
    available: true,
    reason: variant === 'partial' ? '资金流数据集缺失，资金分项不参与计算' : null,
    source_detail: 'limit_pool: archive（7 条）; concept_board: 3 条; fund_flow: 5 条',
    height: 5,
    candidate_count: POOL_CANDIDATES.length,
    candidates: POOL_CANDIDATES,
    weights: { strength: 0.3, standing: 0.25, capital: 0.2, theme: 0.15, turnover: 0.1 },
    formula: 'score = Σ(weight_i × part_i) / Σ(weight_i)，缺失分项不计入分母',
    counts: {
      limit_pool_rows: 7,
      dropped_duplicates: 1,
      unknown_code: 0,
      theme_boards: 3,
      fund_flow_rows: variant === 'partial' ? 0 : 5,
    },
    missing: variant === 'partial' ? ['fund_flow'] : [],
    data_source_status: variant === 'partial' ? 'partial' : 'ok',
    data_as_of: DEMO_POOL_DATE,
  }
}

// ---------------------------------------------------------------- 我的持仓

function position(
  code: string,
  industry: string,
  cost: number,
  qty: number,
  entry_date: string,
  latest: number | null,
): PositionItem {
  const market_value = latest === null ? null : latest * qty
  const totalCost = cost * qty
  return {
    code,
    cost,
    qty,
    entry_date,
    industry,
    note: '',
    latest_price: latest,
    market_value,
    unrealized_pnl: market_value === null ? null : market_value - totalCost,
    unrealized_pnl_pct: market_value === null ? null : ((market_value - totalCost) / totalCost) * 100,
  }
}

const POSITION_ITEMS: PositionItem[] = [
  position('600000', '银行', 10.0, 1000, '2026-10-01', 11.0),
  position('600519', '食品饮料', 1500.0, 10, '2026-09-12', 1620.0),
  position('300750', '电力设备', 200.0, 50, '2026-10-20', 210.0),
]

function round(value: number, digits = 2): number {
  const factor = 10 ** digits
  return Math.round(value * factor) / factor
}

/**
 * 演示夹具专用：按当前条目重算估值 / 集中度 / 行业分布。
 * 真实页面从不做这件事——只渲染 `GET /api/positions` 的载荷。
 */
function positionsFrom(items: PositionItem[]): PositionsPayload {
  const total_cost = items.reduce((sum, item) => sum + item.cost * item.qty, 0)
  const priced = items.filter((item) => item.market_value !== null)
  const allPriced = priced.length === items.length && items.length > 0
  const priced_cost = priced.reduce((sum, item) => sum + item.cost * item.qty, 0)
  const market_value = allPriced ? priced.reduce((sum, item) => sum + (item.market_value ?? 0), 0) : null
  const unrealized_pnl =
    market_value === null
      ? priced.reduce((sum, item) => sum + (item.unrealized_pnl ?? 0), 0)
      : market_value - total_cost
  const pnl_base = market_value === null ? priced_cost : total_cost
  const basis: 'cost' | 'market_value' = allPriced ? 'market_value' : 'cost'
  const weights = items.map((item) => ({
    code: item.code,
    weight: basis === 'market_value' ? (item.market_value ?? 0) : item.cost * item.qty,
    cost: item.cost * item.qty,
  }))
  const denominator = weights.reduce((sum, row) => sum + row.weight, 0)
  const ranked = [...weights].sort((a, b) => b.weight - a.weight)

  const sectors = new Map<string, number>()
  const unlabeled: PositionItem[] = []
  for (const item of items) {
    if (!item.industry) {
      unlabeled.push(item)
      continue
    }
    const weight = basis === 'market_value' ? (item.market_value ?? 0) : item.cost * item.qty
    sectors.set(item.industry, (sectors.get(item.industry) ?? 0) + weight)
  }
  const industries = [...sectors.entries()]
    .map(([industry, weight]) => ({
      industry,
      weight_pct: denominator > 0 ? round((weight / denominator) * 100) : 0,
      cost: round(
        items
          .filter((item) => item.industry === industry)
          .reduce((sum, item) => sum + item.cost * item.qty, 0),
      ),
    }))
    .sort((a, b) => b.weight_pct - a.weight_pct)

  return {
    local_only: true,
    store: 'local_sqlite',
    data_source_status: allPriced ? 'ok' : 'partial',
    as_of: DEMO_POOL_DATE,
    price_source: 'daily_k',
    position_count: items.length,
    valuation: {
      total_cost: round(total_cost),
      market_value: market_value === null ? null : round(market_value),
      priced_cost: round(priced_cost),
      unpriced_cost: round(total_cost - priced_cost),
      coverage_pct: total_cost > 0 ? round((priced_cost / total_cost) * 100, 1) : null,
      unrealized_pnl: items.length === 0 ? null : round(unrealized_pnl),
      unrealized_pnl_pct:
        items.length === 0 || pnl_base <= 0 ? null : round((unrealized_pnl / pnl_base) * 100),
    },
    items,
    concentration: {
      available: items.length > 0,
      reason: items.length > 0 ? null : '没有可计算的持仓',
      basis,
      top1_pct: items.length > 0 ? round((ranked[0].weight / denominator) * 100) : null,
      top3_pct:
        items.length > 0
          ? round((ranked.slice(0, 3).reduce((sum, row) => sum + row.weight, 0) / denominator) * 100)
          : null,
      hhi:
        items.length > 0
          ? round(
              weights.reduce((sum, row) => sum + (row.weight / denominator) ** 2, 0),
              4,
            )
          : null,
      holdings: ranked.map((row) => ({
        code: row.code,
        weight_pct: round((row.weight / denominator) * 100),
        cost: round(row.cost),
      })),
    },
    sector_distribution: {
      available: items.length > 0,
      reason: items.length > 0 ? null : '没有可计算的持仓',
      basis,
      labeled_count: items.length - unlabeled.length,
      unlabeled_count: unlabeled.length,
      industries,
    },
  }
}

export function demoPositions(variant: DemoVariant): PositionsPayload {
  if (variant === 'empty') return positionsFrom([])
  if (variant === 'partial') {
    return positionsFrom([
      POSITION_ITEMS[0],
      POSITION_ITEMS[1],
      { ...POSITION_ITEMS[2], latest_price: null, market_value: null, unrealized_pnl: null, unrealized_pnl_pct: null },
    ])
  }
  return positionsFrom(POSITION_ITEMS)
}

// ---------------------------------------------------------------- 演示源

export function demoSource(variant: DemoVariant): RadarSource {
  let holdings: PositionItem[] = demoPositions(variant).items

  return {
    news: async (query) => demoNews(variant, query),
    calendar: async (query) => demoCalendar(variant, query),
    pool: async (query) => demoPool(variant, query),
    positions: async () => positionsFrom(holdings),
    addPosition: async (input) => {
      const code = input.code.trim()
      if (!code) throw new ApiError('http', '缺少代码', 422, 'code 不能为空')
      if (holdings.some((item) => item.code === code)) {
        throw new ApiError('http', `持仓里已经有 ${code}`, 409, '代码重复，请改用「编辑」')
      }
      const item = position(code, input.industry ?? '', input.cost, input.qty, input.entry_date ?? DEMO_POOL_DATE, null)
      item.note = input.note ?? ''
      holdings = [...holdings, item]
      return { ok: true, local_only: true, store: 'local_sqlite', position: item }
    },
    updatePosition: async (code, patch) => {
      const index = holdings.findIndex((item) => item.code === code)
      if (index < 0) throw new ApiError('http', `持仓里没有 ${code}`, 404, '代码不存在')
      const current = holdings[index]
      const next = position(
        current.code,
        patch.industry ?? current.industry,
        patch.cost ?? current.cost,
        patch.qty ?? current.qty,
        patch.entry_date ?? current.entry_date ?? DEMO_POOL_DATE,
        current.latest_price,
      )
      next.note = patch.note ?? current.note
      holdings = holdings.map((item, position) => (position === index ? next : item))
      return { ok: true, local_only: true, store: 'local_sqlite', position: next }
    },
    removePosition: async (code) => {
      const current = holdings.find((item) => item.code === code)
      if (!current) throw new ApiError('http', `持仓里没有 ${code}`, 404, '代码不存在')
      holdings = holdings.filter((item) => item.code !== code)
      return {
        ok: true,
        local_only: true,
        store: 'local_sqlite',
        removed: { code, cost: current.cost, qty: current.qty },
      }
    },
  }
}
