import { DIMENSION_TITLE, isIndustryDimension } from '../../api/industry'

/**
 * 产业两屏（`/industry` 与 `/industry/:name`）共用的跳转件类名与维度键翻译。
 *
 * 「数据完整度」徽标不在这里：标签表 / 色序 / 图形只允许一份实现，
 * 同源件是 `./sourceBadges`（屏侧临时件，真源 `components/ui/SourceBadges.tsx` 已落 main，
 * 差异只在产业与 radar 家族的 `data_source_status` 多一个 `missing` 档）。
 * 本屏一度自带 `完整/部分/缺失` 三个字的私有词表，与 §5.2 标签表逐字不一致，已删。
 */

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
