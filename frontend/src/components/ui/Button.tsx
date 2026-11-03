import { forwardRef, useId, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * C-01 Button / C-02 IconButton（DESIGN-SPEC §5）。
 *
 * 一屏只能有一个 primary（页面主操作）。卡片内一律 secondary / ghost。
 * 状态靠全局 `:focus-visible` 的 focus.ring（styles/index.css）提供，组件不写 hex。
 */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'md' | 'lg'

/**
 * `transition-all`：底色之外还要动 `translate`，而 `transition-colors` 覆盖不到 transform。
 * `active:translate-y-px` 是按压反馈的主通道——靠底色深浅表达 press 在浅色主题下几乎
 * 看不出来（surface-3 `#edf1f7` 与 sunken `#e9eef4` 只差一档），位移两个主题都可见。
 */
const BASE =
  'inline-flex select-none items-center justify-center gap-1 rounded font-medium transition-all duration-fast ease-ldst active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50'

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent-solid text-accent-contrast hover:bg-accent-solid-hover active:bg-accent-solid-hover',
  secondary: 'border border-border-strong bg-surface text-text hover:bg-surface-3',
  ghost: 'bg-transparent text-text-secondary hover:bg-surface-3 hover:text-text',
  danger: 'border border-danger bg-transparent text-danger hover:bg-danger-soft',
}

const SIZES: Record<ButtonSize, string> = {
  md: 'h-control px-3 text-body',
  lg: 'h-control-lg px-4 text-body',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  leadingIcon?: ReactNode
  /** 禁用原因（§4.8）：置灰必须给出看得见的理由，渲染在按钮右侧的小字上 */
  disabledReason?: string
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    leadingIcon,
    className,
    children,
    disabled,
    type,
    disabledReason,
    ...rest
  },
  ref,
) {
  const reasonId = useId()

  const button = (
    <button
      ref={ref}
      type={type ?? 'button'}
      disabled={disabled || loading}
      aria-describedby={disabledReason ? reasonId : undefined}
      className={cn(BASE, VARIANTS[variant], SIZES[size], className)}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : leadingIcon}
      <span>{children}</span>
    </button>
  )

  if (!disabledReason) return button

  return (
    <span className="inline-flex items-center gap-2">
      {button}
      <span id={reasonId} className="text-caption text-text-muted">
        {disabledReason}
      </span>
    </span>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  variant?: 'ghost' | 'secondary'
  icon: ReactNode
}

/** 图标按钮：32×32，必须给动作名（`aria-label`），不是图标名。 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, variant = 'ghost', icon, className, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex size-8 shrink-0 items-center justify-center rounded transition-all duration-fast ease-ldst active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50',
        variant === 'ghost'
          ? 'text-text-secondary hover:bg-surface-3 hover:text-text'
          : 'border border-border-strong bg-surface text-text hover:bg-surface-3',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  )
})
