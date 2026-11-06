import { Trash2 } from 'lucide-react'

import type { PortfolioIssue, PortfolioTrade } from '../../api/radar'
import { Banner, DataTable, EmptyState, IconButton, type Column } from '../../components/ui'
import { MISSING, formatTime } from '../../lib/format'
import { tradeDetail, tradeTypeLabel } from './portfolioForm'
import { PrivacyText } from './PrivacyText'

/**
 * 交易流水子页（QUOT-434）：全部流水按日期倒序，删除走二次确认（确认框由页面持有）。
 *
 * 数据不对的流水（`issues`）在表上方逐条说明原因——它们没参与计算，
 * 让用户看见「为什么持仓和流水对不上」。
 */

export interface PortfolioTradesTabProps {
  trades: PortfolioTrade[]
  issues: PortfolioIssue[]
  onDelete: (trade: PortfolioTrade) => void
}

function nameOf(trade: PortfolioTrade, names: Map<string, string>): string {
  return trade.name || names.get(trade.symbol) || ''
}

function nameMapOf(trades: PortfolioTrade[]): Map<string, string> {
  const names = new Map<string, string>()
  for (const trade of [...trades].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id)) {
    if (trade.name) names.set(trade.symbol, trade.name)
    else if (!names.has(trade.symbol)) names.set(trade.symbol, '')
  }
  return names
}

/** 明细里的所有权重数字都是自己的账：隐私模式下整段换成掩码。 */
function detailTextOf(trade: PortfolioTrade): string {
  const detail = tradeDetail(trade)
  return detail || MISSING
}

export function PortfolioTradesTab({ trades, issues, onDelete }: PortfolioTradesTabProps) {
  const names = nameMapOf(trades)
  const ordered = [...trades].sort((a, b) => {
    if (a.date !== b.date) return b.date.localeCompare(a.date)
    return b.id - a.id
  })

  const columns: Array<Column<PortfolioTrade>> = [
    {
      key: 'date',
      header: '日期',
      width: '104px',
      render: (trade) => <span className="ldst-num text-text-secondary">{trade.date || MISSING}</span>,
    },
    {
      key: 'type',
      header: '类型',
      width: '112px',
      render: (trade) => (
        <span className="whitespace-nowrap text-text">
          {tradeTypeLabel(trade.type)}
          {trade.undated && <span className="ml-1 text-caption text-warn" title="原记录没写建仓日">暂记</span>}
        </span>
      ),
    },
    {
      key: 'symbol',
      header: '名称 / 代码',
      truncate: true,
      render: (trade) => {
        const name = nameOf(trade, names)
        return (
          <span className="flex min-w-0 items-baseline gap-1.5">
            <span className={name ? 'truncate font-medium' : 'text-text-muted'}>{name || MISSING}</span>
            <span className="ldst-num shrink-0 text-caption text-text-muted">{trade.symbol}</span>
          </span>
        )
      },
      cellTitle: (trade) => `${nameOf(trade, names) || '名称未填'} ${trade.symbol}`,
    },
    {
      key: 'detail',
      header: '明细',
      width: '200px',
      render: (trade) => (
        <PrivacyText
          className="ldst-num whitespace-nowrap text-caption text-text-secondary"
          text={detailTextOf(trade)}
        />
      ),
    },
    {
      key: 'note',
      header: '备注',
      truncate: true,
      render: (trade) => (
        <span className="text-caption text-text-secondary">{trade.note || MISSING}</span>
      ),
      cellTitle: (trade) => trade.note || '未填写备注',
    },
    {
      key: 'created_at',
      header: '录入时间',
      width: '116px',
      render: (trade) => (
        <span className="ldst-num text-caption text-text-muted">
          {trade.created_at ? formatTime(trade.created_at) : MISSING}
        </span>
      ),
    },
    {
      key: 'action',
      header: '操作',
      align: 'right',
      width: '64px',
      render: (trade) => (
        <IconButton
          label={`删除 ${trade.symbol} ${trade.date} ${tradeTypeLabel(trade.type)}`}
          icon={<Trash2 className="size-3.5" aria-hidden="true" />}
          onClick={() => onDelete(trade)}
        />
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-3">
      {issues.length > 0 && (
        <Banner tone="warn" title={`有 ${issues.length} 笔流水的数据不对，没参与计算`}>
          <ul className="mt-1 flex flex-col gap-0.5 text-caption">
            {issues.map((issue) => (
              <li key={issue.id}>
                <span className="ldst-num">{issue.date || '无日期'}</span> ·{' '}
                {tradeTypeLabel(issue.type)} · <span className="ldst-num">{issue.symbol || '无代码'}</span>：
                {issue.why}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-caption">跳过的卖出会让之后的持有数偏大，请先删掉重记。</p>
        </Banner>
      )}

      <DataTable
        caption={`交易流水（${trades.length} 笔）`}
        columns={columns}
        rows={ordered}
        rowKey={(trade) => String(trade.id)}
        empty={
          <EmptyState
            compact
            title="还没有流水"
            description="买入、卖出、分红、送转都在上面「记一笔」里记录；持仓和盈亏会按流水重新算。"
          />
        }
      />
      <p className="text-caption text-text-muted">
        删除是硬删（删前后端会先备份）。流水按日期倒序；金额与股数在隐私模式下遮罩。
      </p>
    </div>
  )
}
