/**
 * 事件时间轴的「看多少天」偏好（QUOT-436）。
 *
 * 只存一个 7 / 30 / 90 的枚举，不存任何事件或持仓数据。
 * 放在 `lib/` 而不是页面里：雷达页本身不碰浏览器存储（positions-privacy 静态检查）。
 */

export type CalendarDays = 7 | 30 | 90

const STORAGE_KEY = 'ldst.calendar.days'

const ALLOWED: readonly CalendarDays[] = [7, 30, 90]

export function readCalendarDays(): CalendarDays {
  try {
    const raw = Number(window.localStorage.getItem(STORAGE_KEY))
    if (ALLOWED.includes(raw as CalendarDays)) return raw as CalendarDays
  } catch {
    /* 隐私模式禁用 localStorage：按默认档处理 */
  }
  return 30
}

export function saveCalendarDays(value: CalendarDays): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    /* 本次会话仍然生效，只是不持久化 */
  }
}
