/**
 * K 线均线计算（纯函数，无 DOM / canvas 依赖，便于单测）。
 *
 * 页面「不重算」红线只约束行情数值本身：MA 是前端按收盘价滚动平均得到的
 * 展示序列（业界惯例），不参与任何估值 / 分位 / 对账口径。
 * 窗口内任一 close 为 null → 该点 MA 为 null（不跳过、不用部分窗口补算），
 * 窗口不满（前 n-1 根）→ null，与主流行情软件一致。
 */

export interface MaSeries {
  window: number
  /** 与输入 closes 等长；不足窗口或窗口内含 null 的位置为 null */
  values: Array<number | null>
}

export const MA_WINDOWS = [5, 10, 20, 60] as const

export type MaBundle = {
  ma5: MaSeries
  ma10: MaSeries
  ma20: MaSeries
  ma60: MaSeries
}

/**
 * 单条均线：输入收盘价序列（可含 null），输出与输入等长的滚动均值。
 * 时间复杂度 O(n)：进出窗口用增量加减，不做窗口内重扫。
 */
export function movingAverage(closes: Array<number | null>, window: number): Array<number | null> {
  const out: Array<number | null> = new Array(closes.length).fill(null)
  if (!Number.isInteger(window) || window < 1) return out
  let sum = 0
  let nulls = 0
  let count = 0
  for (let i = 0; i < closes.length; i++) {
    const value = closes[i]
    if (value === null) {
      nulls++
    } else {
      sum += value
    }
    count++
    if (count > window) {
      const left = closes[i - window]
      if (left === null) nulls--
      else sum -= left
      count = window
    }
    if (count === window && nulls === 0) out[i] = sum / window
  }
  return out
}

/** 四条均线一次算齐；K 线图例与 paths 都从这里取。 */
export function movingAverages(closes: Array<number | null>): MaBundle {
  return {
    ma5: { window: 5, values: movingAverage(closes, 5) },
    ma10: { window: 10, values: movingAverage(closes, 10) },
    ma20: { window: 20, values: movingAverage(closes, 20) },
    ma60: { window: 60, values: movingAverage(closes, 60) },
  }
}

/** 「MA5」这类图例短标签（数字恒定，不需要 toFixed 抖动）。 */
export function maLabel(window: number): string {
  return `MA${window}`
}
