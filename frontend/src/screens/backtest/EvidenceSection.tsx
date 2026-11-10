import type { ReactNode } from 'react'
import { FileJson, FileText } from 'lucide-react'

import { backtestExportPath, type BacktestRunCard } from '../../api/backtest'
import { Badge, DataTable, SectionCard, type Column } from '../../components/ui'
import { MISSING } from '../../lib/format'
import {
  amountText,
  configLabel,
  configValueText,
  metricDisplay,
  METRIC_META,
  percentText,
} from './runCard'

/**
 * 证据区（QUOT-534）：run_id / 数据指纹 / 每指标 calc_id 溯源 / 运行卡下载。
 *
 * 下载链接直接指向后端 `export.{json,md}`（`attachment` 响应），页面不二次生成文件、
 * 不裁剪字段。所有溯源值都从运行卡原样读取。
 */

function DownloadLink({ href, icon, children }: { href: string; icon: ReactNode; children: ReactNode }) {
  return (
    <a
      href={href}
      className="inline-flex h-control items-center gap-1 rounded border border-border-strong bg-surface px-3 text-body text-text hover:bg-surface-3"
    >
      {icon}
      {children}
    </a>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-half">
      <dt className="text-caption text-text-muted">{label}</dt>
      <dd className="break-all text-body text-text">{children}</dd>
    </div>
  )
}

