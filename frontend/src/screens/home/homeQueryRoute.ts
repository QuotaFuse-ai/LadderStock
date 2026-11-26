/**
 * 研究面板底部唯一输入框的「回车路由」（QUOT-552 / SPEC-HOME-V2 v2.0 §3）。
 *
 * 三个搜索面合一后，输入只有一处，去向由这里统一判定。
 *
 * ★ 2026-11-16：取消「疑问词正则」分流。原实现用一张词表判「问句 → AI、其余 → 搜索」，
 * 表里没有的词（如「介绍下自己」）会被判成关键词搜索，落到聚合搜索面板回一句
 * 「今日数据里没有匹配」——用户看到的「配置字段对应」，就是这条正则。
 * 现在只保留确定性最高的一条：**整串 6 位代码 → 个股深挖直达**；其余全部交 agent，
 * 由模型判断这是自我介绍、方法问题、公司名还是关键词，需要标的时它自己调
 * `lookup_symbols` 拿代码再查行情（见 `ai/agent_runtime.py`）。
 * 归一化与代码判定仍复用 QUOT-356 的 `searchIntent` / `searchRoutes`。
 */

import { classifyIntent, normalizeQuery } from '../../lib/searchIntent'
import { researchHref } from '../../lib/searchRoutes'

export type HomeQueryRoute =
  | { kind: 'empty' }
  | { kind: 'assistant'; question: string }
  | { kind: 'research'; code: string; href: string }
  /** 保留给显式搜索入口（`?search=` 深链、搜索历史里的词）；回车路由不再产出。 */
  | { kind: 'search'; text: string }

export function routeHomeQuery(input: string): HomeQueryRoute {
  const query = normalizeQuery(input)
  const intent = classifyIntent(query)
  if (intent === 'empty') return { kind: 'empty' }
  if (intent === 'code' && query.code) {
    return { kind: 'research', code: query.code, href: researchHref(query.code) }
  }
  return { kind: 'assistant', question: query.text }
}
