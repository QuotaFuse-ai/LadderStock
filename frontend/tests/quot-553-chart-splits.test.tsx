// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type uPlot from 'uplot'

import { BarChart, LineChart } from '../src/charts/Chart'

/**
 * QUOT-553：CH-02 柱图 x 轴重复类目。
 *
 * 根因是 uPlot 按宽度自动撒小数刻度，取标签时 round 把多个刻度折叠到同一
 * 数据索引（1板×3 / 2板×5），多余刻度位没有柱。修复把刻度钉在数据索引上。
 * jsdom 没有 2D 画布（同 market-demo.test 的先例），这里用记录入参的 uPlot
 * 替身，断言真实 Chart.tsx 交出去的 splits / values / range / draw 钩子。
 */

interface RecordingCtx {
  calls: Array<{ text: string; x: number; y: number }>
  font: string
  fillStyle: string
  textAlign: string
  textBaseline: string
  save: () => void
  restore: () => void
  fillText: (text: string, x: number, y: number) => void
}

interface RecordedInstance {
  options: uPlot.Options
  data: uPlot.AlignedData
  bbox: { width: number; height: number }
  ctx: RecordingCtx
  valToPos: (val: number, scale: string, canvasPixels?: boolean) => number
}

const h = vi.hoisted(() => ({
  instances: [] as RecordedInstance[],
  barsOpts: [] as Array<{ align?: number; size?: [number, number] }>,
  plotWidth: 720,
  pxRatio: 1,
}))

vi.mock('uplot', () => {
  class FakeUPlot {
    static get pxRatio() {
      return h.pxRatio
    }
    static paths = {
      bars: (opts: { align?: number; size?: [number, number] }) => {
        h.barsOpts.push(opts)
        return () => null
      },
    }
    options: uPlot.Options
    data: uPlot.AlignedData
    bbox = { width: h.plotWidth, height: 280 }
    ctx: RecordingCtx = {
      calls: [],
      font: '',
      fillStyle: '',
      textAlign: '',
      textBaseline: '',
      save: () => {},
      restore: () => {},
      fillText(text: string, x: number, y: number) {
        this.calls.push({ text, x, y })
      },
    }
    constructor(options: uPlot.Options, data: uPlot.AlignedData) {
      this.options = options
      this.data = data
      h.instances.push(this)
    }
    setSize() {}
    destroy() {}
    // 线性几何：y 每单位 3.7px（73 的量程下 1 只有 ~3.7px），x 每类目 180px
    valToPos(val: number, scale: string, canvasPixels?: boolean) {
      const ratio = canvasPixels ? h.pxRatio : 1
      return scale === 'x' ? (40 + val * 180) * ratio : (280 - val * 3.7) * ratio
    }
  }
  return { default: FakeUPlot }
})

const LADDER_POINTS = [
  { label: '1 板', value: 73 },
  { label: '2 板', value: 15 },
  { label: '3 板', value: 4 },
  { label: '7 板', value: 1 },
]

function historyPoints(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    label: `10-${String(index + 1).padStart(2, '0')}`,
    value: 40 + (index % 5) * 10,
  }))
}

function xAxis(inst: RecordedInstance) {
  const axis = inst.options.axes?.[0]
  if (!axis || typeof axis.splits !== 'function' || typeof axis.values !== 'function') {
    throw new Error('x 轴必须提供 splits 与 values 函数')
  }
  return { splits: axis.splits, values: axis.values }
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  h.instances.length = 0
  h.barsOpts.length = 0
  h.plotWidth = 720
  h.pxRatio = 1
})

