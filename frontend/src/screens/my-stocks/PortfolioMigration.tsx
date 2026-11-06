import { Archive } from 'lucide-react'

import type { PortfolioLegacyRow } from '../../api/radar'
import { Button, SectionCard } from '../../components/ui'
import { formatAmount } from '../../lib/format'
import { PrivacyText } from './PrivacyText'

/**
 * 旧仓迁移卡（QUOT-434）：把 `legacy` 里还需要用户拿主意的记录摆出来。
 *
 * - `pending`：这次自动迁移没成功（原因在 `migration.error` 里），仍可手动转；
 * - `conflict`：已经转过、但旧记录后来改了，或这个代码已经有流水 / 多条旧记录；
 * - `skipped`：数据不全（没代码 / 数量 0 / 没成本），只能确认不要。
 *
 * `stale`（已迁完）和 `dismissed`（已确认不要）不再打扰。
 * **只标记、不删除**：dismiss 只写 mark，`positions` 旧行原样保留。
 */

export type LegacyAction = 'adopt' | 'dismiss'

export interface LegacyTarget {
  row: PortfolioLegacyRow
  action: LegacyAction
}

export interface MigrationCardProps {
  rows: PortfolioLegacyRow[]
  /** 自动迁移留下的备份文件名；没有迁移动作时为 null。 */
  backup: string | null
  /** 自动迁移失败的原因（GET 时 `migration.error`）。 */
  error: string | null
  onPick: (target: LegacyTarget) => void
}

export function migrateActionRows(rows: PortfolioLegacyRow[]): PortfolioLegacyRow[] {
  return rows.filter((row) => row.state === 'pending' || row.state === 'conflict' || row.state === 'skipped')
}

export function adoptLabel(row: PortfolioLegacyRow): string {
  if (row.state === 'pending') return '立即转为期初持仓'
  if (row.migrated_trade !== null) return '按它现在的数替换那笔期初持仓'
  return '按它补一笔期初持仓'
}

export function dismissLabel(row: PortfolioLegacyRow): string {
  return row.state === 'skipped' ? '不要这条' : '保留那笔、不要这条'
}

export function MigrationCard({ rows, backup, error, onPick }: MigrationCardProps) {
  return (
    <SectionCard
      tone="warn"
      title="要你确认的旧持有记录"
      subtitle="这些是旧版一只一条的持有记录，没能自动转成流水"
    >
      <div className="flex flex-col gap-3">
        <p className="text-caption leading-body text-text-secondary">
          处理前会先备份；<strong className="text-text">旧记录只标记、不删除</strong>
          ，确认后仍可在数据目录里按备份对照。处理完之前，这些记录不算进下面的持仓明细。
        </p>
        {error && (
          <p role="alert" className="text-caption leading-body text-danger">
            自动迁移这次没成功：{error}。下次打开会再试，也可以逐条手动处理。
          </p>
        )}
        <ul className="flex flex-col gap-2">
          {rows.map((row) => (
            <li
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-border px-3 py-2"
            >
              <div className="min-w-0">
                <p className="flex flex-wrap items-baseline gap-2 text-body">
                  <span className="text-caption text-text-muted">{stateLabel(row.state)}</span>
                  <span className="ldst-num font-medium text-text">{row.symbol || '无代码'}</span>
                  {row.name && <span className="text-text-secondary">{row.name}</span>}
                  {row.shares !== null && (
                    <span className="ldst-num text-caption text-text-secondary">
                      <PrivacyText text={`${formatAmount(row.shares)} 股`} /> · 成本{' '}
                      <PrivacyText text={row.cost === null ? '—' : formatAmount(row.cost)} />
                    </span>
                  )}
                  {row.opened_at && (
                    <span className="ldst-num text-caption text-text-muted">建仓 {row.opened_at}</span>
                  )}
                </p>
                <p className="mt-1 text-caption leading-body text-text-secondary">{row.why}</p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                {row.state !== 'skipped' && (
                  <Button
                    size="md"
                    leadingIcon={<Archive className="size-3.5" aria-hidden="true" />}
                    onClick={() => onPick({ row, action: 'adopt' })}
                  >
                    {adoptLabel(row)}
                  </Button>
                )}
                <Button variant="danger" onClick={() => onPick({ row, action: 'dismiss' })}>
                  {dismissLabel(row)}
                </Button>
              </div>
            </li>
          ))}
        </ul>
        {backup && (
          <p className="text-caption leading-body text-text-muted">
            改动前的原文件备份：<code className="ldst-num">{backup}</code>
          </p>
        )}
      </div>
    </SectionCard>
  )
}

export function stateLabel(state: PortfolioLegacyRow['state']): string {
  switch (state) {
    case 'pending':
      return '待迁移'
    case 'conflict':
      return '冲突'
    case 'skipped':
      return '跳过'
    case 'stale':
      return '已迁移'
    case 'dismissed':
      return '已忽略'
  }
}
