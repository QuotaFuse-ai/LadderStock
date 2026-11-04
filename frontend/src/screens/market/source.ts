import {
  api,
  type LadderEnvelope,
  type LadderResult,
  type MarketHistoryPayload,
  type MarketThemePayload,
  type SentimentResult,
} from '../../api'

/**
 * 市场屏的取数出口。真实通道就是 `api` 的四个端点，演示通道是 `./demo.ts`。
 *
 * 两条通道实现同一个接口，屏侧只在「选哪个 source」上分叉一次：
 * 渲染分支、状态推导、空态文案都只有一套，避免演示态与真实态长成两个页面。
 */

export type SentimentEnvelope = LadderEnvelope<{ sentiment: SentimentResult }>
export type LadderPayloadEnvelope = LadderEnvelope<{ ladder: LadderResult }>
export type HistoryEnvelope = LadderEnvelope<MarketHistoryPayload>
export type ThemeEnvelope = LadderEnvelope<MarketThemePayload>

export interface MarketSource {
  sentiment: (date: string, signal?: AbortSignal) => Promise<SentimentEnvelope>
  ladder: (date: string, signal?: AbortSignal) => Promise<LadderPayloadEnvelope>
  history: (date: string, window: number, signal?: AbortSignal) => Promise<HistoryEnvelope>
  theme: (date: string, signal?: AbortSignal) => Promise<ThemeEnvelope>
}

/** 真实通道：`/api/sentiment` `/api/ladder` `/api/market/history` `/api/market/theme`。 */
export const httpMarketSource: MarketSource = {
  sentiment: (date, signal) => api.sentiment(date, signal),
  ladder: (date, signal) => api.ladder(date, signal),
  history: (date, window, signal) => api.marketHistory(date, window, signal),
  theme: (date, signal) => api.marketTheme(date, signal),
}
