import { ShieldCheck } from 'lucide-react'

import {
  POLICY_FALLBACK_HINTS,
  POLICY_FALLBACK_LABELS,
  POLICY_ORDER,
  normalizePolicy,
  type ConfirmPolicy,
  type PolicyDescribe,
} from '../api/dataPolicy'
import { Select } from './ui'

/**
 * 输入框底行的数据权限档选择器（F6）。
 *
 * 三档语义全部来自服务端 `GET /api/data/policy` 的 `describe`（`core/policy.py` 是
 * 唯一口径）；没拿到时用同文案兜底，不让下拉变成空白。切换是「乐观更新 + 失败回滚」，
 * 失败原因挂在 `title` 与调用方的提示上，不打断正在输入的正文。
 */
export interface DataPolicyPickerProps {
  value: ConfirmPolicy
  describe: PolicyDescribe | null
  busy?: boolean
  error?: string | null
  onChange: (next: ConfirmPolicy) => void
  className?: string
}

export function policyLabel(policy: ConfirmPolicy, describe: PolicyDescribe | null): string {
  return describe?.labels?.[policy] ?? POLICY_FALLBACK_LABELS[policy]
}

export function policyHint(policy: ConfirmPolicy, describe: PolicyDescribe | null): string {
  return describe?.descriptions?.[policy] ?? POLICY_FALLBACK_HINTS[policy]
}

export function DataPolicyPicker({
  value,
  describe,
  busy = false,
  error = null,
  onChange,
  className,
}: DataPolicyPickerProps) {
  const hint = error ?? `数据权限：${policyLabel(value, describe)}——${policyHint(value, describe)}`
  return (
    <span className={`inline-flex min-w-0 items-center gap-1 ${className ?? ''}`} title={hint}>
      <ShieldCheck className="size-3.5 shrink-0 text-text-muted" aria-hidden="true" />
      <Select
        aria-label="数据权限档"
        value={value}
        disabled={busy}
        title={hint}
        onChange={(event) => onChange(normalizePolicy(event.target.value))}
        className="h-7 w-auto min-w-0 border-0 bg-transparent px-1 pr-5 text-caption text-text-secondary"
      >
        {POLICY_ORDER.map((policy) => (
          <option key={policy} value={policy}>
            {policyLabel(policy, describe)}
          </option>
        ))}
      </Select>
    </span>
  )
}
