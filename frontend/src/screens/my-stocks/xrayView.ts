/**
 * X 光子页的纯函数（QUOT-437）：提醒线偏好、超线清单、两张图的 option。
 *
 * 三条口径：
 * 1. **数字全部来自 `GET /api/portfolio/xray`**，这里只做人话化，不重算权重；
 * 2. **提醒线是用户自己定的**：`null` = 这一项不提醒。默认值只是第一次用的起点，
 *    不是建议；超线只说「哪个数字超过了你设的线」，不打分、不给操作建议；
 * 3. 占比在小数与百分比之间换算时不动原始权重（后端 `round4` 已定死口径）。
 */

import type { ChartPalette } from '../../charts/cssVar'
import type { PortfolioXrayPayload, XrayBucket, XrayItem } from '../../api/radar'

/** 只存这一个键；隐私模式、成本法各自的键不在这里管。 */
export const THRESHOLDS_KEY = 'ldst.portfolio.xray.thresholds'

export type ThresholdKey = 'single' | 'top3' | 'industry' | 'corr'

export interface XrayThresholds {
  /** 单只占比（小数） */
  single: number | null
  top3: number | null
  industry: number | null
  /** 相关系数：0–1，不看负相关 */
  corr: number | null
}

export const DEFAULT_THRESHOLDS: XrayThresholds = {
  single: 0.3,
  top3: 0.6,
  industry: 0.4,
  corr: 0.8,
}

export const THRESHOLD_FIELDS: Array<{
  key: ThresholdKey
  label: string
  unit: '%' | 'r'
  hint: string
}> = [
  { key: 'single', label: '单只占比', unit: '%', hint: '任何一只超过这个比例就提醒；只有一只时不比' },
  { key: 'top3', label: '前三只合计', unit: '%', hint: '持仓超过三只时才比较' },
  { key: 'industry', label: '单一行业', unit: '%', hint: '只看你在持仓记录里标注的行业；「未标注」不比' },
  { key: 'corr', label: '两只的相关系数', unit: 'r', hint: '0 到 1，越接近 1 走势越像' },
]

function thresholdOf(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(String(value).trim())
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 1) return null
  return parsed
}

/** 存储里的 JSON → 提醒线：认不出的项按「不提醒」，不拿默认值盖掉用户留的空。 */
export function parseThresholds(raw: unknown): XrayThresholds {
  if (typeof raw !== 'string' || !raw.trim()) return { ...DEFAULT_THRESHOLDS }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ...DEFAULT_THRESHOLDS }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ...DEFAULT_THRESHOLDS }
  }
  const row = parsed as Record<string, unknown>
  // 老键名（single_weight 之类）认不出就当没设：这里只做兼容读，不猜新字段
  return {
    single: thresholdOf(row.single),
    top3: thresholdOf(row.top3),
    industry: thresholdOf(row.industry),
    corr: thresholdOf(row.corr),
  }
}

export function readThresholds(): XrayThresholds {
  try {
    return parseThresholds(window.localStorage.getItem(THRESHOLDS_KEY))
  } catch {
    return { ...DEFAULT_THRESHOLDS }
  }
}

export function saveThresholds(value: XrayThresholds): void {
  try {
    window.localStorage.setItem(THRESHOLDS_KEY, JSON.stringify(value))
  } catch {
    // 记不住只影响下次打开时的默认值，本次修改仍然生效
  }
}

