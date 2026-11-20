import { useEffect, useMemo, useState } from 'react'
import { Database, ExternalLink, Search } from 'lucide-react'
import { Link } from 'react-router-dom'

import {
  describeDataAssetsError,
  loadDataAssetDetail,
  loadDataAssets,
  loadKnowledgeCatalog,
  searchKnowledge,
  type DataAssetDetail,
  type DataAssetIndex,
  type DataAssetRow,
  type KnowledgeCatalog,
  type KnowledgeHit,
} from '../api/dataAssets'
import { PageHeader } from '../components/PageShell'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Field, Input } from '../components/ui'

const DOC_KIND_LABEL: Record<string, string> = {
  financial_report: '财报',
  earnings_preview: '业绩预告',
  dividend: '分红派息',
  buyback: '回购',
  risk_event: '诉讼风险',
  merger: '并购重组',
  refinancing: '再融资',
  stock_incentive: '股权激励',
  esg_report: 'ESG 报告',
  investor_relations: '投资者关系',
  guarantee: '担保',
  agency_report: '中介核查',
  shareholder_meeting: '股东会',
  equity_change: '股权变动',
  personnel: '人事薪酬',
  related_party: '关联交易',
  legal: '法律意见',
  governance: '治理制度',
  notice: '一般公告',
}

/**
 * 数据资产页（`/data-assets`）。
 *
 * 这页解决的不是"再做一个表格"，而是此前平台上**根本看不到**的那一层：
 * FinanceCoffer 的 Postgres 里到底有哪些票、每只票有几期财报、几条公告正文。
 * 没有这页时，用户只能靠一次次提问去试探，试出来的还是"没取到"这种答案。
 *
 * 三条硬约束：
 * 1. **只显示服务端给的行**。缺财报就是 0 期，缺公告就是 0 条，不补默认值、不猜。
 * 2. **点开就调明细**，不在列表里塞一堆数字把页面撑爆。
 * 3. **每行都给去下一站的入口**：问这票（`/research/:code`）、看档案（`/knowledge/:market/:symbol`）。
 */

type LoadState = 'loading' | 'ready' | 'error'

const QUALITY_LABEL: Record<string, string> = {
  suspicious: '待核',
  ok: '正常',
  passed: '已通过',
}

