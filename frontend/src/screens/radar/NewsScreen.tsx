import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, ExternalLink, Newspaper, RefreshCw, Search } from 'lucide-react'

import {
  isRadarEndpointMissing,
  sourceLabel,
  type ExtractedEvent,
  type NewsItem,
  type NewsStreamPayload,
} from '../../api/radar'
import { DemoStateBadge, OfflineBanner, PageHeader, ScreenSkeleton } from '../../components/PageShell'
import {
  Badge,
  Banner,
  Button,
  Card,
  CardHeader,
  Checkbox,
  EmptyState,
  ErrorBlock,
  Field,
  FreshnessBadge,
  Input,
  SourceStatusBadge,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { formatTime, localDateIso, MISSING } from '../../lib/format'
import { DEMO_NEWS_DATE } from './demo'
import { useDemoEnabled, useNewsView } from './useRadarView'
import { withParams } from '../industry/links'

/**
 * P-R10-01 资讯雷达（`/news`）。
 *
 * 三栏：来源筛选 | 时间流 | 详情 + 原文链接。资讯只读，不做二次加工；
 * 「已抽取事件」是后端规则抽取的标记（SPEC §3 的 `events[].item_id`），
 * 页面只做关联展示，不在这里判断事件。
 *
 * 代码 / 行业过滤作用于当前窗口（默认最多 60 条）：行业优先用 `industries`
 * 标签，缺标签时退化为标题 / 摘要关键词匹配（缺口 2），并在页脚写明。
 */

function splitTerms(value: string): string[] {
  return value
    .split(/[\s,，、]+/)
    .map((term) => term.trim())
    .filter(Boolean)
}

function matchesCodes(item: NewsItem, terms: string[]): boolean {
  if (terms.length === 0) return true
  return item.related_codes.some((code) => terms.some((term) => code.includes(term)))
}

function matchesIndustry(item: NewsItem, term: string): boolean {
  if (!term) return true
  if (item.industries.length > 0) return item.industries.some((industry) => industry.includes(term))
  return `${item.title}${item.summary}`.includes(term)
}

function eventLabel(events: ExtractedEvent[]): string {
  const labels = [...new Set(events.map((event) => event.label || event.type))]
  return labels.join('、')
}

function SourceColumn({
  payload,
  selected,
  onToggle,
  onClear,
  date,
  onDate,
  untilValue,
  onUntil,
  code,
  onCode,
  onCodeCommit,
  industry,
  onIndustry,
  onIndustryCommit,
  windowCount,
  visibleCount,
}: {
  payload: NewsStreamPayload
  selected: Set<string>
  onToggle: (dataset: string, next: boolean) => void
  onClear: () => void
  date: string
  onDate: (next: string) => void
  untilValue: string
  onUntil: (next: string) => void
  code: string
  onCode: (next: string) => void
  onCodeCommit: () => void
  industry: string
  onIndustry: (next: string) => void
  onIndustryCommit: () => void
  windowCount: number
  visibleCount: number
}) {
  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader
        title="来源筛选"
        subtitle={`${visibleCount} / ${windowCount} 条`}
        actions={
          selected.size > 0 || code || industry ? (
            <Button variant="ghost" onClick={onClear}>
              清空
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-4 p-4">
        <div className="grid grid-cols-1 gap-3">
          <Field label="起始日" htmlFor="news-date" hint="窗口左端">
            <Input id="news-date" type="date" value={date} onChange={(event) => onDate(event.target.value)} />
          </Field>
          <Field label="截至（可选）" htmlFor="news-until" hint="留空只看当天；最长 31 天">
            <Input
              id="news-until"
              type="date"
              value={untilValue}
              min={date}
              onChange={(event) => onUntil(event.target.value)}
            />
          </Field>
          <Field label="代码" htmlFor="news-code" hint="600519，多个用逗号分隔">
            <Input
              id="news-code"
              value={code}
              placeholder="600519"
              leadingIcon={<Search className="size-3.5" aria-hidden="true" />}
              onChange={(event) => onCode(event.target.value)}
              onBlur={onCodeCommit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onCodeCommit()
              }}
            />
          </Field>
          <Field label="行业" htmlFor="news-industry" hint="按行业标签，缺标签时匹配标题 / 摘要">
            <Input
              id="news-industry"
              value={industry}
              placeholder="食品饮料"
              onChange={(event) => onIndustry(event.target.value)}
              onBlur={onIndustryCommit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onIndustryCommit()
              }}
            />
          </Field>
        </div>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-caption text-text-secondary">资讯栏目</legend>
          {payload.sources_status.length === 0 ? (
            <p className="text-caption text-text-muted">窗口内没有栏目账本。</p>
          ) : (
            payload.sources_status.map((row) => (
              <div key={row.dataset} className="flex min-w-0 flex-col gap-1">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <Checkbox
                    checked={selected.size === 0 || selected.has(row.dataset)}
                    onChange={(next) => onToggle(row.dataset, next)}
                  >
                    {sourceLabel(row.dataset)}
                  </Checkbox>
                  <span className="ldst-num shrink-0 text-caption text-text-secondary">{row.items}</span>
                </div>
                {/* 降级与新鲜度是两条独立事实：计数不再被降级吞掉，档位不再只靠颜色 */}
                <span
                  className="flex min-w-0 flex-wrap items-center gap-1"
                  title={
                    [
                      row.detail || row.source_detail,
                      `新鲜度 ${row.freshness || MISSING}`,
                      `来源 ${row.source || MISSING}`,
                    ]
                      .filter(Boolean)
                      .join('\n')
                  }
                >
                  <FreshnessBadge freshness={row.freshness} asOf={payload.data_as_of} />
                  {(row.status !== 'ok' || row.degraded) && <Badge tone="warn">降级</Badge>}
                </span>
              </div>
            ))
          )}
        </fieldset>

        <p className="text-caption leading-body text-text-muted">
          无可选项时表示「全部栏目」。过滤只作用于当前窗口，不改变后端取数口径。
        </p>
      </div>
    </Card>
  )
}

