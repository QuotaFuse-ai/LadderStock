import { MISSING, formatBoards, formatInt, formatRatePct } from '../../lib/format'
import { cn } from '../../lib/cn'

/**
 * 五段的**程序指标**（每日复盘 `/review` 的正文，QUOT-344-S2 / DEC-049）。
 *
 * 数据只来自 `review/<date>.json` 的 `sections[].metrics`——由 `review/pipeline.collect_metrics`
 * 直出，AI 不参与（边界闸门 `merge_narrative` 保证模型只能追加 `narrative`，改不动指标）。
 * 这一层因此**不读** `narrative` / `model` / `status`：那些是 AI复盘屏 `/review/ai` 的内容。
 *
 * 缺失口径（QUOT-319 的硬规矩）：
 * - 段级 `available === false` 时，计数类指标一律 `—` 并给出 `reason`。`board_rows: 0`
 *   在这种段里是「本机没有这个数据集」，不是「0 条上榜」，显示 0 就是把缺数读成事实。
 * - 单值 `null` / 非有限数 → `—`；`0` 是真实读数，照显示。
 *
 * 后端 key 不直显（§11.0）：取值走下面的登记表，未登记的 key 不渲染。新增指标要改
 * `review/pipeline.collect_metrics` 与本表两处，本表故意不留「未知 key 原样输出」的后路。
 */

/** 与三张指标卡同源的取值口径：同一个指标在卡上与段内必须是同一个字符串。 */
type Renderer = (value: unknown) => string

function boolText(value: unknown, yes: string, no: string): string {
  if (typeof value !== 'boolean') return MISSING
  return value ? yes : no
}

function intText(value: unknown): string {
  return formatInt(typeof value === 'number' ? value : null)
}

/** 档位数组：`5 板、6 板、8 板…`，超过 6 档折行数，避免一屏被断层列表占满。 */
function boardListText(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return '无断层'
  const levels = value.filter((item): item is number => typeof item === 'number')
  if (levels.length === 0) return MISSING
  const shown = levels.slice(0, 6).map((level) => formatBoards(level)).join('、')
  return levels.length > 6 ? `${shown} 等 ${levels.length} 档` : shown
}

/** 龙头候选：`名称（代码 · N 板）`，代码与板数都是数字位，走 `.ldst-num`。 */
function candidateText(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return '暂无候选'
  const items = value.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const record = item as Record<string, unknown>
    const name = typeof record.name === 'string' ? record.name : null
    const code = typeof record.code === 'string' ? record.code : null
    const boards = typeof record.boards === 'number' ? record.boards : null
    if (!name && !code) return []
    return [`${name ?? MISSING}（${code ?? MISSING}${boards === null ? '' : ` · ${formatBoards(boards)}`})`]
  })
  if (items.length === 0) return MISSING
  return items.length > 3 ? `${items.slice(0, 3).join('、')} 等 ${items.length} 只` : items.join('、')
}

function stringListText(value: unknown): string {
  if (!Array.isArray(value)) return MISSING
  const items = value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
  return items.length === 0 ? '无' : items.join('、')
}

interface MetricField {
  label: string
  render: Renderer
  /** 只在段级 `available` 为真时才有意义（行数、条数类） */
  dependsOnAvailability?: boolean
}

