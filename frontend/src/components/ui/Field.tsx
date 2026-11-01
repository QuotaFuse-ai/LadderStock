import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

/**
 * C-03 Input / C-04 Select / C-05 Switch / C-06 Checkbox（DESIGN-SPEC §5）。
 *
 * 边界一律 `border-strong`（≥3:1，WCAG 1.4.11），内底 `sunken`；
 * 错误态用 `border-danger` + 文字说明，不只靠颜色。
 */

/**
 * 焦点环由全局 `:focus-visible`（`styles/index.css` + `--ldst-focus-ring`）提供，组件这边
 * 只用 `focus-visible:` 提亮边框：写 `focus:` 会让鼠标点击也进入「聚焦」观感，
 * 用户分不清自己是在编辑还是只是点过一下。
 */
const CONTROL =
  'w-full rounded border border-border-strong bg-sunken px-2 text-body text-text placeholder:text-text-muted transition-colors duration-fast ease-ldst disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-accent'

export interface FieldProps {
  label: ReactNode
  htmlFor?: string
  hint?: ReactNode
  error?: string | null
  required?: boolean
  className?: string
  children: ReactNode
}

/** 字段外壳：标签 + 控件 + 说明 / 错误（错误必须可被读屏读到）。 */
export function Field({ label, htmlFor, hint, error, required, className, children }: FieldProps) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="text-caption text-text-secondary">
        {label}
        {required && (
          <span className="ml-half text-danger" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p className="text-caption text-danger" role="alert">
          {error}
        </p>
      ) : (
        hint && <p className="text-caption text-text-muted">{hint}</p>
      )}
    </div>
  )
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
  leadingIcon?: ReactNode
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, invalid, leadingIcon, ...rest },
  ref,
) {
  if (!leadingIcon) {
    return (
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(CONTROL, 'h-control', invalid && 'border-danger', className)}
        {...rest}
      />
    )
  }
  return (
    <span className="relative inline-flex w-full items-center">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-2 flex text-text-muted"
      >
        {leadingIcon}
      </span>
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(CONTROL, 'h-control pl-7', invalid && 'border-danger', className)}
        {...rest}
      />
    </span>
  )
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(CONTROL, 'min-h-textarea resize-y py-2 leading-body', className)}
        {...rest}
      />
    )
  },
)

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, invalid, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(CONTROL, 'h-control pr-6', invalid && 'border-danger', className)}
      {...rest}
    >
      {children}
    </select>
  )
})

export interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
  id?: string
}

/** 开关：36×20 轨道 + 16px 圆钮，点击区 ≥32px（外层 min-h-control）。 */
export function Switch({ checked, onChange, label, description, disabled, id }: SwitchProps) {
  const autoId = useId()
  const controlId = id ?? autoId
  return (
    <div className="flex min-h-control items-center justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={controlId} className="text-body text-text">
          {label}
        </label>
        {description && <p className="text-caption text-text-muted">{description}</p>}
      </div>
      <button
        id={controlId}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative inline-flex h-5 w-10 shrink-0 items-center rounded-pill border p-half transition-colors duration-fast ease-ldst disabled:cursor-not-allowed disabled:opacity-50',
          checked ? 'border-accent-solid bg-accent-solid' : 'border-border-strong bg-sunken',
        )}
      >
        {/* 位移走 translate：auto 外边距不可插值，滑块原本是从左端瞬移到右端的 */}
        <span
          aria-hidden="true"
          className={cn(
            'inline-block size-4 rounded-pill bg-surface shadow-card transition-transform duration-fast ease-ldst',
            checked && 'translate-x-switch-knob',
          )}
        />
      </button>
    </div>
  )
}

export interface CheckboxProps {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  id?: string
  className?: string
  children: ReactNode
}

/** 勾选框：原生 input + 自定义外观，保留键盘与读屏行为。 */
export function Checkbox({ checked, onChange, disabled, id, className, children }: CheckboxProps) {
  const autoId = useId()
  const controlId = id ?? autoId
  return (
    <div className={cn('flex items-start gap-2', className)}>
      <input
        id={controlId}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 shrink-0 cursor-pointer rounded-xs accent-accent-solid disabled:cursor-not-allowed"
      />
      <label htmlFor={controlId} className="text-body leading-body text-text">
        {children}
      </label>
    </div>
  )
}
