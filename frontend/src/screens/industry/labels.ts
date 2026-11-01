import { DIMENSION_TITLE, isIndustryDimension, type DataSourceStatus } from '../../api/industry'

/**
 * 产业两屏（`/industry` 与 `/industry/:name`）共用的枚举词表与跳转件类名。
 *
 * 词表只有一份：同一枚「数据完整度」徽标在矩阵与详情里必须是同样的四个字，
 * 以前两屏各写一套（完整/部分/缺失 vs 数据完整/部分缺失/无数据）就是「粗糙」的来源。
 *
 * `data_source_status` 的契约枚举是 `ok|partial|missing`（`api/industry.ts`），
 * 与 UI-SPEC §5.2 的 `ok|degraded|partial` 不同轨：产业侧没有「已降级」这一态，
 * `missing`（输入数据集无可用记录）取最重色 danger。该词表差异已作为规范缺口上报 S1b。
 */

export type IndustryStatus = DataSourceStatus

export const DATA_SOURCE_STATUS_LABEL: Record<IndustryStatus, string> = {
  ok: '完整',
  partial: '部分',
  missing: '缺失',
}

export const DATA_SOURCE_STATUS_TONE: Record<IndustryStatus, 'ok' | 'warn' | 'danger'> = {
  ok: 'ok',
  partial: 'warn',
  missing: 'danger',
}

/**
 * 「长得像按钮的链接」必须与 C-01 `Button` 的 `secondary` 同手感：
 * 同一份 `transition-all duration-fast ease-ldst` + `active:translate-y-px` + `font-medium`。
 * 组件层的 `Button as={Link}`（清单 M-2）落地后，本常量与各使用点一起删掉。
 */
export const LINK_BUTTON_CLASS =
  'inline-flex h-control items-center justify-center gap-1 rounded border border-border-strong bg-surface px-3 text-body font-medium text-text transition-all duration-fast ease-ldst active:translate-y-px hover:bg-surface-3'

/** 普通文字链接的过渡档：hover 变色必须有 `duration-fast`，不能是瞬变。 */
export const LINK_TEXT_CLASS =
  'text-accent underline-offset-2 transition-colors duration-fast ease-ldst hover:text-accent-hover hover:underline'

/**
 * 后端下发的维度键（`formulas` 的键、指标路径前缀）转中文。
 *
 * 五维是封闭枚举（`INDUSTRY_DIMENSIONS`），正常一定命中；命中不了说明契约外的键进来了，
 * 保持原样并交给调用方的 `title` 说明，不在此处编造标签。
 */
export function dimensionTitleOf(key: string): string {
  return isIndustryDimension(key) ? DIMENSION_TITLE[key] : key
}
