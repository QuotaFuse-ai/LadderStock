import { describe, expect, it } from 'vitest'

import { maLabel, movingAverage, movingAverages } from '../src/charts/ma'

/** QUOT-411：K 线 MA5/10/20/60 的纯计算——窗口不满 / 含 null / 增量正确性。 */
describe('均线计算（QUOT-411）', () => {
  it('窗口不满为 null，满窗后为滚动均值', () => {
    expect(movingAverage([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4])
  })

  it('窗口内含 null → 该点 null（不部分补算），null 滑出窗口后恢复', () => {
    // 窗口 3：closes[2] 的 null 一直压住 i2–i4，i5 滑出后恢复
    expect(movingAverage([1, 2, null, 4, 5, 6], 3)).toEqual([null, null, null, null, null, 5])
  })

  it('与朴素窗口实现逐点一致（120 根正弦序列，四条窗口）', () => {
    const closes = Array.from({ length: 120 }, (_, i) => Math.sin(i / 7) * 10 + i * 0.1)
    for (const window of [5, 10, 20, 60]) {
      const fast = movingAverage(closes, window)
      for (let i = 0; i < window - 1; i++) expect(fast[i]).toBeNull()
      for (let i = window - 1; i < closes.length; i++) {
        let sum = 0
        for (let j = i - window + 1; j <= i; j++) sum += closes[j]
        expect(fast[i]).toBeCloseTo(sum / window, 10)
      }
      expect(fast).toHaveLength(closes.length)
    }
  })

  it('movingAverages 一次算齐四条，短标签为 MA5/MA10/MA20/MA60', () => {
    const closes = Array.from({ length: 60 }, (_, i) => i + 1)
    const bundle = movingAverages(closes)
    expect(bundle.ma5.window).toBe(5)
    expect(bundle.ma10.window).toBe(10)
    expect(bundle.ma20.window).toBe(20)
    expect(bundle.ma60.window).toBe(60)
    // 1..60 的 MA60 末值 = 首尾均值
    expect(bundle.ma60.values[59]).toBeCloseTo(30.5, 10)
    expect(bundle.ma20.values[59]).not.toBeNull()
    expect(maLabel(5)).toBe('MA5')
    expect(maLabel(60)).toBe('MA60')
  })

  it('空序列与非法窗口不炸，长度始终与输入对齐', () => {
    expect(movingAverage([], 5)).toEqual([])
    expect(movingAverage([1, 2], 0)).toEqual([null, null])
    expect(movingAverage([1, 2], 2.5)).toEqual([null, null])
  })
})
