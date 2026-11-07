/**
 * 连续竞价时段判定（QUOT-470）。
 *
 * 与后端 `core/intraday.py:in_trading_session` 同口径：09:30–11:30 / 13:00–15:00，左闭右开，
 * 用**交易所挂钟**（`Asia/Shanghai`）而不是本机时区——本机可能是 UTC，用本机钟点判会把
 * 休市说成盘中、把盘中说成休市。
 *
 * 只做「周一至周五 + 竞价窗口」的门控：前端拿不到交易日历数据源，节假日（若逢工作日）
 * 会判成盘中；休市的最终判据交给数据本身（`as_of` 即上一交易日收盘时刻），前端不假装知道假期。
 */

import { useEffect, useState } from 'react'

/** 交易所时区；中国没有夏令时，固定 +08:00 与 `Asia/Shanghai` 等价。 */
export const EXCHANGE_TIMEZONE = 'Asia/Shanghai'

/** 连续竞价窗口（分钟数，左闭右开）：09:30–11:30 / 13:00–15:00。 */
export const TRADING_WINDOWS: ReadonlyArray<readonly [number, number]> = [
  [9 * 60 + 30, 11 * 60 + 30],
  [13 * 60, 15 * 60],
]

interface Clock {
  /** 0=周日 … 6=周六 */
  weekday: number
  /** 自零点起的分钟数（含秒的小数部分） */
  minutes: number
}

const PARTS = new Intl.DateTimeFormat('en-US', {
  timeZone: EXCHANGE_TIMEZONE,
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const WEEKDAYS: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }

/**
 * 把 `now` 换算成交易所挂钟。`Intl` 在 jsdom / 各 Node 版本都走 `timeZone`，
 * 不依赖本机 TZ。解析不出就返回 `null`（调用方按「不确定」处理，不猜）。
 */
export function exchangeClock(now: Date): Clock | null {
  const parts = PARTS.formatToParts(now)
  const pick = (type: string) => parts.find((part) => part.type === type)?.value
  const weekdayKey = pick('weekday')
  const hour = Number(pick('hour'))
  const minute = Number(pick('minute'))
  const second = Number(pick('second'))
  if (weekdayKey === undefined || !Number.isFinite(hour) || !Number.isFinite(minute)) return null
  const weekday = WEEKDAYS[weekdayKey]
  if (weekday === undefined) return null
  // `hour12:false` 在个别实现里对午夜给 "24"：归一成 0。
  const normalizedHour = hour === 24 ? 0 : hour
  return {
    weekday,
    minutes: normalizedHour * 60 + minute + (Number.isFinite(second) ? second / 60 : 0),
  }
}

function isWeekday(weekday: number): boolean {
  return weekday >= 1 && weekday <= 5
}

/** `now` 是否落在交易所连续竞价时段（周一至周五 + 竞价窗口）。 */
export function isTradingSession(now: Date = new Date()): boolean {
  const clock = exchangeClock(now)
  if (!clock || !isWeekday(clock.weekday)) return false
  return TRADING_WINDOWS.some(([start, end]) => start <= clock.minutes && clock.minutes < end)
}

/**
 * 时段门控的挂钟钩子：每分钟重估一次「是否盘中」（跨 09:30 / 11:30 / 13:00 / 15:00 边界时
 * 文案和轮询都要跟着变）。`true` 传 `enabled` 之外没有别的判定入口，屏侧只读一个布尔值。
 */
export function useTradingSession(enabled = true, now: () => Date = () => new Date()): boolean {
  const [open, setOpen] = useState<boolean>(() => enabled && isTradingSession(now()))

  useEffect(() => {
    if (!enabled) {
      setOpen(false)
      return
    }
    setOpen(isTradingSession(now()))
    // 对齐到下一个整分钟再起跳，避免 59s 起的漂移把边界判错。
    let interval: ReturnType<typeof setInterval> | null = null
    const timeout = setTimeout(() => {
      setOpen(isTradingSession(now()))
      interval = setInterval(() => setOpen(isTradingSession(now())), 60_000)
    }, 60_000 - (now().getSeconds() * 1000 + now().getMilliseconds()))
    return () => {
      clearTimeout(timeout)
      if (interval !== null) clearInterval(interval)
    }
  }, [enabled, now])

  return open
}
