import { useEffect, useMemo, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

import { cn } from '../lib/cn'
import { chartPalette, TRANSPARENT } from './cssVar'
import { useThemeChoice } from '../lib/prefs'

/**
 * CH-01 情绪温度折线（带区间带 + 阈值线）/ CH-02 连板梯队柱（DESIGN-SPEC §6）。
 *
 * 为什么用 uPlot：canvas 渲染，~45KB 未压缩，远低于自绘坐标轴的成本；
 * 颜色的唯一来源是 `charts/cssVar.ts`（canvas 读不到 CSS 变量）。
 * 每个图表都必须配一个数据表（`§6.4`），这里只负责画布，表格由调用方渲染。
 */

export interface LinePoint {
  label: string
  value: number | null
}

export interface LineChartProps {
  points: LinePoint[]
  height?: number
  /** 情绪区间带：正常/偏热/过热的分界以横向色带表示 */
  band?: { lower: number; upper: number }
  threshold?: { value: number; label?: string }
  unit?: string
  /** 画布本身不可读：调用方必须给一句文字结论（§9 图表要有等价文本） */
  ariaLabel?: string
  className?: string
}

const AXIS_FONT = '12px var(--ldst-font-sans)'

export function LineChart({
  points,
  height = 220,
  band,
  threshold,
  unit = '',
  ariaLabel,
  className,
}: LineChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const theme = useThemeChoice()
  const pointsKey = useMemo(() => JSON.stringify(points), [points])

  useEffect(() => {
    const host = hostRef.current
    if (!host || points.length === 0) return
    const palette = chartPalette()
    const xs = points.map((_, index) => index)
    const values = points.map((point) => point.value)
    const data: uPlot.AlignedData = [xs, values]
    const series: uPlot.Series[] = [
      {},
      {
        label: `情绪温度${unit}`,
        stroke: palette.line,
        width: 2,
        fill: palette.band,
        points: { show: points.length <= 12, size: 6, stroke: palette.line, fill: palette.line },
      },
    ]
    const bands: uPlot.Band[] = []

    if (band) {
      data.push(new Array(xs.length).fill(band.upper), new Array(xs.length).fill(band.lower))
      series.push(
        { label: '区间上沿', stroke: TRANSPARENT, width: 0, points: { show: false } },
        { label: '区间下沿', stroke: TRANSPARENT, width: 0, points: { show: false } },
      )
      bands.push({ series: [2, 3], fill: palette.band })
    }

    if (threshold) {
      data.push(new Array(xs.length).fill(threshold.value))
      series.push({
        label: threshold.label ?? String(threshold.value),
        stroke: palette.threshold,
        width: 1,
        dash: [4, 4],
        points: { show: false },
      })
    }

    const options: uPlot.Options = {
      width: Math.max(host.clientWidth, 240),
      height,
      padding: [8, 8, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false } },
      scales: { x: { time: false }, y: { range: (_u, min, max) => [Math.min(0, min - 2), Math.max(60, max + 2)] } },
      axes: [
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          grid: { stroke: palette.grid, width: 1 },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
          values: (_u, splits) => splits.map((value) => points[Math.round(value)]?.label ?? ''),
        },
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          size: 44,
          grid: { stroke: palette.grid, width: 1 },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
        },
      ],
      series,
      bands: bands.length > 0 ? bands : undefined,
    }

    const chart = new uPlot(options, data, host)

    const observer = new ResizeObserver(() => {
      chart.setSize({ width: Math.max(host.clientWidth, 240), height })
    })
    observer.observe(host)
    return () => {
      observer.disconnect()
      chart.destroy()
    }
    // pointsKey 覆盖 points 内容；theme 变化时必须重读 CSS 变量重绘
  }, [pointsKey, height, band?.lower, band?.upper, threshold?.value, threshold?.label, unit, theme]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={hostRef}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
      className={cn('w-full', className)}
    />
  )
}

export interface BarPoint {
  label: string
  value: number
  hint?: string
}

export interface BarChartProps {
  points: BarPoint[]
  height?: number
  ariaLabel?: string
  className?: string
}

/** CH-02 连板梯队：竖柱，数值即该板位数家数。 */
export function BarChart({ points, height = 200, ariaLabel, className }: BarChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const theme = useThemeChoice()
  const pointsKey = useMemo(() => JSON.stringify(points), [points])

  useEffect(() => {
    const host = hostRef.current
    if (!host || points.length === 0) return
    const palette = chartPalette()
    const xs = points.map((_, index) => index)
    const values = points.map((point) => point.value)
    const data: uPlot.AlignedData = [xs, values]

    const options: uPlot.Options = {
      width: Math.max(host.clientWidth, 240),
      height,
      padding: [8, 8, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false } },
      scales: { x: { time: false }, y: { range: (_u, _min, max) => [0, Math.max(1, max + 1)] } },
      axes: [
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          grid: { show: false },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
          values: (_u, splits) => splits.map((value) => points[Math.round(value)]?.label ?? ''),
        },
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          size: 36,
          grid: { stroke: palette.grid, width: 1 },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
        },
      ],
      series: [
        {},
        {
          label: '家数',
          stroke: palette.barUp,
          fill: palette.barUp,
          width: 0,
          points: { show: false },
          paths: uPlot.paths.bars?.({ size: [0.55, 16], align: 1 }),
        },
      ],
    }

    const chart = new uPlot(options, data, host)
    const observer = new ResizeObserver(() => {
      chart.setSize({ width: Math.max(host.clientWidth, 240), height })
    })
    observer.observe(host)
    return () => {
      observer.disconnect()
      chart.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pointsKey, height, theme])

  return (
    <div
      ref={hostRef}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
      className={cn('w-full', className)}
    />
  )
}