describe('BarChart（CH-02 连板梯队）', () => {
  it('刻度数=类目数：刻度钉在数据索引，标签唯一且一一对应', () => {
    render(<BarChart points={LADDER_POINTS} ariaLabel="连板梯队柱状图" />)
    const inst = h.instances[0]
    const axis = xAxis(inst)
    const splits = axis.splits(inst as unknown as uPlot, 0, 0, 3, 0.25, 40)
    expect(splits).toEqual([0, 1, 2, 3])
    expect(splits.every(Number.isInteger)).toBe(true)
    const labels = axis.values(inst as unknown as uPlot, splits, 0, 40, 1)
    expect(labels).toEqual(['1 板', '2 板', '3 板', '7 板'])
    expect(new Set(labels).size).toBe(LADDER_POINTS.length)
  })

  it('x 量程左右各留半个类目位，首尾柱与标签不被画布裁掉', () => {
    render(<BarChart points={LADDER_POINTS} />)
    const range = h.instances[0].options.scales?.x?.range
    expect(typeof range).toBe('function')
    if (typeof range === 'function') {
      expect(range(h.instances[0] as unknown as uPlot, 0, 3, 'x')).toEqual([-0.5, 3.5])
    }
  })

  it('柱身居中于类目刻度（align 0），柱与标签对齐', () => {
    render(<BarChart points={LADDER_POINTS} />)
    expect(h.barsOpts[0]?.align).toBe(0)
  })

  it('小值柱柱顶如实标家数，大柱不标；不改数据', () => {
    render(<BarChart points={LADDER_POINTS} />)
    const inst = h.instances[0]
    expect(inst.data[1]).toEqual([73, 15, 4, 1])
    const draw = inst.options.hooks?.draw?.[0]
    expect(draw).toBeDefined()
    draw?.(inst as unknown as uPlot)
    // 只有 7板=1（~3.7px < 6px）被标注；4/15/73 的柱足够高，不标
    expect(inst.ctx.calls).toHaveLength(1)
    expect(inst.ctx.calls[0].text).toBe('1')
    expect(inst.ctx.calls[0].x).toBe(40 + 3 * 180)
    expect(inst.ctx.textAlign).toBe('center')
  })

  it('高分屏按 canvas 像素判定小柱与字号', () => {
    h.pxRatio = 2
    render(<BarChart points={LADDER_POINTS} />)
    const inst = h.instances[0]
    inst.options.hooks?.draw?.[0]?.(inst as unknown as uPlot)
    expect(inst.ctx.calls).toHaveLength(1)
    expect(inst.ctx.calls[0].text).toBe('1')
    expect(inst.ctx.calls[0].x).toBe((40 + 3 * 180) * 2)
    expect(inst.ctx.font).toContain('24px')
  })

  it('只有一个板级时量程与刻度不退化', () => {
    render(<BarChart points={[{ label: '1 板', value: 5 }]} />)
    const inst = h.instances[0]
    const range = inst.options.scales?.x?.range
    if (typeof range === 'function') {
      expect(range(inst as unknown as uPlot, 0, 0, 'x')).toEqual([-0.5, 0.5])
    }
    const axis = xAxis(inst)
    expect(axis.splits(inst as unknown as uPlot, 0, 0, 0, 1, 40)).toEqual([0])
  })
})

describe('LineChart（CH-01 情绪温度）', () => {
  it('宽画布：每个数据点一个整数刻度，标签唯一', () => {
    h.plotWidth = 1200
    const points = historyPoints(20)
    render(<LineChart points={points} ariaLabel="情绪温度折线" />)
    const inst = h.instances[0]
    const axis = xAxis(inst)
    const splits = axis.splits(inst as unknown as uPlot, 0, 0, 19, 1, 60)
    expect(splits).toEqual(points.map((_, index) => index))
    const labels = axis.values(inst as unknown as uPlot, splits, 0, 60, 1)
    expect(new Set(labels).size).toBe(20)
    expect(labels[0]).toBe('10-01')
    expect(labels[19]).toBe('10-20')
  })

  it('窄画布：刻度按步长抽稀，仍是整数索引、标签不重复不越界', () => {
    h.plotWidth = 300
    const points = historyPoints(20)
    render(<LineChart points={points} />)
    const inst = h.instances[0]
    const axis = xAxis(inst)
    const splits = axis.splits(inst as unknown as uPlot, 0, 0, 19, 0.5, 40)
    expect(splits.length).toBeLessThanOrEqual(Math.floor(300 / 48))
    expect(splits.length).toBeGreaterThanOrEqual(2)
    expect(splits.every(Number.isInteger)).toBe(true)
    expect(splits.every((value) => value >= 0 && value <= 19)).toBe(true)
    const labels = axis.values(inst as unknown as uPlot, splits, 0, 40, 1)
    expect(new Set(labels).size).toBe(splits.length)
    expect(labels).toEqual(splits.map((index) => points[index].label))
  })
})