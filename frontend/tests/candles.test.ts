import { describe, expect, it } from 'vitest'

import { candleBodyWidth, drawCandles, type CandleDatum, type CandlePathSink } from '../src/charts/candles'

/** 录制 sink：按调用顺序记下命令，测试里逐条断言。 */
type Cmd =
  | { op: 'moveTo'; x: number; y: number }
  | { op: 'lineTo'; x: number; y: number }
  | { op: 'rect'; x: number; y: number; w: number; h: number }

function recordingSink(): { sink: CandlePathSink; cmds: Cmd[] } {
  const cmds: Cmd[] = []
  return {
    cmds,
    sink: {
      moveTo: (x, y) => cmds.push({ op: 'moveTo', x, y }),
      lineTo: (x, y) => cmds.push({ op: 'lineTo', x, y }),
      rect: (x, y, w, h) => cmds.push({ op: 'rect', x, y, w, h }),
    },
  }
}

function noopSink(): CandlePathSink {
  return { moveTo() {}, lineTo() {}, rect() {} }
}

/** 与蜡烛无关的线性布局：x = i*10，价格 0..100 → y = 100 - price（越涨越高越靠上）。 */
const layout = {
  xAt: (i: number) => i * 10,
  yAt: (price: number) => 100 - price,
  bodyWidth: 8,
}

function bar(open: number, high: number, low: number, close: number): CandleDatum {
  return { open, high, low, close }
}

describe('candleBodyWidth（QUOT-411）', () => {
  it('实体宽 = 间距 × 0.55 四舍五入', () => {
    expect(candleBodyWidth(20)).toBe(11)
    expect(candleBodyWidth(10)).toBe(6) // 5.5 → 6
  })

  it('clamp 到 1–16px：极窄间距不低于 1，超宽间距不超 16', () => {
    expect(candleBodyWidth(1)).toBe(1)
    expect(candleBodyWidth(0.2)).toBe(1)
    expect(candleBodyWidth(200)).toBe(16)
  })
})

describe('drawCandles（QUOT-411）', () => {
  it('阳线进 upSink（红），阴线进 downSink（绿），close === open 平盘按阳线口径', () => {
    const up = recordingSink()
    const down = recordingSink()
    const drawn = drawCandles(
      [bar(10, 11, 9, 10), bar(10, 11, 9, 9.5)], // 第一根平盘十字星，第二根阴线
      layout,
      up.sink,
      down.sink,
    )
    expect(drawn).toBe(2)
    expect(up.cmds).toHaveLength(3)
    expect(down.cmds).toHaveLength(3)
  })

  it('任一 OHLC 为 null 的根整根跳过，不写任何命令', () => {
    const up = recordingSink()
    const down = recordingSink()
    const drawn = drawCandles(
      [
        bar(9, 10, 8, 9.6),
        { open: null, high: 10, low: 8, close: 9 },
        bar(9.6, 10.5, 9.2, 10),
      ],
      layout,
      up.sink,
      down.sink,
    )
    expect(drawn).toBe(2)
    // x 只出现 i=0 与 i=2 的位置（0 与 20）
    for (const { cmds } of [up, down]) {
      for (const cmd of cmds) {
        const x = cmd.op === 'rect' ? cmd.x + layout.bodyWidth / 2 : cmd.x
        expect([0, 20]).toContain(x)
      }
    }
  })

  it('命令序列：先影线（moveTo high → lineTo low）再实体 rect', () => {
    const up = recordingSink()
    const down = recordingSink()
    drawCandles([bar(9, 12, 7, 11)], layout, up.sink, down.sink)
    expect(up.cmds).toEqual([
      { op: 'moveTo', x: 0, y: 100 - 12 }, // 影线顶：high
      { op: 'lineTo', x: 0, y: 100 - 7 }, // 影线底：low
      { op: 'rect', x: -4, y: 100 - 11, w: 8, h: 2 }, // 实体：x-half, min(yO,yC)
    ])
    expect(down.cmds).toEqual([])
  })

  it('十字星（close === open）实体高度最小 1px，不消失', () => {
    const up = recordingSink()
    drawCandles([bar(10, 10.2, 9.8, 10)], layout, up.sink, noopSink())
    const rect = up.cmds.find((c) => c.op === 'rect')
    expect(rect && rect.op === 'rect' ? rect.h : 0).toBe(1)
  })

  it('实体 y 取 open/close 中更靠上者，高度 = 价差的设备像素绝对值', () => {
    const down = recordingSink()
    // 阴线：open 11 → y=89，close 9 → y=91，实体顶应为 89，高 2
    drawCandles([bar(11, 12, 8, 9)], layout, noopSink(), down.sink)
    expect(down.cmds).toEqual([
      { op: 'moveTo', x: 0, y: 100 - 12 },
      { op: 'lineTo', x: 0, y: 100 - 8 },
      { op: 'rect', x: -4, y: 89, w: 8, h: 2 },
    ])
  })

  it('空序列与全 null 序列返回 0，不写命令', () => {
    const up = recordingSink()
    const down = recordingSink()
    const blank: CandleDatum = { open: null, high: null, low: null, close: null }
    expect(drawCandles([], layout, up.sink, down.sink)).toBe(0)
    expect(drawCandles([blank, blank], layout, up.sink, down.sink)).toBe(0)
    expect(up.cmds).toEqual([])
    expect(down.cmds).toEqual([])
  })
})
