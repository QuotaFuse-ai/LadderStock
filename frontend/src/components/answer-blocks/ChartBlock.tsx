import { useMemo } from 'react'
import type { EChartsCoreOption } from 'echarts/core'

import type { ChartBlockPayload } from './types'
import { BlockShell } from './BlockShell'
import { EChart } from '../../charts/EChart'
import { TRANSPARENT, useChartPalette, type ChartPalette } from '../../charts/cssVar'

/**
 * `chart` 块（任务书 §6.2）：折线 / 柱状 / 面积，复用仓库既有 ECharts 运行时。
 *
 * 【据名判】为什么不复用 `KlineCard`：它是「自己取数 + 蜡烛图 + 周期切换」的产品卡
 * （`loader = loadKline`，数据契约是 K 线行），要画这里的任意序列得改它的数据契约，
 * 阶段 E 明确不动它。`charts/Chart.tsx` 的 LineChart / BarChart 也各自写死了
 * 「情绪温度」「家数」的语义与量程。所以这里用同一套底座 `EChart`，按载荷现拼 option。
 *
 * 一图多序列时按涨跌语义着色：第一条用 `chart-line`（同值 `accent`，蓝只管主线），
 * 其余依次用 `series-2`；柱状统一用 `chart-bar-up`（纯数量，不承载涨跌语义）。
 */

const CHART_HEIGHT = 220

function seriesColors(palette: ChartPalette, kind: ChartBlockPayload['kind'], index: number): string {
  if (kind !== 'line' && kind !== 'area') return palette.barUp
  return index === 0 ? palette.line : palette.series2
}

function buildOption(payload: ChartBlockPayload, palette: ChartPalette): EChartsCoreOption {
  // 各序列日期可能不一致：按「序列 0」的日期当横轴，其余序列按日期对齐，缺的点留空
  const categories = payload.series[0].points.map((point) => point[0])

  return {
    animationDuration: 120,
    grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderWidth: 0,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      valueFormatter: (value: unknown) =>
        payload.y_unit && typeof value === 'number'
          ? `${value.toLocaleString('en-US')} ${payload.y_unit}`
          : String(value ?? '—'),
    },
    legend:
      payload.series.length > 1
        ? { top: 0, right: 0, textStyle: { color: palette.text, fontSize: 12 }, itemWidth: 12, itemHeight: 2 }
        : { show: false },
    xAxis: {
      type: 'category',
      data: categories,
      name: payload.x_axis || undefined,
      nameLocation: 'end',
      nameTextStyle: { color: palette.text, fontSize: 12, padding: [0, 0, 0, 4] },
      axisLine: { lineStyle: { color: palette.grid } },
      axisTick: { show: false },
      axisLabel: { color: palette.axis, fontSize: 12, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: payload.y_unit || undefined,
      nameTextStyle: { color: palette.text, fontSize: 12 },
      scale: true,
      splitLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axis, fontSize: 12 },
    },
    series: payload.series.map((series, index) => {
      const color = seriesColors(palette, payload.kind, index)
      const values = categories.map((label) => {
        const hit = series.points.find((point) => point[0] === label)
        return hit ? hit[1] : null
      })
      const base = {
        name: series.name,
        data: values,
        connectNulls: false,
      }
      if (payload.kind === 'bar') {
        return { ...base, type: 'bar' as const, itemStyle: { color }, barMaxWidth: 24 }
      }
      return {
        ...base,
        type: 'line' as const,
        symbol: values.length <= 24 ? 'circle' : 'none',
        symbolSize: 5,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        areaStyle: payload.kind === 'area' ? { color, opacity: 0.18 } : { color: TRANSPARENT },
      }
    }),
  }
}

export function ChartBlock({ payload }: { payload: ChartBlockPayload }) {
  const palette = useChartPalette()
  const option = useMemo(() => buildOption(payload, palette), [payload, palette])

  const ariaLabel = [
    payload.caption || payload.series.map((series) => series.name).join('、'),
    payload.series.length > 0
      ? `${payload.series[0].points.length} 个点`
      : '',
    payload.y_unit ? `单位 ${payload.y_unit}` : '',
  ]
    .filter(Boolean)
    .join('，')

  return (
    <BlockShell
      type="chart"
      title={payload.caption || payload.series.map((series) => series.name).join('、')}
      meta={[payload.x_axis, payload.y_unit].filter(Boolean).join(' · ')}
    >
      <EChart option={option} height={CHART_HEIGHT} ariaLabel={ariaLabel} />
    </BlockShell>
  )
}