function TimelineColumn({
  items,
  eventsByItem,
  selectedId,
  onSelect,
}: {
  items: NewsItem[]
  eventsByItem: Map<string, ExtractedEvent[]>
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader title="时间流" subtitle={`${items.length} 条 · 按发布时间倒序`} />
      <div className="min-h-0 flex-1 overflow-auto">
        {items.length === 0 ? (
          <div className="p-4">
            <EmptyState compact title="没有匹配的资讯" description="换个代码 / 行业，或清空筛选。" />
          </div>
        ) : (
          <ul className="flex flex-col">
            {items.map((item) => {
              const events = eventsByItem.get(item.id) ?? []
              const active = item.id === selectedId
              return (
                <li key={item.id} className="border-b border-border last:border-b-0">
                  <button
                    type="button"
                    aria-current={active ? 'true' : undefined}
                    onClick={() => onSelect(item.id)}
                    className={cn(
                      'flex w-full flex-col gap-1 px-3 py-2 text-left transition-colors duration-fast ease-ldst hover:bg-surface-3',
                      active && 'bg-surface-2',
                    )}
                  >
                    <span className="flex flex-wrap items-center gap-2 text-caption text-text-muted">
                      <span className="ldst-num">{formatTime(item.published_at)}</span>
                      <span>{sourceLabel(item.source)}</span>
                      {events.length > 0 && (
                        <Badge tone="info" title={eventLabel(events)}>
                          已抽取事件
                        </Badge>
                      )}
                    </span>
                    <span
                      className={cn(
                        'truncate text-body leading-body',
                        active ? 'font-medium text-text' : 'text-text',
                      )}
                      title={item.title}
                    >
                      {item.title}
                    </span>
                    {item.related_codes.length > 0 && (
                      <span
                        className="ldst-num block truncate text-caption leading-body text-text-secondary"
                        title={item.related_codes.join(' / ')}
                      >
                        {item.related_codes.join(' / ')}
                      </span>
                    )}
                    {item.industries.length > 0 && (
                      <span className="flex flex-wrap gap-1">
                        {item.industries.map((industry) => (
                          <span
                            key={industry}
                            className="rounded-xs border border-border px-1 text-caption text-text-secondary"
                          >
                            {industry}
                          </span>
                        ))}
                      </span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </Card>
  )
}

function DetailColumn({
  item,
  events,
  state,
}: {
  item: NewsItem | null
  events: ExtractedEvent[]
  state: string | null
}) {
  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader title="详情" subtitle={item ? sourceLabel(item.source) : '未选择'} />
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {!item ? (
          <EmptyState compact title="选择一条资讯" description="左侧时间流点选后，正文、证据与原文链接显示在这里。" />
        ) : (
          <article className="flex flex-col gap-3">
            <h2 className="text-subtitle font-semibold leading-tight text-text">{item.title}</h2>
            <div className="flex flex-wrap items-center gap-2 text-caption text-text-muted">
              <span className="ldst-num">{formatTime(item.published_at)}</span>
              <span>{sourceLabel(item.source)}</span>
              {item.related_codes.map((code) => (
                <span key={code} className="ldst-num rounded-xs border border-border px-1 text-text-secondary">
                  {code}
                </span>
              ))}
              {item.industries.map((industry) => (
                <span key={industry} className="rounded-xs border border-border px-1 text-text-secondary">
                  {industry}
                </span>
              ))}
            </div>

            <p className="whitespace-pre-line text-body leading-body text-text">
              {item.summary || '这条资讯没有摘要，正文请见原文。'}
            </p>

            {events.length > 0 && (
              <section className="flex flex-col gap-2 rounded border border-border bg-surface-2 p-3">
                <h3 className="text-caption font-medium text-text-secondary">已抽取事件</h3>
                {events.map((event) => (
                  <div key={event.id} className="flex flex-col gap-1">
                    <p className="flex flex-wrap items-center gap-2 text-body text-text">
                      <Badge tone="info">{event.label || event.type}</Badge>
                      <span className="min-w-0 flex-1 truncate" title={event.title}>
                        {event.title}
                      </span>
                    </p>
                    <p
                      className="line-clamp-2 text-caption leading-body text-text-secondary"
                      title={event.evidence.snippet || MISSING}
                    >
                      证据：{event.evidence.snippet || MISSING}
                    </p>
                    <p className="text-caption text-text-muted">
                      来源 {event.evidence.dataset || MISSING} · 日期依据 {event.date_basis || MISSING} · 抽取器{' '}
                      <code className="ldst-num">{event.extractor || MISSING}</code>
                    </p>
                  </div>
                ))}
                <p className="text-caption text-text-muted">事件由后端规则抽取（DEC-006），页面不二次判定。</p>
              </section>
            )}

            {item.origins.length > 0 && (
              <section className="flex flex-col gap-1">
                <h3 className="text-caption font-medium text-text-secondary">合并来源</h3>
                <ul className="flex flex-col gap-1">
                  {item.origins.map((origin, index) => (
                    <li key={`${origin.dataset}-${index}`} className="text-caption leading-body">
                      <span className="text-text-muted">{sourceLabel(origin.dataset)}：</span>
                      {origin.url ? (
                        <a
                          href={origin.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-accent underline underline-offset-2"
                        >
                          {origin.title || origin.url}
                        </a>
                      ) : (
                        <span className="text-text">{origin.title || MISSING}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <div className="mt-auto flex flex-col gap-2 border-t border-border pt-3">
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex h-control items-center justify-center gap-1 rounded bg-accent-solid px-3 text-body font-medium text-accent-contrast transition-colors duration-fast ease-ldst hover:bg-accent-solid-hover"
                >
                  原文链接
                  <ExternalLink className="size-3.5" aria-hidden="true" />
                </a>
              ) : (
                <p className="text-caption text-danger">这条资讯没有原文链接（抽取跳过项），不以摘要冒充原文。</p>
              )}
              <p className="break-all text-caption text-text-muted">
                id <code className="ldst-num">{item.id}</code>
                {state === 'partial' && <span className="ml-1">· 部分栏目降级</span>}
              </p>
            </div>
          </article>
        )}
      </div>
    </Card>
  )
}

export default function NewsScreen() {
  const [params, setParams] = useSearchParams()
  const demo = useDemoEnabled()

  const date = params.get('date') ?? (demo ? DEMO_NEWS_DATE : localDateIso())
  const until = params.get('until')
  const sourceParam = params.get('source') ?? ''
  const codeParam = params.get('code') ?? ''
  const industryParam = params.get('industry') ?? ''

  const [codeText, setCodeText] = useState(codeParam)
  const [industryText, setIndustryText] = useState(industryParam)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const view = useNewsView({ date, until, codes: [] })
  const { forced, state, payload, error, reload } = view

  const selected = useMemo(() => new Set(sourceParam.split(',').filter(Boolean)), [sourceParam])
  const codeTerms = useMemo(() => splitTerms(codeParam), [codeParam])
  const industryTerm = industryParam.trim()

  const visibleItems = useMemo(() => {
    if (!payload) return []
    return payload.items
      .filter((item) => selected.size === 0 || selected.has(item.source))
      .filter((item) => matchesCodes(item, codeTerms))
      .filter((item) => matchesIndustry(item, industryTerm))
      .slice()
      .sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))
  }, [payload, selected, codeTerms, industryTerm])

  const eventsByItem = useMemo(() => {
    const map = new Map<string, ExtractedEvent[]>()
    for (const event of payload?.events ?? []) {
      if (!event.item_id) continue
      const bucket = map.get(event.item_id) ?? []
      bucket.push(event)
      map.set(event.item_id, bucket)
    }
    return map
  }, [payload])

  const activeItem =
    visibleItems.find((item) => item.id === selectedId) ?? visibleItems[0] ?? null
  const activeEvents = activeItem ? (eventsByItem.get(activeItem.id) ?? []) : []

  const header = (
    <PageHeader
      title="资讯雷达"
      subtitle={`窗口 ${payload?.window.start ?? date} → ${payload?.window.end ?? until ?? date} · 共 ${
        payload?.items.length ?? 0
      } 条 · 已抽取事件 ${payload?.events.length ?? 0}`}
      badge={
        <div className="flex flex-wrap items-center gap-2">
          <DemoStateBadge state={forced} />
          {payload && <SourceStatusBadge status={payload.data_source_status} detail={payload.reason} />}
        </div>
      }
      actions={
        <Button
          variant="primary"
          onClick={reload}
          disabled={state === 'loading'}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新
        </Button>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ScreenSkeleton metrics={3} rows={7} />
      </div>
    )
  }

  if (state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={reload} />
        <Card>
          <EmptyState
            icon={<Newspaper aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入 /api/news；未连接时不展示任何资讯。"
            action={<Button variant="secondary" onClick={reload}>重新检查</Button>}
          />
        </Card>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock
          variant="banner"
          title="资讯流取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty' || !payload) {
    const endpointMissing = payload === null && isRadarEndpointMissing(error)
    return (
      <div className="flex flex-col gap-5">
        {header}
        <Card>
          <EmptyState
            icon={<Database aria-hidden="true" />}
            title={endpointMissing ? '接口未接入' : '窗口内没有资讯'}
            description={
              endpointMissing ? (
                <>
                  页面已按 <code className="ldst-num">GET /api/news</code> 契约实现；后端提供该只读接口后自动接入，
                  当前不伪造任何资讯行。
                </>
              ) : (
                <>
                  {payload?.reason ?? '本机缓存里没有这个窗口的资讯。'}
                  。先运行 <code className="ldst-num">ldst news</code> 抓取并入库，再点「刷新」。
                </>
              )
            }
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button variant="secondary" onClick={reload}>刷新</Button>
                {!demo && (
                  <a
                    href={withParams('/news', { demo: '1', state: forced })}
                    className="inline-flex h-control items-center rounded border border-border-strong bg-surface px-3 text-body text-text transition-colors duration-fast ease-ldst hover:bg-surface-3"
                  >
                    查看演示数据
                  </a>
                )}
              </div>
            }
          />
        </Card>
      </div>
    )
  }

  const failed = payload.sources_status.filter((row) => row.status !== 'ok' || row.degraded)

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分资讯栏目降级">
          {failed.length > 0
            ? failed.map((row) => `${sourceLabel(row.dataset)}：${row.detail || '已降级'}`).join('；')
            : (payload.reason ?? '部分栏目不可用')}
          。其余栏目照常展示，缺失部分不补造。
        </Banner>
      )}

      {payload.extraction.suppressed_by_negation > 0 && (
        <Banner tone="info" title="有事件被否定语境抑制">
          {payload.extraction.suppressed_by_negation} 条候选事件因否定语境未抽出（规则 v1）；页面不展示未成立的事件。
        </Banner>
      )}

      <div className="grid grid-cols-[184px_minmax(0,1fr)_340px] items-start gap-4 desk:grid-cols-[200px_minmax(0,1fr)_400px]">
        <SourceColumn
          payload={payload}
          selected={selected}
          onToggle={(dataset, next) => {
            const base = selected.size === 0 ? payload.sources_status.map((row) => row.dataset) : [...selected]
            const updated = next ? [...new Set([...base, dataset])] : base.filter((key) => key !== dataset)
            const all = payload.sources_status.every((row) => updated.includes(row.dataset))
            setParam('source', all || updated.length === 0 ? null : updated.join(','))
            setSelectedId(null)
          }}
          onClear={() => {
            const next = new URLSearchParams(params)
            next.delete('source')
            next.delete('code')
            next.delete('industry')
            setParams(next, { replace: true })
            setCodeText('')
            setIndustryText('')
            setSelectedId(null)
          }}
          date={date}
          onDate={(next) => setParam('date', next || null)}
          untilValue={until ?? ''}
          onUntil={(next) => setParam('until', next || null)}
          code={codeText}
          onCode={setCodeText}
          onCodeCommit={() => setParam('code', codeText.trim() || null)}
          industry={industryText}
          onIndustry={setIndustryText}
          onIndustryCommit={() => setParam('industry', industryText.trim() || null)}
          windowCount={payload.items.length}
          visibleCount={visibleItems.length}
        />

        <TimelineColumn
          items={visibleItems}
          eventsByItem={eventsByItem}
          selectedId={activeItem?.id ?? null}
          onSelect={setSelectedId}
        />

        <DetailColumn item={activeItem} events={activeEvents} state={state} />
      </div>

      <p className="text-caption leading-body text-text-muted">
        口径：资讯只读自本机缓存（R6 `ldst news`），标题与摘要保持原文；「已抽取事件」为后端规则抽取结果。
        代码 / 行业过滤作用于当前窗口（最多 60 条）
        {payload.items.some((item) => item.industries.length === 0) && '；缺行业标签的条目按标题 / 摘要关键词匹配'}。
        演示源固定 {DEMO_NEWS_DATE} 的样例，<code className="ldst-num">?variant=partial|empty</code> 切换降级程度。
      </p>
    </div>
  )
}
