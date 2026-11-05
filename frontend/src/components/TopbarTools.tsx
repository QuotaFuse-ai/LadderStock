import { Eye, EyeOff, Moon, Sparkles, Sun } from 'lucide-react'

import { cn } from '../lib/cn'
import { usePrivacyMode } from '../lib/privacy'
import { setTheme, useThemeChoice } from '../lib/prefs'
import { resolveTheme } from '../lib/theme'

/**
 * 顶栏右侧工具区（DEC-062 §2）：`本地 A 股短线工作台` 小字 → 隐私开关 → 深浅色 → AI 入口。
 *
 * 抽成独立组件是硬约束：下一个任务要在**最左侧**插入服务状态灯，届时只改这里，
 * 不再动 `Layout.tsx` 的顶栏结构。
 *
 * 顺序与文案逐字对照参考实现 `components/layout/Layout.tsx:297-308`；
 * 颜色只走 `tokens.css` 语义类（`text-accent` / `text-text-muted` / `hover:bg-surface-3`）。
 */
export interface TopbarToolsProps {
  onOpenAi: () => void
}

export function TopbarTools({ onOpenAi }: TopbarToolsProps) {
  const privacy = usePrivacyMode()
  const theme = useThemeChoice()
  const dark = resolveTheme(theme) === 'dark'

  return (
    <div
      className="flex shrink-0 items-center gap-1.5"
      style={{ marginRight: 'var(--ai-trigger-space, 0px)' }}
    >
      <span className="hidden text-caption text-text-muted lg:inline">本地 A 股短线工作台</span>

      <button
        type="button"
        onClick={privacy.toggle}
        aria-pressed={privacy.on}
        aria-label={privacy.on ? '关闭隐私模式' : '打开隐私模式'}
        title={
          privacy.on
            ? '隐私模式已打开：金额、股数显示成 ••••。点一下恢复显示'
            : '隐私模式：录屏、截图前打开，金额和股数显示成 ••••，百分比照常。只遮屏幕显示，不影响计算和发给 AI 的内容'
        }
        className={cn(
          'rounded p-1.5 transition-colors duration-fast hover:bg-surface-3',
          privacy.on ? 'text-accent' : 'text-text-muted hover:text-text',
        )}
      >
        {privacy.on ? (
          <EyeOff className="size-4" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <Eye className="size-4" strokeWidth={1.75} aria-hidden="true" />
        )}
      </button>

      <button
        type="button"
        onClick={() => setTheme(dark ? 'light' : 'dark')}
        aria-label={dark ? '切换为浅色' : '切换为深色'}
        title={dark ? '切换为浅色' : '切换为深色'}
        className="rounded p-1.5 text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text"
      >
        {dark ? (
          <Sun className="size-4" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <Moon className="size-4" strokeWidth={1.75} aria-hidden="true" />
        )}
      </button>

      <button
        type="button"
        onClick={onOpenAi}
        aria-label="接入 AI"
        title="接入 AI"
        aria-haspopup="dialog"
        className="rounded p-1.5 text-text-muted transition-colors duration-fast hover:bg-surface-3 hover:text-text"
      >
        <Sparkles className="size-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
      </button>
    </div>
  )
}

export default TopbarTools