function IdentityCard({ card }: { card: BacktestRunCard }) {
  const { data, period } = card
  const artifactEntries = Object.entries(card.artifacts)

  return (
    <SectionCard
      title="运行卡身份"
      subtitle="run_id / 数据指纹 / 区间 / 产物路径，全部原样来自运行卡"
      actions={<Badge tone="neutral">只读</Badge>}
    >
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Row label="run_id">
          <span className="ldst-num">{card.run_id}</span>
        </Row>
        <Row label="生成时间">
          <span className="ldst-num">{card.generated_at ?? MISSING}</span>
        </Row>
        <Row label="schema_version">
          <span className="ldst-num">{card.schema_version || MISSING}</span>
        </Row>
        <Row label="策略 / 参数">
          <span className="ldst-num">{card.strategy}</span>
          <span className="ldst-num mt-half block text-caption text-text-muted">
            {Object.entries(card.strategy_params)
              .map(([key, value]) => `${key}=${String(value)}`)
              .join(' · ') || MISSING}
          </span>
        </Row>
        <Row label="标的 / 板块">
          <span className="ldst-num">
            {card.symbol || MISSING}
            {card.board ? ` · ${card.board}` : ''}
            {card.limit_pct !== null ? ` · 涨跌幅 ${percentText(card.limit_pct, 0)}` : ''}
          </span>
        </Row>
        <Row label="区间 / 交易日">
          <span className="ldst-num">
            {period.start ?? MISSING} ~ {period.end ?? MISSING} · {period.trading_days ?? MISSING} 日
          </span>
        </Row>
        <Row label="数据指纹">
          <span className="ldst-num">{data.fingerprint ?? MISSING}</span>
        </Row>
        <Row label="数据源 / 复权">
          <span className="ldst-num">
            {data.source ?? MISSING} · {data.adjust ?? MISSING} · {data.bars ?? MISSING} 根
          </span>
          <span className="ldst-num mt-half block text-caption text-text-muted">
            {data.first_date ?? MISSING} ~ {data.last_date ?? MISSING}
          </span>
        </Row>
        <Row label="资金">
          <span className="ldst-num">
            初始 {amountText(card.initial_cash)} · 期末 {amountText(card.final_equity)} · 未平仓{' '}
            {amountText(card.open_position_pnl)}
          </span>
        </Row>
      </dl>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-2 text-caption text-text-muted">
        {Object.entries(card.calc_ids).map(([key, value]) => (
          <span key={key}>
            {key} calc_id <span className="ldst-num text-text-secondary">{value}</span>
          </span>
        ))}
      </div>

      {artifactEntries.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 border-t border-border pt-2 text-caption text-text-muted">
          {artifactEntries.map(([key, path]) => (
            <li key={key} className="flex flex-wrap gap-1">
              <span>{key}</span>
              <span className="ldst-num break-all text-text-secondary">{path}</span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

interface CalcRow {
  key: string
  label: string
  display: string
  raw: string
  calcId: string
}

const CALC_COLUMNS: Array<Column<CalcRow>> = [
  {
    key: 'label',
    header: '指标',
    width: '112px',
    render: (row) => row.label,
  },
  {
    key: 'display',
    header: '运行卡展示值',
    numeric: true,
    render: (row) => <span className="ldst-num text-text">{row.display}</span>,
  },
  {
    key: 'raw',
    header: '运行卡原值',
    numeric: true,
    render: (row) => <span className="ldst-num text-text-secondary">{row.raw}</span>,
  },
  {
    key: 'calcId',
    header: 'calc_id',
    render: (row) => <span className="ldst-num break-all text-caption">{row.calcId}</span>,
  },
]

function TraceCard({ card }: { card: BacktestRunCard }) {
  const rows: CalcRow[] = METRIC_META.map((meta) => ({
    key: meta.key,
    label: meta.label,
    display: metricDisplay(card, meta.key),
    raw: card.metrics[meta.key] === undefined ? MISSING : String(card.metrics[meta.key]),
    calcId: card.metrics_calc_ids[meta.key] ?? MISSING,
  }))

  return (
    <SectionCard
      title={`指标溯源 · ${rows.length} 项`}
      subtitle="每行对应运行卡 metrics / metrics_display / metrics_calc_ids 三份同键数据"
    >
      <DataTable
        columns={CALC_COLUMNS}
        rows={rows}
        rowKey={(row) => row.key}
        density="compact"
        caption="展示值取 metrics_display，原值取 metrics，calc_id 取 metrics_calc_ids；前端不做二次换算。"
      />
      <p className="mt-2 text-caption leading-body text-text-muted">
        展示值与原值不一致是契约内的正常现象（例：收益率原值 <span className="ldst-num">0.2338</span>、展示串{' '}
        <span className="ldst-num">23.38%</span>）。页面只搬后端字符串，不参与格式化口径的推导。
      </p>
    </SectionCard>
  )
}

function ConfigCard({ card }: { card: BacktestRunCard }) {
  const entries = Object.entries(card.config)
  return (
    <SectionCard
      title="生效配置 / 假设"
      subtitle={`${entries.length} 项配置 · 来源见 config_sources · 假设 ${card.assumptions.length} 条`}
    >
      {entries.length > 0 ? (
        <DataTable
          columns={[
            { key: 'key', header: '参数', width: '160px', render: (row) => configLabel(row[0]) },
            {
              key: 'value',
              header: '生效值',
              numeric: true,
              render: (row) => <span className="ldst-num">{configValueText(row[1])}</span>,
            },
            {
              key: 'source',
              header: '来源',
              render: (row) => <span className="ldst-num">{card.config_sources[row[0]] ?? MISSING}</span>,
            },
          ]}
          rows={entries}
          rowKey={(row) => row[0]}
          density="compact"
          caption="config / config_sources 原样透传；前端不提供默认值，也不覆盖后端配置。"
        />
      ) : (
        <p className="text-caption text-text-muted">运行卡没有 config 字段。</p>
      )}

      {card.assumptions.length > 0 && (
        <ul className="mt-3 flex list-disc flex-col gap-1 border-t border-border pt-2 pl-5 text-caption leading-body text-text-secondary">
          {card.assumptions.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-caption text-text-muted">
        成本口径（佣金万 / 免五 / 过户费 / 印花税）由后端参数与 broker preset 决定，前端只展示本次生效值；
        可调参数区在上方「参数区」提交。
      </p>
    </SectionCard>
  )
}

export function EvidenceSection({ card }: { card: BacktestRunCard }) {
  return (
    <section className="flex flex-col gap-4" aria-label="证据区">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-caption font-semibold text-text-secondary">证据区</h2>
        <Badge tone="neutral">可下载产物 · 不二次生成</Badge>
        <span className="ldst-num text-caption text-text-muted">run_id {card.run_id}</span>
      </div>

      <SectionCard
        title="运行卡下载"
        subtitle="链接指向本机只读导出端点，下载内容由后端 attachment 响应给出"
        tone="info"
        actions={
          <>
            <DownloadLink href={backtestExportPath(card.run_id, 'json')} icon={<FileJson className="size-4" aria-hidden="true" />}>
              运行卡 JSON
            </DownloadLink>
            <DownloadLink href={backtestExportPath(card.run_id, 'md')} icon={<FileText className="size-4" aria-hidden="true" />}>
              运行卡 Markdown
            </DownloadLink>
          </>
        }
      >
        <p className="text-caption leading-body text-text-muted">
          JSON 与 Markdown 都是后端落盘产物的原样导出，包含本页未铺开的全部字段（原始序列、分段明细、
          配置来源等）。页面不拼接、不删减、不重新计算。
        </p>
      </SectionCard>

      <IdentityCard card={card} />
      <TraceCard card={card} />
      <ConfigCard card={card} />
    </section>
  )
}