/** 小数 → 展示用百分比文本（后端给的就是小数）。 */
export function weightText(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export interface XrayBreach {
  key: ThresholdKey
  /** 「单只最大占比」这类条目名 */
  label: string
  /** 只说哪个数字超了线、超到哪：不解释好坏 */
  text: string
}

/** 横幅里报「哪一只」用名称；名称没填才退回代码。 */
function displayName(item: Pick<XrayItem, 'symbol' | 'name'>): string {
  return item.name || item.symbol
}

/**
 * 超线清单：每一项只回答「你设的线被谁超过了」。
 *
 * 三条比较上的克制：
 * - 只有一只持仓时不比「单只占比」——100% 是数学结果，不是集中度；
 * - 持仓不足 3 只时不比「前三合计」——同上；
 * - 「未标注」这一桶不参与行业比较：没标行业不等于行业集中。
 */
export function findBreaches(payload: PortfolioXrayPayload, thresholds: XrayThresholds): XrayBreach[] {
  const breaches: XrayBreach[] = []
  const weighted = payload.items.filter((item) => item.weight !== null)

  if (thresholds.single !== null && weighted.length >= 2) {
    const over = weighted.filter((item) => (item.weight ?? 0) > thresholds.single!)
    for (const item of over) {
      breaches.push({
        key: 'single',
        label: '单只最大占比',
        text: `${displayName(item)} ${weightText(item.weight)}，超过你设的 ${weightText(thresholds.single)}`,
      })
    }
  }

  if (thresholds.top3 !== null && payload.top3 !== null && weighted.length >= 4) {
    if (payload.top3 > thresholds.top3) {
      breaches.push({
        key: 'top3',
        label: '前三只合计',
        text: `前三只合计 ${weightText(payload.top3)}，超过你设的 ${weightText(thresholds.top3)}`,
      })
    }
  }

  if (thresholds.industry !== null) {
    const over = payload.industry_buckets.filter(
      (bucket) => bucket.labeled && bucket.weight !== null && bucket.weight > thresholds.industry!,
    )
    for (const bucket of over) {
      breaches.push({
        key: 'industry',
        label: '单一行业',
        text: `${bucket.industry} ${weightText(bucket.weight)}，超过你设的 ${weightText(thresholds.industry)}`,
      })
    }
  }

  const pair = payload.correlation.max_pair
  if (thresholds.corr !== null && pair && pair.corr > thresholds.corr) {
    const names = pair.pair.map((symbol) => {
      const item = weighted.find((row) => row.symbol === symbol)
      return item ? displayName(item) : symbol
    })
    breaches.push({
      key: 'corr',
      label: '走势最像的一对',
      text: `${names.join(' 与 ')} 相关系数 ${pair.corr.toFixed(2)}，超过你设的 ${thresholds.corr.toFixed(2)}`,
    })
  }

  return breaches
}

/** 轴刻度：0.0095 → `0.95%`（窄区间保留两位，宽区间自然去掉多余的 0）。 */
function pctTick(value: number): string {
  const text = (value * 100)
    .toFixed(2)
    .replace(/\.00$/, '')
    .replace(/(\d)0$/, '$1')
  return `${text}%`
}

function tooltipOf(palette: ChartPalette) {
  return {
    trigger: 'item' as const,
    backgroundColor: palette.tooltipBg,
    borderColor: palette.axis,
    textStyle: { color: palette.tooltipText, fontSize: 12 },
  }
}

/**
 * 单只占比条形图：按权重降序，超过提醒线的那根换成提醒色。
 *
 * 只画前 12 只（其余归不进图里也不硬算一个「其他」权重——那是把缺失当 0）。
 */
export function singleWeightOption(
  payload: PortfolioXrayPayload,
  thresholds: XrayThresholds,
  palette: ChartPalette,
): { option: Record<string, unknown>; shown: number } | null {
  const rows = payload.items
    .filter((item) => item.weight !== null)
    .slice(0, 12)
    .reverse()
  if (rows.length === 0) return null
  const line = thresholds.single
  return {
    shown: rows.length,
    option: {
      animation: false,
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        ...tooltipOf(palette),
        formatter: (params: { name: string; value: number }) =>
          `${params.name}　${weightText(params.value)}`,
      },
      xAxis: {
        type: 'value',
        max: 1,
        axisLabel: { color: palette.text, fontSize: 11, formatter: (value: number) => pctTick(value) },
        splitLine: { lineStyle: { color: palette.grid } },
      },
      yAxis: {
        type: 'category',
        data: rows.map((item) => item.name || item.symbol),
        axisLabel: { color: palette.text, fontSize: 11, width: 96, overflow: 'truncate' },
        axisLine: { lineStyle: { color: palette.axis } },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 14,
          data: rows.map((item) => ({
            value: item.weight,
            itemStyle: {
              color: line !== null && (item.weight ?? 0) > line ? palette.threshold : palette.line,
            },
          })),
        },
      ],
    },
  }
}

/** 行业暴露条形图：「未标注」永远画出来，并和标注过的桶用不同颜色区分。 */
export function industryWeightOption(
  buckets: XrayBucket[],
  thresholds: XrayThresholds,
  palette: ChartPalette,
): Record<string, unknown> | null {
  if (buckets.length === 0) return null
  const rows = [...buckets].reverse()
  const line = thresholds.industry
  return {
    animation: false,
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      ...tooltipOf(palette),
      formatter: (params: { name: string; value: number }) =>
        `${params.name}　${weightText(params.value)}`,
    },
    xAxis: {
      type: 'value',
      max: 1,
      axisLabel: { color: palette.text, fontSize: 11, formatter: (value: number) => pctTick(value) },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((bucket) => bucket.industry),
      axisLabel: { color: palette.text, fontSize: 11, width: 96, overflow: 'truncate' },
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 16,
        data: rows.map((bucket) => ({
          value: bucket.weight,
          itemStyle: {
            color:
              !bucket.labeled
                ? palette.empty
                : line !== null && (bucket.weight ?? 0) > line
                  ? palette.threshold
                  : palette.series2,
          },
        })),
      },
    ],
  }
}
