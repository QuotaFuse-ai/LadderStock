/**
 * 答案块渲染（任务书阶段 E）。
 *
 * 页面只接 `BlockRenderer`；块组件与协议类型从这里按需取，不要绕过渲染器自己判 type。
 */
export { BlockRenderer, type BlockRendererProps } from './BlockRenderer'
export {
  ANSWER_FLOW,
  BlockShell,
  EmptyAnswerPanel,
  InvalidBlockPanel,
  UnreachableActionPanel,
} from './BlockShell'
export { ConclusionBlock } from './ConclusionBlock'
export { MetricBlock, formatMetricValue } from './MetricBlock'
export { ChartBlock } from './ChartBlock'
export { TableBlock } from './TableBlock'
export { CitationBlock } from './CitationBlock'
export { NoticeBlock, NOTICE_KINDS, asNoticeKind, type NoticeBlockProps } from './NoticeBlock'
export { ActionBlock } from './ActionBlock'
export { isKnownTarget } from './targets'
export {
  BACKEND_MAX_BLOCKS,
  BLOCK_TYPES,
  MAX_BLOCKS,
  MAX_CHART_POINTS,
  MAX_METRIC_ITEMS,
  MAX_TABLE_ROWS,
  TABLE_COLLAPSE_ROWS,
  parseBlock,
  parseBlocks,
  type ActionBlockPayload,
  type ActionItem,
  type ActionKey,
  type AnswerBlock,
  type BlockType,
  type ChartBlockPayload,
  type ChartKind,
  type ChartSeries,
  type CitationBlockPayload,
  type CitationItem,
  type ConclusionBlockPayload,
  type ConclusionTone,
  type InvalidBlock,
  type MetricBlockPayload,
  type MetricDirection,
  type MetricItem,
  type NoticeBlockPayload,
  type RenderedBlock,
  type TableBlockPayload,
} from './types'