/** 登记的指标 key → 中文标签与取值口径（对应 `review/pipeline.collect_metrics` 的产出）。 */
const METRIC_FIELDS: Record<string, MetricField> = {
  temperature: { label: '情绪温度', render: intText },
  promotion_rate: { label: '晋级率', render: (value) => formatRatePct(typeof value === 'number' ? value : null).text },
  seal_rate: { label: '封板率', render: (value) => formatRatePct(typeof value === 'number' ? value : null).text },
  limit_up_count: { label: '涨停家数', render: intText, dependsOnAvailability: true },
  broken_count: { label: '炸板家数', render: intText, dependsOnAvailability: true },
  yesterday_limit_up_count: { label: '昨日涨停', render: intText, dependsOnAvailability: true },
  max_board: { label: '最高板', render: (value) => formatBoards(typeof value === 'number' ? value : null) },
  gap: { label: '梯队断层', render: (value) => boolText(value, '存在断层', '无断层') },
  gap_levels: { label: '断层档位', render: boardListText },
  missing: { label: '缺失分项', render: stringListText },
  records: { label: '资金流样本', render: intText, dependsOnAvailability: true },
  industry_rows: { label: '行业板块行数', render: intText, dependsOnAvailability: true },
  concept_rows: { label: '概念板块行数', render: intText, dependsOnAvailability: true },
  news_rows: { label: '题材资讯行数', render: intText, dependsOnAvailability: true },
  board_rows: { label: '上榜个股', render: intText, dependsOnAvailability: true },
  seat_rows: { label: '席位行数', render: intText, dependsOnAvailability: true },
  height: { label: '天梯最高', render: (value) => formatBoards(typeof value === 'number' ? value : null) },
  count: { label: '天梯家数', render: intText, dependsOnAvailability: true },
  leader_candidates: { label: '龙头候选', render: candidateText },
  unknown_board: { label: '板数未知', render: intText, dependsOnAvailability: true },
}

/** 每段固定的展示顺序：按登记顺序渲染，段内不随 JSON key 顺序漂移。 */
const SECTION_FIELD_ORDER: Record<string, readonly string[]> = {
  sentiment: [
    'temperature',
    'promotion_rate',
    'seal_rate',
    'limit_up_count',
    'broken_count',
    'yesterday_limit_up_count',
    'max_board',
    'gap',
    'gap_levels',
    'missing',
  ],
  capital: ['records'],
  theme: ['industry_rows', 'concept_rows', 'news_rows'],
  dragon_tiger: ['board_rows', 'seat_rows'],
  leader: ['height', 'count', 'max_board', 'gap', 'gap_levels', 'leader_candidates', 'unknown_board'],
}

function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

export interface SectionMetricsProps {
  sectionName: string
  metrics: Record<string, unknown>
  className?: string
}

export default function SectionMetrics({ sectionName, metrics, className }: SectionMetricsProps) {
  const available = metrics.available !== false
  const reason = textOf(metrics.reason)
  const sourceDetail = textOf(metrics.source_detail)
  const summary = textOf(metrics.summary)
  const order = SECTION_FIELD_ORDER[sectionName] ?? []
  const rows = order
    .filter((key) => key in metrics)
    .map((key) => {
      const field = METRIC_FIELDS[key]
      if (!field) return null
      const value =
        field.dependsOnAvailability && !available ? MISSING : field.render(metrics[key])
      return { key, label: field.label, value }
    })
    .filter((row): row is { key: string; label: string; value: string } => row !== null)

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {rows.length > 0 ? (
        <dl className="flex flex-col gap-1">
          {rows.map((row) => (
            <div key={row.key} className="flex items-baseline justify-between gap-2">
              <dt className="min-w-0 shrink-0 text-caption text-text-muted">{row.label}</dt>
              <dd
                className={cn(
                  'min-w-0 truncate text-right text-caption text-text-secondary',
                  row.value !== MISSING && 'ldst-num',
                )}
                title={row.value}
              >
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-caption text-text-muted">
          这一段没有登记的程序指标（{MISSING}）——指标口径由程序直出，页面不自算。
        </p>
      )}

      {!available && (
        <p className="text-caption text-warn">
          本段数据缺失：{reason ?? '本机没有该数据集（行情尚未同步），同步后点重试重新检查。'}
        </p>
      )}
      {summary && <p className="text-caption leading-body text-text-secondary">{summary}</p>}
      {sourceDetail && (
        <p className="border-t border-border pt-2 text-caption text-text-muted" title={sourceDetail}>
          数据来源 <span className="ldst-num">{sourceDetail}</span>
        </p>
      )}
    </div>
  )
}