function formatAmount(value: number | null): string {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)} 万`
  return value.toFixed(2)
}

function marketOf(symbol: string): string {
  if (symbol.startsWith('6')) return 'SH'
  if (symbol.startsWith('8') || symbol.startsWith('4')) return 'BJ'
  return 'SZ'
}

function rowTone(row: DataAssetRow): 'ok' | 'warn' | 'danger' {
  if (row.has_wide && row.has_announcement) return 'ok'
  if (row.has_financial || row.has_announcement) return 'warn'
  return 'danger'
}

function rowLabel(row: DataAssetRow): string {
  if (row.has_wide && row.has_announcement) return '齐'
  if (row.has_wide && !row.has_announcement) return '缺公告'
  if (!row.has_wide && row.has_financial) return '缺宽表'
  return '缺数据'
}

export default function DataAssetsScreen() {
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState<string | null>(null)
  const [errorTitle, setErrorTitle] = useState('读取失败')
  const [index, setIndex] = useState<DataAssetIndex | null>(null)
  const [query, setQuery] = useState('')
  const [onlyGaps, setOnlyGaps] = useState(false)

  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<DataAssetDetail | null>(null)
  const [detailState, setDetailState] = useState<LoadState>('loading')
  const [detailError, setDetailError] = useState<string | null>(null)

  const [catalog, setCatalog] = useState<KnowledgeCatalog | null>(null)
  const [catalogState, setCatalogState] = useState<LoadState>('loading')
  const [kbQuery, setKbQuery] = useState('')
  const [kbKind, setKbKind] = useState('')
  const [kbHits, setKbHits] = useState<KnowledgeHit[]>([])
  const [kbSearching, setKbSearching] = useState(false)
  const [kbError, setKbError] = useState<string | null>(null)
  const [showFields, setShowFields] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    loadKnowledgeCatalog(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setCatalog(result)
        setCatalogState('ready')
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const described = describeDataAssetsError(err)
        setKbError(described.detail)
        setCatalogState('error')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    loadDataAssets(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setIndex(result)
        setState('ready')
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const described = describeDataAssetsError(err)
        setErrorTitle(described.title)
        setError(described.detail)
        setState('error')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    setDetailState('loading')
    setDetailError(null)
    setDetail(null)
    loadDataAssetDetail(selected, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setDetail(result)
        setDetailState('ready')
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const described = describeDataAssetsError(err)
        setDetailError(described.detail)
        setDetailState('error')
      })
    return () => controller.abort()
  }, [selected])

  const visible = useMemo(() => {
    const rows = index?.items ?? []
    const keyword = query.trim()
    return rows.filter((row) => {
      if (onlyGaps && row.has_wide && row.has_announcement) return false
      if (keyword && !row.symbol.includes(keyword)) return false
      return true
    })
  }, [index, query, onlyGaps])

  async function runKbSearch() {
    const text = kbQuery.trim()
    if (!text) return
    setKbSearching(true)
    setKbError(null)
    try {
      const result = await searchKnowledge(text, { docKind: kbKind || undefined, topK: 8 })
      setKbHits(result.hits)
    } catch (err) {
      const described = describeDataAssetsError(err)
      setKbError(described.detail)
      setKbHits([])
    } finally {
      setKbSearching(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="数据资产"
        subtitle="本地库里到底有哪些票、几期财报、几条公告正文——平台平时攒下来的东西，一格一格列清楚"
        badge={<Badge tone="neutral">FinanceCoffer</Badge>}
      />

      <Card>
        <CardHeader
          title="库里现在有什么"
          subtitle="数字全部来自本地 Postgres，缺的就是缺的，没有估算"
        />
        <CardBody className="flex flex-col gap-4">
          {state === 'loading' ? (
            <p className="text-body text-text-muted">正在读库…</p>
          ) : state === 'error' ? (
            <div className="flex flex-col gap-2">
              <p className="text-body font-medium text-text">{errorTitle}</p>
              <p className="text-body leading-body text-text-secondary">{error}</p>
              <p className="text-caption text-text-muted">
                库不在不影响你继续提问——问的时候平台会先说「本地没有」，再按权限决定要不要去采。
              </p>
            </div>
          ) : index ? (
            <>
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  { label: '标的', value: `${index.total} 只`, hint: `已建宽表 ${index.with_wide} 只` },
                  {
                    label: '财报',
                    value: `${index.financial_rows} 期`,
                    hint: `覆盖 ${index.with_financial} 只`,
                  },
                  {
                    label: '公告',
                    value: `${index.announcement_rows} 条`,
                    hint: `有正文 ${index.with_announcement} 只`,
                  },
                  {
                    label: '宽表',
                    value: `${index.with_wide} 只`,
                    hint: '每票一份最新快照',
                  },
                ].map((item) => (
                  <li key={item.label} className="rounded border border-border bg-surface-2 p-3">
                    <p className="text-caption text-text-muted">{item.label}</p>
                    <p className="mt-1 font-mono text-page text-text">{item.value}</p>
                    <p className="text-caption text-text-secondary">{item.hint}</p>
                  </li>
                ))}
              </ul>
              <p className="text-caption leading-body text-text-muted">
                「财报期数」按明细行算（一只票 24 期就是一季一期，含同比），「公告」只统计**有正文**的条目；
                正文为空的不算，免得把「有标题没内容」当成可检索。
              </p>
            </>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="知识库（qdrant）"
          subtitle="公告正文按分类入的向量库——分类不是装饰，检索时先按它过滤再算语义"
          actions={
            <Button variant="secondary" onClick={() => setShowFields((value) => !value)}>
              {showFields ? '收起字段' : '看字段清单'}
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-4">
          {catalogState === 'loading' ? (
            <p className="text-body text-text-muted">正在读知识库…</p>
          ) : catalogState === 'error' ? (
            <div className="flex flex-col gap-2">
              <p className="text-body font-medium text-text">知识库读不到</p>
              <p className="text-body leading-body text-text-secondary">{kbError}</p>
              <p className="text-caption text-text-muted">
                qdrant 没起的时候，公告正文照样在库里（DWD 有行），只是语义检索退化成按代码精确读。
              </p>
            </div>
          ) : catalog ? (
            <>
              <ul className="grid gap-3 sm:grid-cols-3">
                {[
                  { label: '切片段', value: `${catalog.points} 片`, hint: '每条公告按 500 字切' },
                  {
                    label: '公告类型',
                    value: `${Object.keys(catalog.doc_kinds ?? {}).length} 类`,
                    hint: '财报 / 分红 / 股东会 …',
                  },
                  {
                    label: '财报期',
                    value: `${Object.keys(catalog.periods ?? {}).length} 期`,
                    hint: '2026Q3 / 2026H1 / 2025A',
                  },
                ].map((item) => (
                  <li key={item.label} className="rounded border border-border bg-surface-2 p-3">
                    <p className="text-caption text-text-muted">{item.label}</p>
                    <p className="mt-1 font-mono text-page text-text">{item.value}</p>
                    <p className="text-caption text-text-secondary">{item.hint}</p>
                  </li>
                ))}
              </ul>

              {catalog.ok ? null : (
                <p className="text-caption leading-body text-warn">
                  知识库当前不可用：{catalog.error ?? '未知原因'}
                </p>
              )}

              {Object.keys(catalog.doc_kinds ?? {}).length > 0 ? (
                <div className="flex flex-col gap-2">
                  <p className="text-body font-medium text-text">分类分布</p>
                  <ul className="flex flex-wrap gap-2">
                    {Object.entries(catalog.doc_kinds)
                      .sort((a, b) => b[1] - a[1])
                      .map(([kind, count]) => (
                        <li
                          key={kind}
                          className="rounded border border-border bg-surface-2 px-2 py-1 text-caption"
                        >
                          {DOC_KIND_LABEL[kind] ?? kind}
                          <span className="ml-1 font-mono text-text-muted">{count}</span>
                        </li>
                      ))}
                  </ul>
                </div>
              ) : null}

              {showFields && catalog.fields?.length ? (
                <div className="flex flex-col gap-2 border-t border-border pt-3">
                  <p className="text-body font-medium text-text">字段清单（payload）</p>
                  <ul className="flex flex-col divide-y divide-border">
                    {catalog.fields.map((field) => (
                      <li key={field.key} className="flex items-start justify-between gap-3 py-1.5 text-caption">
                        <span className="font-mono text-text">{field.key}</span>
                        <span className="text-text-muted">{field.type}</span>
                        <span className="min-w-0 flex-1 truncate text-right text-text-secondary">
                          {field.sample}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="flex flex-wrap items-end gap-3 border-t border-border pt-3">
                <Field label="按语义查公告" htmlFor="kb-query" className="min-w-[260px] flex-1">
                  <Input
                    id="kb-query"
                    value={kbQuery}
                    placeholder="比如：分红方案、回购进度、关联交易"
                    autoComplete="off"
                    leadingIcon={<Search className="size-4" aria-hidden="true" />}
                    onChange={(event) => setKbQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void runKbSearch()
                    }}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  {['', 'financial_report', 'dividend', 'buyback', 'shareholder_meeting'].map((kind) => (
                    <Button
                      key={kind || 'all'}
                      variant={kbKind === kind ? 'primary' : 'secondary'}
                      onClick={() => setKbKind(kind)}
                    >
                      {kind ? DOC_KIND_LABEL[kind] ?? kind : '全部'}
                    </Button>
                  ))}
                </div>
                <Button variant="primary" onClick={() => void runKbSearch()} disabled={kbSearching}>
                  {kbSearching ? '检索中…' : '检索'}
                </Button>
              </div>

              {kbError ? <p className="text-caption text-warn">{kbError}</p> : null}

              {kbHits.length > 0 ? (
                <ul className="flex flex-col divide-y divide-border">
                  {kbHits.map((hit) => (
                    <li key={`${hit.symbol}-${hit.section}-${hit.chunk_index}`} className="flex flex-col gap-1 py-2">
                      <div className="flex items-center justify-between gap-3">
                        <span className="min-w-0 flex-1 truncate text-caption text-text">{hit.title}</span>
                        <span className="shrink-0 text-caption text-text-muted">
                          <Badge tone="neutral">{DOC_KIND_LABEL[hit.doc_kind] ?? hit.doc_kind}</Badge>
                          <span className="ml-2 font-mono">{hit.score.toFixed(3)}</span>
                        </span>
                      </div>
                      <p className="text-caption leading-body text-text-secondary line-clamp-2">{hit.text}</p>
                      <p className="text-caption text-text-muted">
                        {hit.symbol} · {hit.ann_date} · 第 {hit.chunk_index + 1}/{hit.chunk_total || '?'} 段
                      </p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="逐票清单"
          subtitle={index ? `共 ${visible.length} 只在当前筛选下` : '等库读出来'}
          actions={
            <Button
              variant={onlyGaps ? 'primary' : 'secondary'}
              onClick={() => setOnlyGaps((value) => !value)}
            >
              {onlyGaps ? '正在看缺数据的' : '只看缺数据的'}
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="按代码过滤" htmlFor="asset-filter" className="w-56">
              <Input
                id="asset-filter"
                value={query}
                inputMode="numeric"
                placeholder="600519"
                autoComplete="off"
                spellCheck={false}
                leadingIcon={<Search className="size-4" aria-hidden="true" />}
                onChange={(event) => setQuery(event.target.value)}
              />
            </Field>
            <p className="text-caption text-text-muted">
              点一行看明细：最近 12 期财报 + 最近 20 条公告标题。
            </p>
          </div>

          {state === 'error' ? null : !index ? (
            <p className="text-body text-text-muted">正在读库…</p>
          ) : visible.length === 0 ? (
            <EmptyState
              compact
              title="当前筛选下没有标的"
              description="换个代码，或把「只看缺数据的」关掉。库是空的就先去提问，让它按权限补一次。"
            />
          ) : (
            <ul className="flex max-h-[520px] flex-col divide-y divide-border overflow-auto">
              {visible.map((row) => (
                <li key={row.symbol}>
                  <button
                    type="button"
                    onClick={() => setSelected(row.symbol)}
                    className={`flex w-full items-center justify-between gap-3 py-2 text-left transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                      selected === row.symbol ? 'bg-surface-2' : ''
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      <span className="font-mono text-body font-medium text-text">{row.symbol}</span>
                      <Badge tone={rowTone(row)}>{rowLabel(row)}</Badge>
                    </span>
                    <span className="flex shrink-0 items-center gap-4 text-caption text-text-secondary">
                      <span>财报 {row.financial_rows} 期</span>
                      <span>报告 {row.latest_report ?? '—'}</span>
                      <span>公告 {row.announcement_with_text} 条</span>
                      <span className="text-text-muted">宽表 {row.wide_as_of ?? '—'}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {selected ? (
        <Card>
          <CardHeader
            title={`${selected} 明细`}
            subtitle={detail ? `覆盖判定：${detail.coverage.complete ? '齐全' : `缺 ${detail.coverage.missing.join('、') || '—'}`}` : '正在读…'}
            actions={
              <span className="flex items-center gap-2">
                <Link
                  to={`/research/${selected}`}
                  className="text-caption text-accent underline-offset-2 hover:underline"
                >
                  <ExternalLink className="mr-1 inline size-3.5" aria-hidden="true" />
                  去问这只票
                </Link>
                <Link
                  to={`/knowledge/${marketOf(selected)}/${selected}`}
                  className="text-caption text-accent underline-offset-2 hover:underline"
                >
                  看档案
                </Link>
              </span>
            }
          />
          <CardBody className="flex flex-col gap-4">
            {detailState === 'loading' ? (
              <p className="text-body text-text-muted">正在读明细…</p>
            ) : detailState === 'error' ? (
              <p className="text-body leading-body text-text-secondary">{detailError}</p>
            ) : detail ? (
              <>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(detail.coverage.kinds).map(([kind, info]) => (
                    <span
                      key={kind}
                      className="rounded border border-border bg-surface-2 px-2 py-1 text-caption"
                    >
                      {kind}：
                      <span className={info.state === 'ready' ? 'text-ok' : 'text-warn'}>
                        {info.state}
                      </span>
                      {info.as_of ? <span className="text-text-muted">@{info.as_of}</span> : null}
                      {info.reason ? <span className="text-text-muted">·{info.reason}</span> : null}
                    </span>
                  ))}
                </div>

                <div className="flex flex-col gap-2">
                  <p className="text-body font-medium text-text">最近财报</p>
                  {detail.reports.length === 0 ? (
                    <p className="text-caption text-text-muted">库里没有这只票的财报行。</p>
                  ) : (
                    <ul className="flex flex-col divide-y divide-border">
                      {detail.reports.map((report) => (
                        <li
                          key={`${report.report_date}-${report.report_type}`}
                          className="flex items-center justify-between gap-3 py-1.5 text-caption"
                        >
                          <span className="font-mono text-text">{report.report_date}</span>
                          <span className="text-text-muted">{report.report_type}</span>
                          <span className="text-text-secondary">营收 {formatAmount(report.revenue)}</span>
                          <span className="text-text-secondary">
                            净利 {formatAmount(report.net_profit)}
                          </span>
                          {report.quality_status ? (
                            <Badge tone={report.quality_status === 'suspicious' ? 'warn' : 'ok'}>
                              {QUALITY_LABEL[report.quality_status] ?? report.quality_status}
                            </Badge>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="flex flex-col gap-2 border-t border-border pt-3">
                  <p className="text-body font-medium text-text">最近公告</p>
                  {detail.announcements.length === 0 ? (
                    <p className="text-caption text-text-muted">
                      库里没有这只票的公告行（正文为空的不计入）。
                    </p>
                  ) : (
                    <ul className="flex flex-col divide-y divide-border">
                      {detail.announcements.map((item) => (
                        <li
                          key={`${item.ann_date}-${item.title}`}
                          className="flex items-start justify-between gap-3 py-1.5"
                        >
                          <span className="min-w-0 flex-1 truncate text-caption text-text">
                            {item.title || '(无标题)'}
                          </span>
                          <span className="shrink-0 text-caption text-text-muted">
                            {item.ann_date ?? '—'} · {item.content_chars} 字
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <p className="flex items-center gap-1 border-t border-border pt-3 text-caption text-text-muted">
                  <Database className="size-3.5" aria-hidden="true" />
                  这些行来自 FinanceCoffer 的 Postgres（dwd / dws 两库表），不是前端拼的。
                </p>
              </>
            ) : null}
          </CardBody>
        </Card>
      ) : null}
    </div>
  )
}
