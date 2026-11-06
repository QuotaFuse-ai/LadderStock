import type { CSSProperties } from 'react'

import { cn } from '../../lib/cn'
import { hideIf, usePrivacyMode } from '../../lib/privacy'
import { Input, type InputProps } from '../../components/ui'

/**
 * 持仓页的隐私遮罩：**只有这一份**，金额 / 股数 / 成本 / 盈亏都走它。
 *
 * 口径来自 `lib/privacy.ts`（键 `ldst.privacy`、掩码 `MASK`），开关在顶栏：
 * - 遮：数量、成本价、市值、盈亏金额、已实现、分红、表单里的自有数字；
 * - 不遮：现价（公开行情）、盈亏%（单凭比例推不出本金）、`—`（没有数）。
 */

export interface PrivacyTextProps {
  /** 已经格式化好的文本；`—` 原样保留，不遮。 */
  text: string
  className?: string
  title?: string
}

/** 表格单元格 / 指标读数用：文本按需换成掩码。 */
export function PrivacyText({ text, className, title }: PrivacyTextProps) {
  const { on } = usePrivacyMode()
  return (
    <span className={cn(className)} title={title}>
      {hideIf(on, text)}
    </span>
  )
}

const SECRET_INPUT = { WebkitTextSecurity: 'disc' } as CSSProperties

/** 表单里的自有数字：隐私模式下用圆点代替字符，值本身仍可编辑与校验。 */
export function PrivacyInput({ style, ...rest }: InputProps) {
  const { on } = usePrivacyMode()
  return <Input {...rest} style={on ? { ...style, ...SECRET_INPUT } : style} />
}
