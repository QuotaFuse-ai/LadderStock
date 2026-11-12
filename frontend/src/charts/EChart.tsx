import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, HeatmapChart, LineChart } from 'echarts/charts'
import {
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { cn } from '../lib/cn'

/**
 * ECharts 运行时（DEC-057）。
 *
 * 只按需注册用到的图表与组件：全量 `echarts` 约 1MB，这里只打蜡烛 / 折线 /
 * 柱状 / 热力图和基础组件。颜色一律由调用方通过 `charts/cssVar.ts` 读令牌后
 * 写进 option——容器本身不感知亮暗主题。
 */
echarts.use([
  LineChart,
  BarChart,
  HeatmapChart,
  CandlestickChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  VisualMapContinuousComponent,
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  CanvasRenderer,
])

export interface EChartProps {
  option: echarts.EChartsCoreOption
  height?: number
  /** 画布内容不可读：调用方必须给一句等价文本（§9） */
  ariaLabel?: string
  className?: string
}

/**
 * 轻量容器：初始化 / 跟随容器尺寸 / 卸载时 dispose。
 *
 * 初始化或 `setOption` 失败只让这张图降级成一行提示，不把异常抛给 React
 * 渲染树（否则整页会被卸成白屏）。
 */
export function EChart({ option, height = 300, ariaLabel, className }: EChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const instRef = useRef<echarts.ECharts | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let chart: echarts.ECharts
    try {
      chart = echarts.init(host)
    } catch (error) {
      console.error('图表初始化失败', error)
      setFailed(true)
      return
    }
    instRef.current = chart
    // 侧栏收起 / 窗口缩放只改容器宽度，不触发 window.resize；监听容器本身
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(host)
    return () => {
      observer.disconnect()
      // 容器没有 2D context 时初始化的实例同样在 dispose 期抛错；卸载阶段的异常会冒到
      // 整棵 React 树（同一次里其它块一起被卸掉），这里按「画不出来」静默收尾
      try {
        chart.dispose()
      } catch (error) {
        console.error('图表销毁失败', error)
      }
      instRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = instRef.current
    if (!chart) return
    // notMerge=true：整份替换，刷新后序列增减时不会残留旧线
    try {
      chart.setOption(option, true)
      setFailed(false)
    } catch (error) {
      console.error('图表渲染失败', error)
      setFailed(true)
    }
  }, [option])

  return (
    <div
      className={cn('relative w-full', className)}
      style={{ height }}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
    >
      <div ref={hostRef} style={{ height }} />
      {failed && (
        <p className="absolute inset-0 grid place-items-center text-caption text-text-muted">
          这张图没有画出来
        </p>
      )}
    </div>
  )
}
