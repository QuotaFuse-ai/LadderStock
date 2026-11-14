import { Eye, EyeOff, Moon, Sparkles, Sun } from 'lucide-react'

import PlatformStatusLight from './PlatformStatusLight'
import { cn } from '../lib/cn'
import { useIsMobile } from '../lib/mediaQuery'
import { usePrivacyMode } from '../lib/privacy'
import { setTheme, useThemeChoice } from '../lib/prefs'
import { resolveTheme } from '../lib/theme'
import { Button } from './ui'

/**
 * 顶栏右侧工具区（DEC-062 §2）：**服务状态灯** → `本地 A 股短线工作台` 小字 →
 * 隐私开关 → 深浅色 → 「接入 AI」配置图标 → 「问 AI」主按钮（QUOT-475）。
 *
 * 状态灯是最左一格（QUOT-443 / DEC-063）：红灯才出「重启平台」，绿 / 黄不占位。
 * 抽成独立组件是硬约束：顶栏结构只在 `Layout.tsx` 维护，工具内容只改这里。
 *
 * 顺序与文案逐字对照参考实现 `components/layout/Layout.tsx:297-308`；
 * 颜色只走 `tokens.css` 语义类（`text-accent` / `text-text-muted` / `hover:bg-surface-3`）。
 *
 * 「问 AI」与「接入 AI」语义不同，**不合并**：前者是随时开口的研究助手入口，
 * 后者是配置模型通道的次级图标。
 *
 * 窄屏（< md）顶栏不渲染「问 AI」，入口整体挪到 `MobileNavDrawer` 底部，
 * 同一条口子（`onAskAi`），不另起一份逻辑。
 */
export interface TopbarToolsProps {
  /** 接入 AI（配置 key） */
  onOpenAi: () => void
  /** 问 AI（打开全站唯一的助手抽屉；首页上改为聚焦既有输入框） */
  onAskAi: () => void
  /** 首页（QUOT-590）：home 已有 chat / desk 两颗 ask 面，问 AI 只聚焦不另开抽屉 */
  homeAskFocus?: boolean
}

export function TopbarTools({ onOpenAi, onAskAi, homeAskFocus = false }: TopbarToolsProps) {
  const privacy = usePrivacyMode()
  const theme = useThemeChoice()
  const dark = resolveTheme(theme) === 'dark'
  const isMobile = useIsMobile()

  return (
    <div
      className="flex shrink-0 items-center gap-1.5"
      style={{ marginRight: 'var(--ai-trigger-space, 0px)' }}
    >
      <PlatformStatusLight />

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

      {!isMobile && (
        <Button
          variant="secondary"
          onClick={onAskAi}
          aria-label={homeAskFocus ? '聚焦研究 / 对话输入' : '问 AI'}
          title={
            homeAskFocus
              ? '聚焦首页研究 / 对话输入框（首页已有研究面板与对话，不再另开抽屉）'
              : '问 AI：按当前页面绑定技能，打开研究助手'
          }
          aria-haspopup={homeAskFocus ? undefined : 'dialog'}
          leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
          className="ml-0.5 border-accent px-2.5 text-accent hover:bg-accent-soft hover:text-accent"
        >
          问 AI
        </Button>
      )}
    </div>
  )
}

export default TopbarTools
