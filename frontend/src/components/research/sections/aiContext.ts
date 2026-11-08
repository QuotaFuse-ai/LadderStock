import type {
  EarningsBriefSummary,
  EventStudySummary,
  ResearchOverview,
} from '../../../api/research'
import {
  describeResearchError,
  httpResearchSource,
  loadEarningsBriefSummary,
  loadEventStudySummary,
} from '../../../api/research'
import { isApiError } from '../../../api'

/**
 * 研究助手的**页级上下文白名单**（QUOT-454）。
 *
 * 只允许页面已经展示、且不属于私有的字段进入这里：代码 / 名称 / 研究日 / 视图、
 * 行情快照、K 线区间、最近一期财务指标、财报期次与一致预期、事件条数。
 * 论点正文、持仓、令牌、密钥、prompt 一律不在此列——新增字段前先问一句
 * 「它已经渲染在页面上了吗、它是私有的吗」，两个答案都是「是」才允许加。
 *
 * `buildAssistantContext()` 是唯一的组装入口；抽屉里的 JSON 预览与发给后端的
 * `context` 字符串都来自同一个对象，避免「展示的上下文」和「真正发出去的上下文」
 * 各写一份。
 */

export interface AssistantContext {
  page: '个股深挖'
  code: string
  name: string | null
  date: string
  tab: string
  quote: {
    price: number | null
    prevClose: number | null
    changePct: number | null
    asOf: string | null
  } | null
  kline: {
    bars: number
    start: string | null
    end: string | null
    firstClose: number | null
    lastClose: number | null
    lowClose: number | null
    highClose: number | null
  } | null
  financials: {
    reportName: string | null
    reportDate: string | null
    eps: number | null
    revenue: number | null
    netProfit: number | null
    roe: number | null
    revenueYoy: number | null
    profitYoy: number | null
  } | null
  earnings: {
    periods: number
    latestReportDate: string | null
    reportDates: string[]
    withForecast: number
    consensus: {
      asOf: string | null
      analystCount: number | null
      epsCurrentYear: number | null
      epsNextYear: number | null
    } | null
  } | null
  events: {
    total: number
    types: Array<{ label: string; count: number }>
  } | null
  /** 页面确实没取到的项（数据源原因，不含传输错误与私有字段） */
  unavailable: string[]
}

export interface AssistantPageData {
  overview: ResearchOverview | null
  earnings: EarningsBriefSummary | null
  events: EventStudySummary | null
  /** 只在抽屉里提示，不进入发给模型的上下文 */
  issues: string[]
}

export interface AssistantContextInput {
  code: string
  name: string | null
  date: string
  tab: string
  overview: ResearchOverview | null
  earnings: EarningsBriefSummary | null
  events: EventStudySummary | null
}

function klineSummary(overview: ResearchOverview): AssistantContext['kline'] {
  const bars = overview.kline.bars.filter((bar) => bar.close !== null)
  if (bars.length === 0) return null
  const closes = bars.map((bar) => bar.close as number)
  return {
    bars: overview.kline.count,
    start: bars[0].date,
    end: bars[bars.length - 1].date,
    firstClose: closes[0],
    lastClose: closes[closes.length - 1],
    lowClose: Math.min(...closes),
    highClose: Math.max(...closes),
  }
}

export function buildAssistantContext(input: AssistantContextInput): AssistantContext {
  const { overview, earnings, events } = input
  const unavailable: string[] = []

  if (overview?.kline.reason) unavailable.push(`K 线：${overview.kline.reason}`)
  if (overview?.quote.reason) unavailable.push(`行情：${overview.quote.reason}`)
  if (overview?.financials.reason) unavailable.push(`财务指标：${overview.financials.reason}`)
  if (earnings?.consensusReason) unavailable.push(`一致预期：${earnings.consensusReason}`)
  if (earnings?.earningsReason) unavailable.push(`财报明细：${earnings.earningsReason}`)

  const quote = overview?.quote.quote ?? null
  const financials = overview?.financials.financials ?? null

  return {
    page: '个股深挖',
    code: input.code,
    name: input.name ?? overview?.name ?? null,
    date: input.date,
    tab: input.tab,
    quote: quote
      ? {
          price: quote.price,
          prevClose: quote.prev_close,
          changePct: quote.change_pct,
          asOf: quote.timestamp ?? overview?.as_of ?? null,
        }
      : null,
    kline: overview ? klineSummary(overview) : null,
    financials: financials
      ? {
          reportName: financials.report_name,
          reportDate: financials.report_date,
          eps: financials.eps,
          revenue: financials.revenue,
          netProfit: financials.net_profit,
          roe: financials.roe,
          revenueYoy: financials.revenue_yoy,
          profitYoy: financials.profit_yoy,
        }
      : null,
    earnings: earnings
      ? {
          periods: earnings.periods,
          latestReportDate: earnings.latestReportDate,
          reportDates: earnings.reportDates,
          withForecast: earnings.withForecast,
          consensus: earnings.consensus,
        }
      : null,
    events: events
      ? {
          total: events.total,
          types: events.types.map((item) => ({ label: item.label, count: item.count })),
        }
      : null,
    unavailable,
  }
}

/** 发给后端的就是这份 JSON；抽屉里的「查看上下文」读的也是它。 */
export function serializeAssistantContext(context: AssistantContext): string {
  return JSON.stringify(context, null, 2)
}

