import { useSyncExternalStore } from 'react'

/**
 * 媒体查询订阅（DEC-062 §2）：移动端抽屉必须**不渲染**汉堡按钮，而不是用 CSS 藏起来，
 * 所以 `<768px` 只能由 JS 判断。jsdom 不提供 `matchMedia`，这里按「不匹配」兜底，
 * 保证既有测试渲染 Layout 时不会多出一颗汉堡。
 */
function queryList(query: string): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  try {
    return window.matchMedia(query)
  } catch {
    return null
  }
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const list = queryList(query)
      if (!list) return () => {}
      list.addEventListener('change', listener)
      return () => list.removeEventListener('change', listener)
    },
    () => queryList(query)?.matches ?? false,
    () => false,
  )
}

/** 抽屉断点：与 DEC-062 §2 的 `md`（768px）一致，两边改一处必须同改另一处。 */
export const MOBILE_QUERY = '(max-width: 767px)'

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY)
}
