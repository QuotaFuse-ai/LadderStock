import type { ResearchStageName } from '../../api/research'
import type { ResearchView } from './useResearchView'

type LinkContext = Pick<ResearchView, 'demo' | 'date' | 'variant'>

/**
 * 两页之间传递的最小上下文：演示开关、日期、演示变体。
 * `?state=` 是单页取证参数，不跨页携带。
 */
export function researchQuery(
  view: LinkContext,
  extra: { stage?: ResearchStageName | null } = {},
): string {
  const params = new URLSearchParams()
  if (view.demo) params.set('demo', '1')
  if (view.demo && view.variant !== 'full') params.set('variant', view.variant)
  params.set('date', view.date)
  if (extra.stage) params.set('stage', extra.stage)
  return `?${params.toString()}`
}

export function reportHref(
  code: string,
  view: LinkContext,
  stage?: ResearchStageName,
): string {
  return `/research/${encodeURIComponent(code)}/report${researchQuery(view, { stage })}`
}

export function detailHref(code: string, view: LinkContext): string {
  return `/research/${encodeURIComponent(code)}${researchQuery(view)}`
}
