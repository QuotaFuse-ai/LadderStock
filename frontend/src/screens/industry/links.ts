import type { PageState } from '../../lib/pageState'

/**
 * 产业页之间的跳转只带「演示开关」与「强制态」两个参数：
 * 演示时点进行情仍能复现同一张图；`?state=` 不会被悄悄丢掉。
 */

export type ParamValue = string | boolean | null | undefined

export function withParams(path: string, params: Record<string, ParamValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === false || value === '') continue
    search.set(key, value === true ? '1' : String(value))
  }
  const query = search.toString()
  return query ? `${path}?${query}` : path
}

export function industryHref(
  name: string,
  options: { demo?: boolean; state?: PageState | null } = {},
): string {
  return withParams(`/industry/${encodeURIComponent(name)}`, {
    demo: options.demo ? '1' : null,
    state: options.state ?? null,
  })
}
