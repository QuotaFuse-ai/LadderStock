import type { CSSProperties } from 'react'

/**
 * 热力图色阶（DEC-028）：只按「同列相对位置」上色，不产生新指标、不判断好坏。
 *
 * - 数值列：`--ldst-accent` 按 rank 比例混入 `--ldst-surface`（4%–52%）；
 * - 带正负号的列：红涨绿跌用 `--ldst-up-soft` / `--ldst-down-soft`，不做深浅分级；
 * - 颜色全部由令牌经 `color-mix()` 生成，业务代码不出现色值。
 */

export const HEAT_MIN_PERCENT = 4
export const HEAT_MAX_PERCENT = 52

/** 同列内数值的相对位置：最小值 0，最大值 1；并列取并列组的中位位置；空列返回 null。 */
export function rankRatio(values: ReadonlyArray<number | null>, value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null
  const finite = values
    .filter((item): item is number => typeof item === 'number' && Number.isFinite(item))
    .sort((a, b) => a - b)
  if (finite.length === 0) return null
  if (finite[0] === finite[finite.length - 1]) return 0.5
  const below = finite.filter((item) => item < value).length
  const equal = finite.filter((item) => item === value).length
  const rank = below + (equal - 1) / 2
  return rank / (finite.length - 1)
}

/** 数值列底色：rank 0→4%，1→52%（最低值也有一点底色，便于识别「有数」）。 */
export function levelHeatStyle(ratio: number | null): CSSProperties | undefined {
  if (ratio === null || !Number.isFinite(ratio)) return undefined
  const clamped = Math.min(1, Math.max(0, ratio))
  const percent = Math.round(HEAT_MIN_PERCENT + clamped * (HEAT_MAX_PERCENT - HEAT_MIN_PERCENT))
  return {
    backgroundColor: `color-mix(in srgb, var(--ldst-accent) ${percent}%, var(--ldst-surface))`,
  }
}

/** 带方向列底色：红涨绿跌的 soft 令牌，0 用 flat-soft。 */
export function signedHeatStyle(value: number | null): CSSProperties | undefined {
  if (value === null || !Number.isFinite(value)) return undefined
  const token = value > 0 ? 'up-soft' : value < 0 ? 'down-soft' : 'flat-soft'
  return { backgroundColor: `var(--ldst-${token})` }
}

/** 缺数据单元格：不给底色（保持 surface），由文字与 title 说明「无数据（未核实）」。 */
export const MISSING_CELL_TITLE = '无数据（未核实）'
