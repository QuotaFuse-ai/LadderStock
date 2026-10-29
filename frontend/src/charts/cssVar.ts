/**
 * 唯一允许出现颜色字面量的模块之一（另一个是 `src/styles/tokens.css`）。
 *
 * 原因：uPlot 画在 `<canvas>` 上，取不到 CSS 变量，必须先把 `--ldst-chart-*`
 * 解析成具体色值。主题切换时调用方会重新读一遍并重绘，所以这里不做缓存。
 * 其余组件一律用 Tailwind 语义类（`bg-surface` / `text-up` …），不写颜色。
 */

/** 仅在 CSS 变量读不到时兜底（例如测试环境 jsdom 没有样式表），与 tokens.css 的暗色值一致 */
const FALLBACK: Record<string, string> = {
  '--ldst-chart-grid': '#26303c',
  '--ldst-chart-axis': '#8a96a6',
  '--ldst-chart-band': 'rgba(76, 141, 255, 0.12)',
  '--ldst-chart-line': '#4c8dff',
  '--ldst-chart-bar-up': '#ff5c5c',
  '--ldst-chart-bar-down': '#2fbf71',
  '--ldst-chart-series-2': '#a78bfa',
  '--ldst-chart-threshold': '#f0b429',
  '--ldst-chart-empty': '#2a3342',
  '--ldst-accent': '#4c8dff',
  '--ldst-text-muted': '#8a96a6',
}

export const TRANSPARENT = 'rgba(0, 0, 0, 0)'

export function cssVar(name: string): string {
  if (typeof window === 'undefined') return FALLBACK[name] ?? FALLBACK['--ldst-chart-line']
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || FALLBACK[name] || FALLBACK['--ldst-chart-line']
}

export interface ChartPalette {
  grid: string
  axis: string
  band: string
  line: string
  barUp: string
  barDown: string
  series2: string
  threshold: string
  empty: string
}

/** 每次重绘前读一次，保证深浅主题切换后颜色跟着变。 */
export function chartPalette(): ChartPalette {
  return {
    grid: cssVar('--ldst-chart-grid'),
    axis: cssVar('--ldst-chart-axis'),
    band: cssVar('--ldst-chart-band'),
    line: cssVar('--ldst-chart-line'),
    barUp: cssVar('--ldst-chart-bar-up'),
    barDown: cssVar('--ldst-chart-bar-down'),
    series2: cssVar('--ldst-chart-series-2'),
    threshold: cssVar('--ldst-chart-threshold'),
    empty: cssVar('--ldst-chart-empty'),
  }
}