function shortReason(error: unknown): string {
  const detail = describeResearchError(error).detail
  return detail.length > 120 ? `${detail.slice(0, 120)}…` : detail
}

/**
 * 打开抽屉时并行取三份只读摘要。任何一份失败都不影响问答本身：
 * 失败项记进 `issues`，上下文里对应字段保持 `null`，模型据此说「没取到」。
 */
export async function loadAssistantPageData(
  code: string,
  signal: AbortSignal,
  presetOverview?: ResearchOverview | null,
): Promise<AssistantPageData> {
  const [overviewResult, earningsResult, eventResult] = await Promise.allSettled([
    presetOverview ? Promise.resolve(presetOverview) : httpResearchSource.overview(code, signal),
    loadEarningsBriefSummary(code, signal),
    loadEventStudySummary(code, signal),
  ])

  const issues: string[] = []
  if (overviewResult.status === 'rejected') {
    issues.push(`行情 / 财务摘要未取到：${shortReason(overviewResult.reason)}`)
  }
  if (earningsResult.status === 'rejected') {
    issues.push(`财报前瞻未取到：${shortReason(earningsResult.reason)}`)
  }
  if (eventResult.status === 'rejected') {
    issues.push(`事件回看未取到：${shortReason(eventResult.reason)}`)
  }

  return {
    overview: overviewResult.status === 'fulfilled' ? overviewResult.value : null,
    earnings: earningsResult.status === 'fulfilled' ? earningsResult.value : null,
    events: eventResult.status === 'fulfilled' ? eventResult.value : null,
    issues,
  }
}

export type AssistantFailureKind =
  | 'offline'
  | 'no-key'
  | 'channel'
  | 'timeout'
  | 'quota'
  | 'upstream'
  | 'invalid'

export interface AssistantFailureInfo {
  kind: AssistantFailureKind
  title: string
  detail: string
  code: string | null
  retryable: boolean
}

/**
 * 把助手请求的异常翻译成「结论 + 原因 + 能不能重试」。
 *
 * 四种降级各有各的说法，不能都写「请求失败」：本机没连、没配 Key、
 * 模型超时、上游/额度问题，用户要做的事完全不同。
 */
export function describeAssistantError(error: unknown): AssistantFailureInfo {
  if (isApiError(error)) {
    if (error.kind === 'not-connected') {
      return {
        kind: 'offline',
        title: '本机服务未连接',
        detail: '研究助手通过本机 API 转发模型请求；启动 ldst run 后重试，页面数据不会因此丢失。',
        code: null,
        retryable: true,
      }
    }
    if (error.kind === 'timeout') {
      return {
        kind: 'timeout',
        title: '回答超时',
        detail: '模型通道超过 90 秒没有返回；可以重试，或把问题问得更短一些。',
        code: 'TIMEOUT',
        retryable: true,
      }
    }
    if (error.status === 422) {
      return {
        kind: 'invalid',
        title: '问题无法提交',
        detail: error.detail ?? '问题或页级上下文超出长度限制，请缩短后重试。',
        code: 'HTTP 422',
        retryable: false,
      }
    }
    if (error.status === 403) {
      // 来源闸门（写方法必须有本机页面 Origin 或显式头）：页内重试通常还是 403，
      // 所以不给重试按钮，把服务的原话带出来
      return {
        kind: 'invalid',
        title: '请求来源校验未通过',
        detail:
          error.detail ??
          '写请求缺少来源证据：需要本机页面 Origin 或显式客户端头。请从 LadderStock 页面内重新打开后再问。',
        code: 'HTTP 403',
        retryable: false,
      }
    }
    if (error.status === 503) {
      const detail = error.detail ?? '模型通道不可用'
      if (detail.includes('未配置') || detail.includes('缺少')) {
        return {
          kind: 'no-key',
          title: '研究助手未配置模型',
          detail,
          code: 'HTTP 503',
          retryable: true,
        }
      }
      return {
        kind: 'channel',
        title: 'AI 通道不可用',
        detail,
        code: 'HTTP 503',
        retryable: true,
      }
    }
    if (error.status === 504) {
      return {
        kind: 'timeout',
        title: '回答超时',
        detail: error.detail ?? '模型通道超时；可以重试，或把问题问得更短一些。',
        code: 'HTTP 504',
        retryable: true,
      }
    }
    if (error.status === 502) {
      return {
        kind: 'upstream',
        title: 'AI 请求未送达',
        detail: error.detail ?? '模型请求没有送达或上游返回错误（网络 / 代理 / 上游服务）；可以重试。',
        code: 'HTTP 502',
        retryable: true,
      }
    }
    if (error.status === 429) {
      return {
        kind: 'quota',
        title: '模型额度用尽',
        detail: error.detail ?? '上游模型返回额度限制；稍后重试或更换模型通道。',
        code: 'HTTP 429',
        retryable: true,
      }
    }
    if (error.kind === 'parse') {
      return {
        kind: 'upstream',
        title: 'AI 回答解析失败',
        detail: error.message,
        code: 'E_PARSE',
        retryable: true,
      }
    }
    return {
      kind: 'upstream',
      title: 'AI 回答失败',
      detail: error.detail ?? error.message,
      code: error.status ? `HTTP ${error.status}` : null,
      retryable: true,
    }
  }
  return {
    kind: 'upstream',
    title: 'AI 回答失败',
    detail: error instanceof Error ? error.message : String(error),
    code: null,
    retryable: true,
  }
}
