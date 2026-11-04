import { useEffect, useMemo, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

import type { KlineBar } from '../api/research'
import { cn } from '../lib/cn'
import { MISSING } from '../lib/format'
import { useThemeChoice } from '../lib/prefs'
import { candleBodyWidth, drawCandles, type CandlePathSink } from './candles'
import { chartPalette } from './cssVar'
import { movingAverages, type MaBundle } from './ma'

/**
 * CH-03 K 线主图（QUOT-411，DESIGN-SPEC §6）：
 *
 * 上主图 = 蜡烛（红涨绿跌，A 股口径，色取 `--ldst-chart-bar-up/down`）+
 * MA5/10/20/60 四条均线（色取 `--ldst-chart-ma-1..4`）；
 * 下副图 = 成交量柱，颜色随当日阴阳。两图共用一根时间轴：
 * 光标联动走 uPlot 原生 `cursor.sync`（只同步 x 与 hover 索引），
 * 缩放联动由「日期区间滑条」单一真源驱动——两个实例各自
 * `setScale('x', …)`，不引入第三条同步通道。
 *
 * 蜡烛不引第三方库：单 series + 自定义 `paths`，uPlot 1.6.32 的
 * `Paths.stroke/fill` 接受 `Map<strokeStyle, Path2D>`（iife 内有
 * instanceof Map 分支），一次填充即可画出红绿两种实体。
 * MA 只是把收盘价滚动平均成展示序列，不参与估值/分位等口径，页面不重算行情值。
 */

const AXIS_FONT = '12px var(--ldst-font-sans)'
const MAIN_HEIGHT = 280
const VOLUME_HEIGHT = 96
const Y_AXIS_SIZE = 48
/** 滑条窗口最少可见根数（含端点） */
const MIN_WINDOW = 10
/** 初挂载默认显示最近多少根 */
const DEFAULT_WINDOW = 120
/** 两图 cursor.sync 的 key：同 key 才互相同步 */
const SYNC_KEY = 'ldst-kline'

const MA_TEXT_CLASS = ['text-chart-ma-1', 'text-chart-ma-2', 'text-chart-ma-3', 'text-chart-ma-4']

export interface CandlestickChartProps {
  bars: KlineBar[]
  /** 成交量单位（读数行「量」后缀），如「手」 */
  volumeUnit?: string
  /** 主图画布的等价文本（§9 图表要有等价文本） */
  ariaLabel?: string
  className?: string
}

function fmtPrice(value: number | null): string {
  return value === null ? MISSING : value.toFixed(2)
}

function fmtVolume(value: number | null): string {
  if (value === null) return MISSING
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`
  return value.toLocaleString('zh-CN')
}

function fmtMa(value: number | null): string {
  return value === null ? MISSING : value.toFixed(2)
}

/** 读数行单项：六项之一（日期 / 开 / 高 / 低 / 收 / 量），数字用 .ldst-num。 */
function Readout({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'up' | 'down'
}) {
  return (
    <span className="flex items-center gap-half text-caption text-text-secondary">
      {label}
      <span
        className={cn(
          'ldst-num',
          tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-text',
        )}
      >
        {value}
      </span>
    </span>
  )
}

/**
 * 底部日期区间滑条：轨道 + 可拖窗口体 + 双手柄（role="slider"）。
 * 值域是 bar 索引 [0, count-1]，浮点存窗口（拖动平滑），aria 取整。
 */
function RangeSlider({
  count,
  lo,
  hi,
  dateAt,
  onChange,
}: {
  count: number
  lo: number
  hi: number
  dateAt: (index: number) => string
  onChange: (next: { lo: number; hi: number }) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{
    mode: 'move' | 'lo' | 'hi'
    startX: number
    origLo: number
    origHi: number
  } | null>(null)

  const minSpan = MIN_WINDOW - 1
  const lastIndex = count - 1
  const pct = (value: number) => (lastIndex === 0 ? 0 : (value / lastIndex) * 100)

  const beginDrag = (
    event: React.PointerEvent<HTMLElement>,
    mode: 'move' | 'lo' | 'hi',
  ) => {
    if (lastIndex < minSpan) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { mode, startX: event.clientX, origLo: lo, origHi: hi }
  }

  const onPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    const track = trackRef.current
    if (!drag || !track) return
    const width = track.clientWidth
    if (width <= 0) return
    const delta = ((event.clientX - drag.startX) / width) * lastIndex
    if (drag.mode === 'move') {
      const span = drag.origHi - drag.origLo
      const nextLo = Math.min(Math.max(drag.origLo + delta, 0), lastIndex - span)
      onChange({ lo: nextLo, hi: nextLo + span })
      return
    }
    if (drag.mode === 'lo') {
      const nextLo = Math.min(Math.max(drag.origLo + delta, 0), drag.origHi - minSpan)
      onChange({ lo: nextLo, hi: drag.origHi })
      return
    }
    const nextHi = Math.max(Math.min(drag.origHi + delta, lastIndex), drag.origLo + minSpan)
    onChange({ lo: drag.origLo, hi: nextHi })
  }

  const endDrag = (event: React.PointerEvent<HTMLElement>) => {
    if (!dragRef.current) return
    dragRef.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>, mode: 'lo' | 'hi') => {
    const step =
      event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0
    if (event.key === 'Home') {
      event.preventDefault()
      onChange(mode === 'lo' ? { lo: 0, hi } : { lo, hi: Math.max(minSpan, lo) })
      return
    }
    if (event.key === 'End') {
      event.preventDefault()
      onChange(mode === 'lo' ? { lo: Math.min(lastIndex - minSpan, hi), hi } : { lo, hi: lastIndex })
      return
    }
    if (step === 0) return
    event.preventDefault()
    if (mode === 'lo') {
      onChange({ lo: Math.min(Math.max(lo + step, 0), hi - minSpan), hi })
    } else {
      onChange({ lo, hi: Math.max(Math.min(hi + step, lastIndex), lo + minSpan) })
    }
  }

  const sliderAria = (mode: 'lo' | 'hi') => ({
    role: 'slider' as const,
    tabIndex: 0,
    'aria-label': mode === 'lo' ? 'K线窗口起点' : 'K线窗口终点',
    'aria-valuemin': 0,
    'aria-valuemax': lastIndex,
    'aria-valuenow': Math.round(mode === 'lo' ? lo : hi),
    'aria-valuetext': dateAt(Math.round(mode === 'lo' ? lo : hi)),
    onPointerDown: (event: React.PointerEvent<HTMLElement>) => beginDrag(event, mode),
    onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => onKeyDown(event, mode),
  })

  if (lastIndex < minSpan) return null

  return (
    <div
      ref={trackRef}
      role="group"
      aria-label="K线日期区间"
      className="relative h-3 w-full cursor-default select-none rounded-pill border border-border bg-surface-2"
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {/* 窗口体：拖动平移 */}
      <div
        aria-hidden="true"
        onPointerDown={(event) => beginDrag(event, 'move')}
        className="absolute inset-y-0 cursor-grab rounded-pill bg-accent-soft"
        style={{ left: `${pct(lo)}%`, width: `${pct(hi) - pct(lo)}%` }}
      />
      <button
        type="button"
        {...sliderAria('lo')}
        className="absolute inset-y-0 z-10 -ml-1.5 w-3 cursor-col-resize rounded-pill border border-border-strong bg-surface"
        style={{ left: `${pct(lo)}%` }}
      />
      <button
        type="button"
        {...sliderAria('hi')}
        className="absolute inset-y-0 z-10 -ml-1.5 w-3 cursor-col-resize rounded-pill border border-border-strong bg-surface"
        style={{ left: `${pct(hi)}%` }}
      />
    </div>
  )
}

export function CandlestickChart({
  bars,
  volumeUnit = '手',
  ariaLabel,
  className,
}: CandlestickChartProps) {
  const mainHostRef = useRef<HTMLDivElement>(null)
  const volumeHostRef = useRef<HTMLDivElement>(null)
  const mainChartRef = useRef<uPlot | null>(null)
  const volumeChartRef = useRef<uPlot | null>(null)
  const theme = useThemeChoice()

  const [window, setWindowState] = useState({ lo: 0, hi: 0 })
  const windowRef = useRef(window)
  const setWindow = (next: { lo: number; hi: number }) => {
    windowRef.current = next
    setWindowState(next)
  }
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const barsKey = useMemo(() => JSON.stringify(bars), [bars])

  const columns = useMemo(() => {
    const opens = bars.map((bar) => bar.open)
    const highs = bars.map((bar) => bar.high)
    const lows = bars.map((bar) => bar.low)
    const closes = bars.map((bar) => bar.close)
    const volumes = bars.map((bar) => bar.volume)
    const volumeUp = bars.map((bar) =>
      bar.close !== null && bar.open !== null && bar.close >= bar.open ? bar.volume : null,
    )
    const volumeDown = bars.map((bar) =>
      bar.close !== null && bar.open !== null && bar.close < bar.open ? bar.volume : null,
    )
    return { opens, highs, lows, closes, volumes, volumeUp, volumeDown }
    // barsKey 覆盖 bars 内容
  }, [barsKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const ma = useMemo<MaBundle>(() => movingAverages(columns.closes), [columns])

  // 数据切换（周期/复权/股票）→ 窗口回到最近 DEFAULT_WINDOW 根
  useEffect(() => {
    const count = bars.length
    if (count === 0) return
    setWindow({ lo: Math.max(0, count - DEFAULT_WINDOW), hi: count - 1 })
    setHoverIdx(null)
  }, [barsKey]) // eslint-disable-line react-hooks/exhaustive-deps

  /** 主图 y 量程：可见窗口内的 high/low + 四条 MA，外扩 6%。 */
  const priceRange = (): [number, number] | null => {
    const count = bars.length
    if (count === 0) return null
    const { lo, hi } = windowRef.current
    const start = Math.max(0, Math.floor(lo))
    const end = Math.min(count - 1, Math.ceil(hi))
    let min = Infinity
    let max = -Infinity
    const push = (value: number | null) => {
      if (value === null || !Number.isFinite(value)) return
      if (value < min) min = value
      if (value > max) max = value
    }
    for (let i = start; i <= end; i++) {
      push(columns.highs[i])
      push(columns.lows[i])
      push(ma.ma5.values[i])
      push(ma.ma10.values[i])
      push(ma.ma20.values[i])
      push(ma.ma60.values[i])
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null
    const pad = Math.max((max - min) * 0.06, Math.abs(max) * 0.005, 0.01)
    return [min - pad, max + pad]
  }

  /** 副图 y 量程：可见窗口内最大量 ×1.12，从 0 起。 */
  const volumeRange = (): [number, number] => {
    const count = bars.length
    const { lo, hi } = windowRef.current
    const start = Math.max(0, Math.floor(lo))
    const end = Math.min(count - 1, Math.ceil(hi))
    let max = 0
    for (let i = start; i <= end; i++) {
      for (const value of [columns.volumeUp[i], columns.volumeDown[i]]) {
        if (value !== null && Number.isFinite(value) && value > max) max = value
      }
    }
    return [0, Math.max(max * 1.12, 1)]
  }

  // 窗口变化 → 两图同步 x；y 按新窗口显式重设（uPlot 的 setScale 不接受 null 重算）
  const applyX = () => {
    const { lo, hi } = windowRef.current
    for (const chart of [mainChartRef.current, volumeChartRef.current]) {
      chart?.setScale('x', { min: lo - 0.5, max: hi + 0.5 })
    }
    const price = priceRange()
    if (price) mainChartRef.current?.setScale('y', { min: price[0], max: price[1] })
    const volume = volumeRange()
    volumeChartRef.current?.setScale('y', { min: volume[0], max: volume[1] })
  }

  const dateAt = (index: number) => bars[index]?.date ?? ''

  useEffect(() => {
    const mainHost = mainHostRef.current
    const volumeHost = volumeHostRef.current
    if (!mainHost || !volumeHost || bars.length === 0) return

    const palette = chartPalette()
    const count = bars.length
    const xs = bars.map((_, index) => index)
    const candleData = bars.map((bar) => ({
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }))

    // 窗口变化 → 两图 setScale；uPlot 回传 setScale hook 读回（防循环靠同值比较）
    const syncHooks: uPlot.Hooks.Arrays = {
      setCursor: [
        (u) => {
          const idx = u.cursor.idx ?? null
          setHoverIdx(idx === null || idx < 0 ? null : Math.min(idx, count - 1))
        },
      ],
      setScale: [
        (u, key) => {
          if (key !== 'x') return
          const min = u.scales.x.min
          const max = u.scales.x.max
          if (min === undefined || max === undefined || min === null || max === null) return
          const lo = min + 0.5
          const hi = max - 0.5
          const current = windowRef.current
          if (Math.abs(lo - current.lo) < 1e-6 && Math.abs(hi - current.hi) < 1e-6) return
          setWindow({ lo, hi })
        },
      ],
    }

    const cursor: uPlot.Cursor = {
      drag: { x: false, y: false },
      points: { show: false },
      sync: { key: SYNC_KEY, scales: ['x', null] },
    }

    const candleSeries: uPlot.Series = {
      label: 'K线',
      stroke: palette.barUp,
      width: 1,
      points: { show: false },
      paths: (u, _seriesIdx, _idx0, _idx1) => {
        const upWick = new Path2D()
        const upBody = new Path2D()
        const downWick = new Path2D()
        const downBody = new Path2D()
        const spacing =
          Math.abs(u.valToPos(1, 'x', true) - u.valToPos(0, 'x', true)) || 8
        const layout = {
          xAt: (i: number) => u.valToPos(i, 'x', true),
          yAt: (price: number) => u.valToPos(price, 'y', true),
          bodyWidth: candleBodyWidth(spacing),
        }
        const makeSink = (wick: Path2D, body: Path2D): CandlePathSink => ({
          moveTo: (x, y) => wick.moveTo(x, y),
          lineTo: (x, y) => wick.lineTo(x, y),
          rect: (x, y, w, h) => body.rect(x, y, w, h),
        })
        drawCandles(candleData, layout, makeSink(upWick, upBody), makeSink(downWick, downBody))
        return {
          stroke: new Map([
            [palette.barUp, upWick],
            [palette.barDown, downWick],
          ]),
          fill: new Map([
            [palette.barUp, upBody],
            [palette.barDown, downBody],
          ]),
          width: 1,
        }
      },
    }

    const barPaths = uPlot.paths.bars?.({ size: [0.55, 16], align: 1 })

    const mainOptions: uPlot.Options = {
      width: Math.max(mainHost.clientWidth, 240),
      height: MAIN_HEIGHT,
      padding: [8, 8, 0, 0],
      legend: { show: false },
      cursor,
      hooks: syncHooks,
      scales: {
        x: { time: false },
        // 初建时按当前窗口取量程；窗口变化由 applyX 显式 setScale('y') 重设
        y: { range: () => priceRange() ?? [0, 1] },
      },
      axes: [
        { show: false },
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          size: Y_AXIS_SIZE,
          grid: { stroke: palette.grid, width: 1 },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
        },
      ],
      series: [
        {},
        candleSeries,
        { label: '开盘', show: false },
        { label: '最高', show: false },
        { label: '最低', show: false },
        { label: 'MA5', stroke: palette.ma1, width: 1, points: { show: false } },
        { label: 'MA10', stroke: palette.ma2, width: 1, points: { show: false } },
        { label: 'MA20', stroke: palette.ma3, width: 1, points: { show: false } },
        { label: 'MA60', stroke: palette.ma4, width: 1, points: { show: false } },
      ],
    }

    const volumeOptions: uPlot.Options = {
      width: Math.max(volumeHost.clientWidth, 240),
      height: VOLUME_HEIGHT,
      padding: [8, 8, 0, 0],
      legend: { show: false },
      cursor,
      hooks: syncHooks,
      scales: {
        x: { time: false },
        y: { range: () => volumeRange() },
      },
      axes: [
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          grid: { show: false },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
          values: (_u, splits) =>
            splits.map((value) => bars[Math.round(value)]?.date ?? ''),
        },
        {
          stroke: palette.axis,
          font: AXIS_FONT,
          size: Y_AXIS_SIZE,
          grid: { stroke: palette.grid, width: 1 },
          ticks: { stroke: palette.grid, size: 4, width: 1 },
          values: (_u, splits) =>
            splits.map((value) =>
              Math.abs(value) >= 1e8
                ? `${(value / 1e8).toFixed(1)}亿`
                : Math.abs(value) >= 1e4
                  ? `${(value / 1e4).toFixed(0)}万`
                  : String(value),
            ),
        },
      ],
      series: [
        {},
        {
          label: '成交量（阳）',
          stroke: palette.barUp,
          fill: palette.barUp,
          width: 0,
          points: { show: false },
          paths: barPaths,
        },
        {
          label: '成交量（阴）',
          stroke: palette.barDown,
          fill: palette.barDown,
          width: 0,
          points: { show: false },
          paths: barPaths,
        },
      ],
    }

    const mainData: uPlot.AlignedData = [
      xs,
      columns.closes,
      columns.opens,
      columns.highs,
      columns.lows,
      ma.ma5.values,
      ma.ma10.values,
      ma.ma20.values,
      ma.ma60.values,
    ]
    const volumeData: uPlot.AlignedData = [xs, columns.volumeUp, columns.volumeDown]

    const mainChart = new uPlot(mainOptions, mainData, mainHost)
    const volumeChart = new uPlot(volumeOptions, volumeData, volumeHost)
    mainChartRef.current = mainChart
    volumeChartRef.current = volumeChart
    applyX()

    const resize = () => {
      mainChart.setSize({ width: Math.max(mainHost.clientWidth, 240), height: MAIN_HEIGHT })
      volumeChart.setSize({ width: Math.max(volumeHost.clientWidth, 240), height: VOLUME_HEIGHT })
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mainHost)

    return () => {
      observer.disconnect()
      mainChartRef.current = null
      volumeChartRef.current = null
      mainChart.destroy()
      volumeChart.destroy()
    }
    // columns/ma 由 barsKey 派生；theme 变化必须重读 CSS 变量重绘
  }, [barsKey, columns, ma, theme]) // eslint-disable-line react-hooks/exhaustive-deps

  // 窗口变化 → 同步两图（滑条拖动、键盘、setScale 读回共用）
  useEffect(() => {
    applyX()
  }, [window.lo, window.hi])

  if (bars.length === 0) return null

  const count = bars.length
  const readIdx = hoverIdx !== null && hoverIdx < count ? hoverIdx : count - 1
  const readBar = bars[readIdx]
  const bullish =
    readBar.close !== null && readBar.open !== null && readBar.close >= readBar.open
  const tone = bullish ? 'up' : 'down'
  const maRows: Array<{ label: string; value: number | null; className: string }> = [
    { label: 'MA5', value: ma.ma5.values[readIdx], className: MA_TEXT_CLASS[0] },
    { label: 'MA10', value: ma.ma10.values[readIdx], className: MA_TEXT_CLASS[1] },
    { label: 'MA20', value: ma.ma20.values[readIdx], className: MA_TEXT_CLASS[2] },
    { label: 'MA60', value: ma.ma60.values[readIdx], className: MA_TEXT_CLASS[3] },
  ]

  return (
    <div className={cn('flex flex-col', className)}>
      {/* 读数行：悬停十字光标对应根，未悬停显示最新一根 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-2 text-caption">
        <span className="ldst-num text-text-secondary">{readBar.date}</span>
        <Readout label="开" value={fmtPrice(readBar.open)} tone={tone} />
        <Readout label="高" value={fmtPrice(readBar.high)} tone={tone} />
        <Readout label="低" value={fmtPrice(readBar.low)} tone={tone} />
        <Readout label="收" value={fmtPrice(readBar.close)} tone={tone} />
        <Readout label={`量（${volumeUnit}）`} value={fmtVolume(readBar.volume)} />
        <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
          {maRows.map((row) => (
            <span key={row.label} className="flex items-center gap-half text-caption text-text-muted">
              <span className={row.className}>{row.label}</span>
              <span className="ldst-num text-text-secondary">{fmtMa(row.value)}</span>
            </span>
          ))}
        </span>
      </div>

      <div
        ref={mainHostRef}
        role="img"
        aria-label={ariaLabel ?? 'K线主图：蜡烛与均线'}
        className="w-full"
      />
      <div
        ref={volumeHostRef}
        role="img"
        aria-label="K线副图：成交量"
        className="mt-1 w-full"
      />

      <div className="mt-2">
        <RangeSlider count={count} lo={window.lo} hi={window.hi} dateAt={dateAt} onChange={setWindow} />
      </div>
    </div>
  )
}
