import type { Config } from 'tailwindcss'

import tokens from './tokens.json'

/**
 * Tailwind 配置：`theme.extend` 全部由 tokens.json 生成，**不写任何字面量**。
 *
 * 约定（DESIGN-SPEC §2.12）：
 * - 颜色 / 圆角 / 阴影 / 字号 / 间距一律映射到 CSS 变量 `var(--ldst-*)`，
 *   因为 hex 一旦进配置，深浅主题就没法在不重新构建的前提下切换。
 * - 语义类名与令牌名一一对应：`bg-surface-2` ↔ `--ldst-surface-2`，
 *   `text-text-muted` ↔ `--ldst-text-muted`，`shadow-card` ↔ `--ldst-shadow-card`。
 * - `tokens.json` 是 docs/ 那份的派生物（scripts/gen-tokens.mjs 生成），不手改。
 */

const cssVar = (name: string): string => `var(${name})`

type Themed = { cssVar: string; dark: string; light: string }
type Token = { cssVar: string; tailwind?: string }

/** 颜色令牌 → `colors` 键（去掉 `colors.` 前缀） */
function colorScale(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const token of Object.values(tokens.color) as Token[]) {
    out[token.cssVar.replace('--ldst-', '')] = cssVar(token.cssVar)
  }
  for (const token of Object.values(tokens.chart) as Token[]) {
    out[token.cssVar.replace('--ldst-', '')] = cssVar(token.cssVar)
  }
  return out
}

const colors = colorScale()

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-ldst-theme="dark"]'],
  theme: {
    // 设计基准 1280；1024–1279 收窄为图标栏（单栏、指标三列，允许下移但不横向溢出）
    screens: {
      // 1024–1279 收窄为 56px 图标栏；1280 起完整侧栏（DESIGN-SPEC §3.1）
      rail: { max: '1279px' },
      desk: tokens.layout.breakpointBase.value,
    },
    extend: {
      colors: {
        ...colors,
        chart: {
          grid: cssVar(tokens.chart.grid.cssVar),
          axis: cssVar(tokens.chart.axis.cssVar),
          band: cssVar(tokens.chart.band.cssVar),
          line: cssVar(tokens.chart.line.cssVar),
          threshold: cssVar(tokens.chart.threshold.cssVar),
          empty: cssVar(tokens.chart.empty.cssVar),
        },
      },
      borderColor: {
        DEFAULT: cssVar(tokens.color.border.cssVar),
      },
      fontFamily: {
        sans: cssVar(tokens.typography.sans.cssVar),
        mono: cssVar(tokens.typography.mono.cssVar),
      },
      fontSize: Object.fromEntries(
        Object.entries(tokens.typography.size).map(([key, size]) => [
          key,
          [cssVar(size.cssVar), `${size.lineHeight}`],
        ]),
      ),
      fontWeight: {
        regular: `${tokens.typography.weight.regular.value}`,
        medium: `${tokens.typography.weight.medium.value}`,
        semibold: `${tokens.typography.weight.semibold.value}`,
      },
      lineHeight: {
        tight: `${tokens.typography.lineHeight.tight.value}`,
        body: `${tokens.typography.lineHeight.body.value}`,
        relaxed: `${tokens.typography.lineHeight.relaxed.value}`,
      },
      spacing: Object.fromEntries(
        Object.entries(tokens.space).map(([key, space]) => [key, space.value]),
      ),
      borderRadius: {
        DEFAULT: cssVar(tokens.radius.default.cssVar),
        xs: cssVar(tokens.radius.xs.cssVar),
        pill: cssVar(tokens.radius.pill.cssVar),
      },
      boxShadow: {
        card: cssVar(tokens.shadow.card.cssVar),
        overlay: cssVar(tokens.shadow.overlay.cssVar),
        dialog: cssVar(tokens.shadow.dialog.cssVar),
        focus: cssVar(tokens.focus.ring.cssVar),
      },
      width: {
        sidebar: cssVar(tokens.layout.sidebar.cssVar),
        'sidebar-rail': '56px',
        content: cssVar(tokens.layout.contentMax.cssVar),
        table: cssVar(tokens.layout.tableMax.cssVar),
        /* 结构尺寸（非视觉令牌）：§4.8 设置二级菜单 176 / §5 C-15 抽屉 480|640 / C-16 确认框 420 */
        menu: '176px',
        drawer: '480px',
        'drawer-wide': '640px',
        dialog: '420px',
      },
      height: {
        topbar: cssVar(tokens.layout.topbar.cssVar),
        control: cssVar(tokens.layout.control.cssVar),
        'control-lg': cssVar(tokens.layout.controlLg.cssVar),
        row: cssVar(tokens.layout.row.cssVar),
        'row-compact': cssVar(tokens.layout.rowCompact.cssVar),
        'row-comfortable': cssVar(tokens.layout.rowComfortable.cssVar),
        icon: cssVar(tokens.layout.iconButton.cssVar),
        /* §4.5 指标卡固定 96px（卡片标题 + 数值 + 变动） */
        metric: '96px',
      },
      minHeight: {
        control: cssVar(tokens.layout.control.cssVar),
        row: cssVar(tokens.layout.row.cssVar),
        empty: '200px',
      },
      minWidth: {
        icon: cssVar(tokens.layout.iconButton.cssVar),
      },
      maxWidth: {
        content: cssVar(tokens.layout.contentMax.cssVar),
        table: cssVar(tokens.layout.tableMax.cssVar),
      },
      zIndex: {
        sticky: `${tokens.layout.zSticky.value}`,
        banner: `${tokens.layout.zBanner.value}`,
        overlay: `${tokens.layout.zOverlay.value}`,
        dialog: `${tokens.layout.zDialog.value}`,
        toast: `${tokens.layout.zToast.value}`,
      },
      transitionDuration: {
        fast: tokens.motion['duration-fast'].value,
        base: tokens.motion['duration-base'].value,
      },
      transitionTimingFunction: {
        ldst: tokens.motion.ease.value,
      },
    },
  },
  plugins: [],
} satisfies Config
