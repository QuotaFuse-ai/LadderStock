/**
 * 研究面板底部唯一输入框的「回车路由」（QUOT-552 / SPEC-HOME-V2 v2.0 §3）。
 *
 * 三个搜索面合一后，输入只有一处，去向由这里统一判定：
 * - 问句（问号 / 疑问词）→ AI 研究对话（`HomeResearchChat` 的既有通道）；
 * - 整串 6 位代码 → 个股深挖直达（`/research/:code`）；
 * - 名称 / 关键词 → 既有聚合搜索面板（`SearchPanel` 模态，名称要由数据决定命中哪边）。
 *
 * 归一化与代码判定全部复用 QUOT-356 的 `searchIntent` / `searchRoutes`，
 * 这里不写第二份解析；疑问词表是本次新增的唯一判定，只服务于「问句进 AI」这一条路由。
 */

import { classifyIntent, normalizeQuery } from '../../lib/searchIntent'
import { researchHref } from '../../lib/searchRoutes'

export type HomeQueryRoute =
  | { kind: 'empty' }
  | { kind: 'assistant'; question: string }
  | { kind: 'research'; code: string; href: string }
  | { kind: 'search'; text: string }

const QUESTION_MARK = /[？?]/
/** 疑问 / 祈使词：命中即按问句处理。「吗 / 呢」只在句末附近出现才是疑问，但名称里本就不含它们，从简。 */
const QUESTION_WORDS =
  /为什么|怎么|怎样|如何|哪些|哪个|哪里|是否|多少|什么|吗|呢|帮我|分析|对比|比较|解读|核对|评估|看看|看一下|值得|该不该|能不能|要不要|建议/

export function routeHomeQuery(input: string): HomeQueryRoute {
  const query = normalizeQuery(input)
  const intent = classifyIntent(query)
  if (intent === 'empty') return { kind: 'empty' }
  if (intent === 'code' && query.code) {
    return { kind: 'research', code: query.code, href: researchHref(query.code) }
  }
  if (QUESTION_MARK.test(query.text) || QUESTION_WORDS.test(query.text)) {
    return { kind: 'assistant', question: query.text }
  }
  return { kind: 'search', text: query.text }
}