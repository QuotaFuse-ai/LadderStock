/**
 * K 线蜡烛几何（纯函数，无 DOM / canvas 依赖）。
 *
 * 与 `CandlestickChart.tsx` 的分工：这里只产出「往哪个 sink 写什么命令」，
 * 生产侧由 uPlot paths 闭包用 Path2D 实现 sink，测试侧用录制 sink 断言
 * 命令序列。坐标全部是设备像素（uPlot Path2D 约定，valToPos 第三参传 true）。
 *
 * A 股口径：close >= open 为阳线（up / 红），close < open 为阴线（down / 绿）。
 */

export interface CandleDatum {
  open: number | null
  high: number | null
  low: number | null
  close: number | null
}

/** Path2D 的最小子集；录制测试按调用顺序回放。 */
export interface CandlePathSink {
  moveTo(x: number, y: number): void
  lineTo(x: number, y: number): void
  rect(x: number, y: number, w: number, h: number): void
}

export interface CandleLayout {
  /** 第 i 根的中心 x（设备像素），调用方用 uPlot 位置的 px 函数闭包 */
  xAt: (i: number) => number
  /** 价格 → 设备像素 y，调用方用 u.valToPos(v, yScaleKey, true) 闭包 */
  yAt: (price: number) => number
  /** 蜡烛实体宽（px），已 clamp */
  bodyWidth: number
}

/** 实体宽 = 间距的 0.55（与成交量柱 size:[0.55,16] 同系数），clamp 到 1–16px。 */
export function candleBodyWidth(spacingPx: number): number {
  const width = Math.round(spacingPx * 0.55)
  return Math.min(16, Math.max(1, width))
}

/**
 * 把蜡烛写进 up / down 两个 sink（wick + body）。
 * 任一 OHLC 为 null 的根跳过；close === open（平盘/十字星）实体最小 1px 高。
 * 返回实际画出的根数（供测试与调试）。
 */
export function drawCandles(
  data: readonly CandleDatum[],
  layout: CandleLayout,
  upSink: CandlePathSink,
  downSink: CandlePathSink,
): number {
  const half = layout.bodyWidth / 2
  let drawn = 0
  for (let i = 0; i < data.length; i++) {
    const bar = data[i]
    if (bar.open === null || bar.high === null || bar.low === null || bar.close === null) continue
    const x = layout.xAt(i)
    const sink = bar.close >= bar.open ? upSink : downSink
    // 影线：high → low 竖线（1px 由 series width 保证）
    sink.moveTo(x, layout.yAt(bar.high))
    sink.lineTo(x, layout.yAt(bar.low))
    // 实体：rect 高度最小 1px，十字星不消失
    const yOpen = layout.yAt(bar.open)
    const yClose = layout.yAt(bar.close)
    const top = Math.min(yOpen, yClose)
    const height = Math.max(Math.abs(yClose - yOpen), 1)
    sink.rect(x - half, top, layout.bodyWidth, height)
    drawn++
  }
  return drawn
}
